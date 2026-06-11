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

from hireshire.matcher.scorer import MatchResult
from hireshire.models.job import Job, Location
from hireshire.tuner.config import load_tuner_config
from hireshire.tuner.assembler import assemble_resume, load_projects
from hireshire.tuner.evaluator import ResumeEvaluator, make_evaluator_backend
from hireshire.tuner.loader import load_shortlisted
from hireshire.tuner.optimizer import ResumeOptimizer, make_optimizer_backend
from hireshire.tuner.store import TuneStore

load_dotenv()

logger = logging.getLogger(__name__)
console = Console()

STATUS_STYLE = {
    "tuned": "[bold green]tuned[/bold green]",
    "skipped": "[yellow]skipped[/yellow]",
    "error": "[bold red]error[/bold red]",
}


class _NoopProgress:
    def update(self, *a, **kw): pass
    def advance(self, *a, **kw): pass
    def add_task(self, *a, **kw): return 0
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _make_standalone_job(jd_path: Path, title: str, company: str) -> Job:
    now = datetime.now(timezone.utc)
    job_id = re.sub(r"[^a-zA-Z0-9_-]", "_", jd_path.stem)[:64]
    return Job(
        source="manual",
        board_token=re.sub(r"[^a-zA-Z0-9_-]", "_", company.lower())[:32],
        job_id=job_id,
        title=title,
        location=Location(name="N/A"),
        absolute_url="https://placeholder.local",  # type: ignore[arg-type]
        updated_at=datetime.now(timezone.utc),
        scraped_at=datetime.now(timezone.utc),
        content_text=jd_path.read_text(encoding="utf-8"),
    )


async def _process_job(
    job: Job,
    store: TuneStore,
    evaluator: ResumeEvaluator,
    optimizer: ResumeOptimizer,
    resume_tex: str,
    projects: dict,
    template_path: str,
    settings,
    force: bool = False,
    quiet: bool = False,
) -> str:
    """Run the two-pass tune for one job. Returns 'tuned', 'skipped', or 'error'."""
    if not force and store.is_done(job.job_id):
        logger.info("Skipping already-tuned job %s", job.job_id)
        store.record_skip()
        return "skipped"

    # Pass 1: Recruiter evaluation (still uses full resume LaTeX for critique)
    critique = await evaluator.evaluate(job, resume_tex)
    if critique is None:
        store.record_error()
        return "error"

    if not critique.job_id:
        critique = critique.model_copy(update={"job_id": job.job_id})

    # Pass 2: Project selection + keyword injection → code-assembled LaTeX
    optimize_result = await optimizer.optimize(
        job=job,
        critique=critique,
        projects=projects,
        template_path=template_path,
    )
    if optimize_result is None:
        store.record_error()
        return "error"

    optimized_tex = optimize_result.tex
    selection = optimize_result.selection

    job_dir = store.save_job(
        job_id=job.job_id,
        job_description=job.content_text or "",
        critique=critique,
        optimized_tex=optimized_tex,
    )

    # Trim phase 1: drop least-relevant project (last in LLM-ordered list) while > 2 and overflowing
    active_project_ids = list(selection.selected_projects)
    pages = store.compile_pdf(job_dir)
    bullet_limits: dict[str, int] = {}

    while pages > 1 and len(active_project_ids) > 2:
        dropped = active_project_ids.pop()  # last = least relevant per LLM ordering
        logger.info("Trim: dropping least-relevant project %s", dropped)
        optimized_tex = assemble_resume(
            template_path=template_path,
            projects=projects,
            selected_project_ids=active_project_ids,
            selected_work_id=selection.selected_work,
            section_order=selection.section_order,
            keyword_adjustments=selection.keyword_adjustments,
            bullet_limits=bullet_limits,
        )
        store.update_tex(job_dir, optimized_tex)
        pages = store.compile_pdf(job_dir)

    # Trim phase 2: fall back to per-bullet removal if still overflowing
    if pages > 1:
        selected_ids = [
            pid for pid in (active_project_ids + [selection.selected_work])
            if pid in projects
        ]

        def _count(pid: str) -> int:
            return bullet_limits.get(pid, len(projects[pid].get("bullets", [])))

        # Drain longest entry first (by original bullet count), removing from the bottom one at a time
        sorted_ids = sorted(selected_ids, key=lambda pid: len(projects[pid].get("bullets", [])), reverse=True)

        for pid in sorted_ids:
            while _count(pid) > 0 and pages > 1:
                bullet_limits[pid] = _count(pid) - 1
                logger.info("Trim: removing last bullet from %s (now %d)", pid, bullet_limits[pid])

                optimized_tex = assemble_resume(
                    template_path=template_path,
                    projects=projects,
                    selected_project_ids=active_project_ids,
                    selected_work_id=selection.selected_work,
                    section_order=selection.section_order,
                    keyword_adjustments=selection.keyword_adjustments,
                    bullet_limits=bullet_limits,
                )
                store.update_tex(job_dir, optimized_tex)
                pages = store.compile_pdf(job_dir)
            if pages <= 1:
                break

    if pages > 1 and not quiet:
        console.print(f"[yellow]Warning: {job.job_id} is {pages} page(s) after trim[/yellow]")

    return "tuned"


