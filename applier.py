"""
Configure config/applier.yaml and run the matcher first, then:
    python applier.py                          # applies from latest matches run (dry_run from config)
    python applier.py --run-id <run_id>        # use a specific matches run
    python applier.py --dry-run                # override config — never submits
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from hireshire.applier.answerer import QuestionAnswerer
from hireshire.applier.config import load_applier_config
from hireshire.applier.filler import FormFiller
from hireshire.applier.loader import load_shortlisted
from hireshire.applier.store import AppliedStore, ApplyRecord
from hireshire.matcher.resume import extract_resume_text

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)
console = Console()

STATUS_STYLE = {
    "submitted": "[bold green]submitted[/bold green]",
    "dry_run": "[bold cyan]dry_run[/bold cyan]",
    "error": "[bold red]error[/bold red]",
    "skipped": "[yellow]skipped[/yellow]",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description="HireShire Auto-Applier (Phase 3)")
    parser.add_argument("--run-id", help="Specific matches run ID to apply from")
    parser.add_argument("--dry-run", action="store_true", help="Override config: never submit")
    args = parser.parse_args()

    config = load_applier_config("config/applier.yaml")
    settings = config.settings

    dry_run = settings.dry_run or args.dry_run

    console.print(
        f"[bold]HireShire Applier[/bold] — "
        f"{'[cyan]DRY RUN[/cyan] (no submissions)' if dry_run else '[bold red]LIVE MODE[/bold red] (will submit)'}"
    )

    # Load resume text for answer generation
    try:
        resume_text = extract_resume_text(settings.resume_path)
        console.print(f"Resume loaded: [green]{settings.resume_path}[/green] ({len(resume_text)} chars)")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return

    resume_path = Path(settings.resume_path)

    # Set up store and load jobs
    applied_dir = Path(settings.applied_dir)
    store = AppliedStore(applied_dir)
    screenshots_dir = applied_dir / "screenshots"

    jobs = load_shortlisted(
        matches_dir=Path(settings.matches_dir),
        runs_dir=Path(settings.runs_dir),
        store=store,
        run_id=args.run_id,
    )

    if not jobs:
        console.print("[yellow]No shortlisted jobs to apply to. Run python matcher.py first.[/yellow]")
        return

    provider = os.environ.get("LLM_PROVIDER", "openai")
    console.print(
        f"Applying to [bold]{len(jobs)}[/bold] job(s) "
        f"using [bold]{provider}/{settings.model}[/bold]\n"
    )

    answerer = QuestionAnswerer(
        model=settings.model,
        generate_cover_letter=settings.generate_cover_letter,
    )
    filler = FormFiller(
        model=settings.model,
        headless=settings.headless,
        max_steps=settings.max_steps,
        screenshots_dir=screenshots_dir,
    )

    records: list[ApplyRecord] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task_bar = progress.add_task("Applying...", total=len(jobs))

        for match_result, job in jobs:
            progress.update(task_bar, description=f"[cyan]{job.board_token}[/cyan] / {job.title[:40]}")

            record: ApplyRecord

            try:
                # Step 1: generate answers
                answers = await answerer.generate_answers(
                    job=job,
                    resume_text=resume_text,
                    first_name=settings.first_name,
                    last_name=settings.last_name,
                    email=settings.email,
                    phone=settings.phone,
                )

                # Step 2: fill and optionally submit
                result = await filler.fill(
                    url=str(match_result.absolute_url),
                    answers=answers,
                    resume_path=resume_path,
                    first_name=settings.first_name,
                    last_name=settings.last_name,
                    email=settings.email,
                    phone=settings.phone,
                    job_id=job.job_id,
                    dry_run=dry_run,
                )

                record = ApplyRecord(
                    job_id=job.job_id,
                    board_token=job.board_token,
                    title=job.title,
                    absolute_url=str(match_result.absolute_url),
                    applied_at=datetime.now(timezone.utc),
                    status=result["status"],
                    dry_run=dry_run,
                    screenshot=result.get("screenshot"),
                    error=result.get("error"),
                )

            except Exception as exc:
                logger.exception("Unexpected error for job %s/%s", job.board_token, job.job_id)
                record = ApplyRecord(
                    job_id=job.job_id,
                    board_token=job.board_token,
                    title=job.title,
                    absolute_url=str(match_result.absolute_url),
                    applied_at=datetime.now(timezone.utc),
                    status="error",
                    dry_run=dry_run,
                    error=str(exc),
                )

            store.append(record)
            records.append(record)
            progress.advance(task_bar)

            if settings.inter_job_delay_s > 0 and (match_result, job) != jobs[-1]:
                await asyncio.sleep(settings.inter_job_delay_s)

    # Summary table
    console.print()
    table = Table(title="Application Summary", show_lines=True)
    table.add_column("Status", width=12)
    table.add_column("Title", style="bold")
    table.add_column("Company", style="cyan")
    table.add_column("Screenshot")

    for r in records:
        table.add_row(
            STATUS_STYLE.get(r.status, r.status),
            r.title,
            r.board_token,
            r.screenshot or "—",
        )
    console.print(table)

    submitted = sum(1 for r in records if r.status == "submitted")
    dry_runs = sum(1 for r in records if r.status == "dry_run")
    errors = sum(1 for r in records if r.status == "error")
    console.print(
        f"\n[bold]{submitted} submitted[/bold], "
        f"{dry_runs} dry-run, {errors} error(s) "
        f"→ [cyan]{settings.applied_dir}/applied.json[/cyan]"
    )
    if dry_run:
        console.print(
            "[dim]Set dry_run: false in config/applier.yaml to submit real applications.[/dim]"
        )


if __name__ == "__main__":
    asyncio.run(main())
