"""
Configure config/matcher.yaml and place resume.pdf in the project root, then run:
    python matcher.py
"""

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

from hireshire.matcher.config import load_matcher_config
from hireshire.matcher.loader import load_jobs
from hireshire.matcher.resume import extract_resume_text
from hireshire.matcher.scorer import JobScorer, MatchResult, make_backend
from hireshire.matcher.store import MatchStore
from hireshire.storage.json_store import RunStore

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)
console = Console()


async def main() -> None:
    config = load_matcher_config("config/matcher.yaml")
    settings = config.settings

    # Locate latest scraper run
    run_dir = RunStore.latest_run(Path(settings.runs_dir))
    if not run_dir:
        console.print("[red]No scraper runs found in data/runs/. Run python scraper.py first.[/red]")
        return
    run_id = run_dir.name

    console.print(f"[bold]HireShire Matcher[/bold] — scoring jobs from run [cyan]{run_id}[/cyan]")

    # Extract resume text
    try:
        resume_text = extract_resume_text(settings.resume_path)
        console.print(f"Resume loaded: [green]{settings.resume_path}[/green] ({len(resume_text)} chars)")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return

    # Load jobs
    jobs = load_jobs(run_dir)
    if not jobs:
        console.print("[yellow]No jobs found in the latest run. Run python scraper.py first.[/yellow]")
        return

    provider = os.environ.get("LLM_PROVIDER", "gemini")
    console.print(
        f"Scoring [bold]{len(jobs)}[/bold] jobs with [bold]{provider}/{settings.model}[/bold] "
        f"(threshold: {settings.threshold}/100)\n"
    )

    started_at = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(settings.concurrency)
    backend = make_backend(settings, sem)
    scorer = JobScorer(settings=settings, backend=backend)
    store = MatchStore(base_dir=Path(settings.matches_dir), run_id=run_id)
    results: list[MatchResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scoring jobs...", total=len(jobs))

        async def score_one(job):
            try:
                result = await scorer.score(job, resume_text, run_id)
            except Exception:
                logger.exception("Unexpected error scoring job %s/%s", job.board_token, job.job_id)
                result = MatchResult(
                    job_id=job.job_id,
                    board_token=job.board_token,
                    title=job.title,
                    location=job.location.name,
                    absolute_url=str(job.absolute_url),
                    relevance_score=0,
                    match_reasons=[],
                    disqualifiers=[],
                    recommend=False,
                    skipped=True,
                    skip_reason="unexpected_error",
                    scored_at=datetime.now(timezone.utc),
                    source_run_id=run_id,
                )
            finally:
                progress.advance(task)
            # Append to disk immediately so a crash doesn't lose completed work
            store.append_result(result)
            return result

        results = list(await asyncio.gather(*[score_one(j) for j in jobs]))

    shortlisted = [r for r in results if not r.skipped and r.relevance_score >= settings.threshold]
    rejected = [r for r in results if r.skipped or r.relevance_score < settings.threshold]

    # Sort shortlisted by score descending
    shortlisted.sort(key=lambda r: r.relevance_score, reverse=True)

    # Write final partitioned files and manifest
    store.finalise(shortlisted, rejected, started_at, settings.threshold, settings.model, len(jobs))

    # Print summary table
    console.print()
    if shortlisted:
        table = Table(title=f"Shortlisted Jobs (score >= {settings.threshold})", show_lines=True)
        table.add_column("Score", style="bold green", width=7)
        table.add_column("Title", style="bold")
        table.add_column("Company", style="cyan")
        table.add_column("Location")
        table.add_column("Recommend", width=10)

        for r in shortlisted:
            table.add_row(
                str(r.relevance_score),
                r.title,
                r.board_token,
                r.location,
                "[green]Yes[/green]" if r.recommend else "[yellow]Maybe[/yellow]",
            )
        console.print(table)
    else:
        console.print("[yellow]No jobs met the threshold. Try lowering it in config/matcher.yaml.[/yellow]")

    skipped_count = sum(1 for r in results if r.skipped)
    console.print(
        f"\n[bold]{len(shortlisted)} shortlisted[/bold], "
        f"{len(rejected) - skipped_count} rejected, "
        f"{skipped_count} skipped "
        f"→ [cyan]data/matches/{run_id}/[/cyan]"
    )


if __name__ == "__main__":
    asyncio.run(main())