async def main(
    in_queue: asyncio.Queue | None = None,
    out_queue: asyncio.Queue | None = None,
    quiet: bool = False,
) -> None:
    if not quiet:
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
        )
        logging.getLogger("hireshire").setLevel(logging.INFO)

    # Parse args — use empty list in queue mode to avoid consuming orchestrator's sys.argv
    parser = argparse.ArgumentParser(description="HireShire Tuner — two-pass resume optimizer")
    parser.add_argument("--run-id", help="Specific matches run ID to tune from (pipeline mode)")
    parser.add_argument("--job-id", help="Tune a single job only (pipeline mode)")
    parser.add_argument("--force", action="store_true", help="Re-tune already-processed jobs")
    parser.add_argument("--jd-file", metavar="PATH", help="Path to a plain-text job description (standalone mode)")
    parser.add_argument("--title", default="Job", help="Job title (standalone mode)")
    parser.add_argument("--company", default="Manual", help="Company name (standalone mode)")
    parser.add_argument("--resume-tex", metavar="PATH", help="Override resume LaTeX source path")
    args = parser.parse_args([] if in_queue is not None else None)

    config = load_tuner_config("config/tuner.yaml")
    settings = config.settings

    default_provider = os.environ.get("LLM_PROVIDER", "anthropic")
    eval_provider = settings.evaluator_provider or default_provider
    eval_model = settings.evaluator_model or settings.model
    opt_provider = settings.optimizer_provider or default_provider
    opt_model = settings.optimizer_model or settings.model

    if not quiet:
        mode_label = "[cyan]queue[/cyan]" if in_queue is not None else (
            "[cyan]standalone[/cyan]" if args.jd_file else "[cyan]pipeline[/cyan]"
        )
        console.print(f"[bold]HireShire Tuner[/bold] ({mode_label})")
        console.print(f"  Evaluator : [bold]{eval_provider}/{eval_model}[/bold]")
        console.print(f"  Optimizer : [bold]{opt_provider}/{opt_model}[/bold]")

    # --- Load LaTeX source (for evaluator) ---
    tex_path = Path(args.resume_tex) if args.resume_tex else Path(settings.resume_tex_path)
    if not tex_path.exists():
        if not quiet:
            console.print(
                f"[red]LaTeX source not found: {tex_path}\n"
                f"Provide it via --resume-tex or set resume_tex_path in config/tuner.yaml.[/red]"
            )
        return
    resume_tex = tex_path.read_text(encoding="utf-8")
    if not quiet:
        console.print(f"Resume (LaTeX) loaded: [green]{tex_path}[/green] ({len(resume_tex)} chars)")

    # --- Load template (for assembler) ---
    template_path = settings.resume_template_path
    if not Path(template_path).exists():
        if not quiet:
            console.print(f"[red]Resume template not found: {template_path}[/red]")
        return

    # --- Load projects bullets YAML (for optimizer/selector) ---
    bullets_path = Path(settings.projects_bullets_path)
    if bullets_path.exists():
        projects = load_projects(bullets_path)
        if not quiet:
            console.print(
                f"Projects bullets loaded: [green]{bullets_path}[/green] "
                f"({len(projects)} entries)"
            )
    else:
        projects = {}
        if not quiet:
            console.print(
                f"[yellow]Projects bullets file not found: {bullets_path} — "
                "optimizer will assemble with no projects.[/yellow]"
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

    # =========================================================
    # Queue mode: consume (MatchResult, Job) pairs from in_queue
    # =========================================================
    if in_queue is not None:
        source_run_id = "pipeline"
        try:
            while True:
                item = await in_queue.get()
                if item is None:
                    break
                match_result, job = item
                if isinstance(match_result, MatchResult):
                    source_run_id = match_result.source_run_id
                logger.info(
                    "Tuning job %s/%s (%s)",
                    job.board_token, job.job_id, job.title[:50],
                )
                status = await _process_job(
                    job=job,
                    store=store,
                    evaluator=evaluator,
                    optimizer=optimizer,
                    resume_tex=resume_tex,
                    projects=projects,
                    template_path=template_path,
                    settings=settings,
                    force=False,
                    quiet=quiet,
                )
                summary_rows.append((status, job.title, job.board_token))
                if out_queue is not None:
                    pdf_path = store.run_dir / job.job_id / "Udayan_Atreya_Resume.pdf"
                    await out_queue.put({
                        "job_id": job.job_id,
                        "title": job.title,
                        "company": job.board_token,
                        "job_url": str(match_result.absolute_url) if isinstance(match_result, MatchResult) else "",
                        "relevance_score": match_result.relevance_score if isinstance(match_result, MatchResult) else None,
                        "resume_tex": str(store.run_dir / job.job_id / "Udayan_Atreya_Resume.tex") if status == "tuned" else None,
                        "resume_pdf": str(pdf_path) if (status == "tuned" and pdf_path.exists()) else None,
                        "tuner_status": status,
                        "tuner_run_id": run_id,
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    })
                if settings.request_interval_s > 0:
                    await asyncio.sleep(settings.request_interval_s)
        except Exception:
            logger.exception("Tuner queue loop failed")
        finally:
            if out_queue is not None:
                await out_queue.put(None)
            store.save_manifest(
                started_at=started_at,
                source_run_id=source_run_id,
                model=f"{eval_provider}/{eval_model} + {opt_provider}/{opt_model}",
                provider=f"{eval_provider} + {opt_provider}",
                total_loaded=len(summary_rows),
            )
            tuned = sum(1 for s, _, _ in summary_rows if s == "tuned")
            logger.info(
                "Tuner done: %d tuned, %d skipped, %d error(s) → %s/%s/",
                tuned,
                sum(1 for s, _, _ in summary_rows if s == "skipped"),
                sum(1 for s, _, _ in summary_rows if s == "error"),
                settings.tuned_dir, run_id,
            )
        return

    # =========================================================
    # Standalone / pipeline-from-file mode (existing behaviour)
    # =========================================================
    standalone = args.jd_file is not None

    if standalone:
        jd_path = Path(args.jd_file)
        if not jd_path.exists():
            if not quiet:
                console.print(f"[red]Job description file not found: {jd_path}[/red]")
            return
        job = _make_standalone_job(jd_path, title=args.title, company=args.company)
        jobs = [(None, job)]
        source_run_id = "standalone"
        if not quiet:
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
            if not quiet:
                console.print(
                    "[yellow]No shortlisted jobs found. Run python matcher.py first, "
                    "or use --jd-file for standalone mode.[/yellow]"
                )
            return
        if args.job_id:
            raw_jobs = [(mr, j) for mr, j in raw_jobs if j.job_id == args.job_id]
            if not raw_jobs:
                if not quiet:
                    console.print(f"[red]Job ID '{args.job_id}' not found in shortlisted jobs.[/red]")
                return
        jobs = raw_jobs
        source_run_id = jobs[0][0].source_run_id
        if not quiet:
            console.print(
                f"Tuning [bold]{len(jobs)}[/bold] job(s) "
                f"from matches run [cyan]{source_run_id}[/cyan]\n"
            )

    prog_ctx = (
        Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
        )
        if not quiet
        else _NoopProgress()
    )

    with prog_ctx as progress:
        task_bar = progress.add_task("Tuning resumes...", total=len(jobs))

        for match_result, job in jobs:
            progress.update(task_bar, description=f"[cyan]{job.board_token}[/cyan] / {job.title[:45]}")

            status = await _process_job(
                job=job,
                store=store,
                evaluator=evaluator,
                optimizer=optimizer,
                resume_tex=resume_tex,
                projects=projects,
                template_path=template_path,
                settings=settings,
                force=args.force,
                quiet=quiet,
            )
            summary_rows.append((status, job.title, job.board_token))
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

    if not quiet:
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
