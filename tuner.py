"""
Pipeline mode (reads from latest matcher run):
    python tuner.py
    python tuner.py --run-id <run_id>        # use a specific matches run
    python tuner.py --job-id <job_id>        # tune a single job only
    python tuner.py --force                  # re-tune already-processed jobs

Standalone mode (no pipeline required):
    python tuner.py --jd-file path/to/job.txt
    python tuner.py --jd-file path/to/job.txt --resume-tex path/to/resume.tex
    python tuner.py --jd-file path/to/job.txt --title "Senior Engineer" --company "Acme"
"""

import argparse
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from hireshire.models.job import Job, Location
from hireshire.tuner.config import load_tuner_config
from hireshire.tuner.evaluator import ResumeEvaluator, make_evaluator_backend
from hireshire.tuner.loader import load_shortlisted
from hireshire.tuner.optimizer import ResumeOptimizer, make_optimizer_backend
from hireshire.tuner.store import TuneStore

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
)
logging.getLogger("hireshire").setLevel(logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

STATUS_STYLE = {
    "tuned": "[bold green]tuned[/bold green]",
    "skipped": "[yellow]skipped[/yellow]",
    "error": "[bold red]error[/bold red]",
}


def _make_standalone_job(jd_path: Path, title: str, company: str) -> Job:
    """Build a synthetic Job from a plain-text job description file."""
    now = datetime.now(timezone.utc)
    # Derive a filesystem-safe job_id from the filename
    job_id = re.sub(r"[^a-zA-Z0-9_-]", "_", jd_path.stem)[:64]
    return Job(
        source="manual",
        board_token=re.sub(r"[^a-zA-Z0-9_-]", "_", company.lower())[:32],
        job_id=job_id,
        title=title,
        location=Location(name="N/A"),
        absolute_url="https://placeholder.local",  # type: ignore[arg-type]
        updated_at=now,
        scraped_at=now,
        content_text=jd_path.read_text(encoding="utf-8"),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="HireShire Tuner — two-pass resume optimizer")
    # Pipeline mode args
    parser.add_argument("--run-id", help="Specific matches run ID to tune from (pipeline mode)")
    parser.add_argument("--job-id", help="Tune a single job only (pipeline mode)")
    parser.add_argument("--force", action="store_true", help="Re-tune already-processed jobs")
    # Standalone mode args
    parser.add_argument("--jd-file", metavar="PATH", help="Path to a plain-text job description (enables standalone mode)")
    parser.add_argument("--title", default="Job", help="Job title (standalone mode, default: 'Job')")
    parser.add_argument("--company", default="Manual", help="Company name (standalone mode, default: 'Manual')")
    # Shared override
    parser.add_argument("--resume-tex", metavar="PATH", help="Override resume LaTeX source path")
    args = parser.parse_args()

    standalone = args.jd_file is not None

    config = load_tuner_config("config/tuner.yaml")
    settings = config.settings

    default_provider = os.environ.get("LLM_PROVIDER", "anthropic")
    eval_provider = settings.evaluator_provider or default_provider
    eval_model = settings.evaluator_model or settings.model
    opt_provider = settings.optimizer_provider or default_provider
    opt_model = settings.optimizer_model or settings.model

    mode_label = "[cyan]standalone[/cyan]" if standalone else "[cyan]pipeline[/cyan]"
    console.print(f"[bold]HireShire Tuner[/bold] ({mode_label})")
    console.print(f"  Evaluator : [bold]{eval_provider}/{eval_model}[/bold]")
    console.print(f"  Optimizer : [bold]{opt_provider}/{opt_model}[/bold]")

    # --- Load LaTeX source (used by both passes) ---
    tex_path = Path(args.resume_tex) if args.resume_tex else Path(settings.resume_tex_path)
    if not tex_path.exists():
        console.print(
            f"[red]LaTeX source not found: {tex_path}\n"
            f"Provide it via --resume-tex or set resume_tex_path in config/tuner.yaml.[/red]"
        )
        return
    resume_tex = tex_path.read_text(encoding="utf-8")
    console.print(
        f"Resume (LaTeX) loaded: [green]{tex_path}[/green] ({len(resume_tex)} chars)"
    )

    # --- Load projects file (optional) ---
    projects_path = Path(settings.projects_path)
    if projects_path.exists():
        projects_text = projects_path.read_text(encoding="utf-8")
        console.print(
            f"Projects file loaded: [green]{settings.projects_path}[/green] "
            f"({len(projects_text)} chars)"
        )
    else:
        projects_text = ""
        console.print(
            f"[yellow]Projects file not found: {settings.projects_path} — "
            "optimizer will work without additional projects pool.[/yellow]"
        )

    # --- Build job list ---
    if standalone:
        jd_path = Path(args.jd_file)
        if not jd_path.exists():
            console.print(f"[red]Job description file not found: {jd_path}[/red]")
            return
        job = _make_standalone_job(jd_path, title=args.title, company=args.company)
        # Wrap in same (match_result, job) tuple shape the loop expects; match_result unused
        jobs = [(None, job)]
        source_run_id = "standalone"
        console.print(
            f"Standalone job: [bold]{job.title}[/bold] @ [cyan]{job.board_token}[/cyan] "
            f"({len(job.content_text or '')} chars)\n"
        )
    else:
        raw_jobs = load_shortlisted(
            matches_dir=Path(settings.matches_dir),
            runs_dir=Path(settings.runs_dir),
            run_id=args.run_id,
        )
        if not raw_jobs:
            console.print(
                "[yellow]No shortlisted jobs found. Run python matcher.py first, "
                "or use --jd-file for standalone mode.[/yellow]"
            )
            return
        if args.job_id:
            raw_jobs = [(mr, j) for mr, j in raw_jobs if j.job_id == args.job_id]
            if not raw_jobs:
                console.print(f"[red]Job ID '{args.job_id}' not found in shortlisted jobs.[/red]")
                return
        jobs = raw_jobs
        source_run_id = jobs[0][0].source_run_id
        console.print(
            f"Tuning [bold]{len(jobs)}[/bold] job(s) "
            f"from matches run [cyan]{source_run_id}[/cyan]\n"
        )

    # --- Set up store and LLM backends ---
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    store = TuneStore(base_dir=Path(settings.tuned_dir), run_id=run_id)
    started_at = datetime.now(timezone.utc)

    sem = asyncio.Semaphore(1)
    evaluator = ResumeEvaluator(
        settings=settings,
        backend=make_evaluator_backend(settings, sem, provider=eval_provider, model=eval_model),
    )
    optimizer = ResumeOptimizer(
        settings=settings,
        backend=make_optimizer_backend(settings, sem, provider=opt_provider, model=opt_model),
    )

    summary_rows: list[tuple[str, str, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task_bar = progress.add_task("Tuning resumes...", total=len(jobs))

        for match_result, job in jobs:
            progress.update(
                task_bar,
                description=f"[cyan]{job.board_token}[/cyan] / {job.title[:45]}",
            )

            if not args.force and store.is_done(job.job_id):
                logger.info("Skipping already-tuned job %s", job.job_id)
                store.record_skip()
                summary_rows.append(("skipped", job.title, job.board_token))
                progress.advance(task_bar)
                continue

            # Pass 1: Recruiter evaluation
            critique = await evaluator.evaluate(job, resume_tex)
            if critique is None:
                store.record_error()
                summary_rows.append(("error", job.title, job.board_token))
                progress.advance(task_bar)
                continue

            if not critique.job_id:
                critique = critique.model_copy(update={"job_id": job.job_id})

            # Pass 2: Resume optimization
            optimized_tex = await optimizer.optimize(
                job=job,
                resume_tex=resume_tex,
                critique=critique,
                projects_text=projects_text,
            )
            if optimized_tex is None:
                store.record_error()
                summary_rows.append(("error", job.title, job.board_token))
                progress.advance(task_bar)
                continue

            job_dir = store.save_job(
                job_id=job.job_id,
                job_description=job.content_text or "",
                critique=critique,
                optimized_tex=optimized_tex,
            )

            # Compile and trim loop — retry up to 2 times if > 1 page
            MAX_TRIM_RETRIES = 2
            pages = store.compile_pdf(job_dir)
            for attempt in range(MAX_TRIM_RETRIES):
                if pages <= 1:
                    break
                logger.info(
                    "Resume for %s is %d pages; trimming (attempt %d/%d)",
                    job.job_id, pages, attempt + 1, MAX_TRIM_RETRIES,
                )
                trimmed = await optimizer.trim_to_one_page(optimized_tex, pages)
                if trimmed is None:
                    break
                optimized_tex = trimmed
                store.update_tex(job_dir, optimized_tex)
                pages = store.compile_pdf(job_dir)
            if pages > 1:
                console.print(
                    f"[yellow]Warning: {job.job_id} is {pages} page(s) after "
                    f"{MAX_TRIM_RETRIES} trim attempt(s)[/yellow]"
                )

            summary_rows.append(("tuned", job.title, job.board_token))
            progress.advance(task_bar)

            if settings.request_interval_s > 0 and (match_result, job) != jobs[-1]:
                await asyncio.sleep(settings.request_interval_s)

    store.save_manifest(
        started_at=started_at,
        source_run_id=source_run_id,
        model=f"{eval_provider}/{eval_model} + {opt_provider}/{opt_model}",
        provider=f"{eval_provider} + {opt_provider}",
        total_loaded=len(jobs),
    )

    console.print()
    table = Table(title="Tuning Summary", show_lines=True)
    table.add_column("Status", width=10)
    table.add_column("Title", style="bold")
    table.add_column("Company", style="cyan")

    for status, title, company in summary_rows:
        table.add_row(STATUS_STYLE.get(status, status), title, company)
    console.print(table)

    tuned = sum(1 for s, _, _ in summary_rows if s == "tuned")
    skipped = sum(1 for s, _, _ in summary_rows if s == "skipped")
    errors = sum(1 for s, _, _ in summary_rows if s == "error")
    console.print(
        f"\n[bold]{tuned} tuned[/bold], {skipped} skipped, {errors} error(s) "
        f"→ [cyan]{settings.tuned_dir}/{run_id}/[/cyan]"
    )


if __name__ == "__main__":
    asyncio.run(main())
