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
from hireshire.matcher.seen import SeenStore
from hireshire.matcher.store import MatchStore
from hireshire.matcher.title_filter import apply_title_filter
from hireshire.storage.json_store import RunStore

load_dotenv()

logger = logging.getLogger(__name__)
console = Console()


class _NoopProgress:
    def update(self, *a, **kw): pass
    def advance(self, *a, **kw): pass
    def add_task(self, *a, **kw): return 0
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _passthrough_result(job, run_id: str) -> MatchResult:
    return MatchResult(
        job_id=job.job_id,
        board_token=job.board_token,
        title=job.title,
        location=job.location.name,
        absolute_url=str(job.absolute_url),
        relevance_score=100,
        match_reasons=["LLM scoring skipped"],
        disqualifiers=[],
        recommend=True,
        skip_reason="llm_skipped",
        scored_at=datetime.now(timezone.utc),
        source_run_id=run_id,
    )


async def main(
    in_queue: asyncio.Queue | None = None,
    out_queue: asyncio.Queue | None = None,
    quiet: bool = False,
    run_id: str | None = None,
    skip_llm: bool = False,
) -> None:
    if not quiet:
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
        )

    config = load_matcher_config("config/matcher.yaml")
    settings = config.settings
    effective_skip_llm = skip_llm or settings.skip_llm

    # --- Determine run_id ---
    run_dir = None
    if in_queue is not None:
        if run_id is None:
            raise ValueError("run_id is required when using in_queue (orchestrator mode)")
    else:
        run_dir = RunStore.latest_run(Path(settings.runs_dir))
        if not run_dir:
            if not quiet:
                console.print("[red]No scraper runs found in data/scraped/. Run python scraper.py first.[/red]")
            return
        run_id = run_dir.name

    if not quiet:
        console.print(f"[bold]HireShire Matcher[/bold] — scoring jobs from run [cyan]{run_id}[/cyan]")

    # --- Load resume (both modes) ---
    try:
        resume_text = extract_resume_text(settings.resume_path)
        if not quiet:
            console.print(f"Resume loaded: [green]{settings.resume_path}[/green] ({len(resume_text)} chars)")
    except (FileNotFoundError, ValueError) as exc:
        if not quiet:
            console.print(f"[red]{exc}[/red]")
        if out_queue is not None:
            await out_queue.put(None)
        return

    # --- Load optional projects context (both modes) ---
    projects_text = ""
    if settings.projects_path:
        p = Path(settings.projects_path)
        if p.exists():
            projects_text = p.read_text(encoding="utf-8")
            if not quiet:
                console.print(f"Projects loaded: [green]{settings.projects_path}[/green] ({len(projects_text)} chars)")
        elif not quiet:
            console.print(f"[yellow]projects_path set but file not found: {settings.projects_path}[/yellow]")

    # --- Set up scorer and store (both modes) ---
    started_at = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(settings.concurrency)
    if not effective_skip_llm:
        backend = make_backend(settings, sem)
        scorer = JobScorer(settings=settings, backend=backend)
    store = MatchStore(base_dir=Path(settings.matches_dir), run_id=run_id)

    seen = SeenStore(Path(settings.matches_dir).parent / "seen_jobs.json")

    results: list[MatchResult] = []

    async def score_one(job) -> MatchResult:
        try:
            result = await scorer.score(job, resume_text, run_id, projects_text)
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
        store.append_result(result)
        # In queue mode, forward shortlisted (result, job) pairs immediately
        if (out_queue is not None
                and not result.skipped
                and result.relevance_score >= settings.threshold):
            await out_queue.put((result, job))
        return result

    # =========================================================
    # Queue mode: consume company batches from in_queue
    # =========================================================
    if in_queue is not None:
        try:
            while True:
                item = await in_queue.get()
                if item is None:
                    break
                board_token, batch_jobs = item
                logger.info("Scoring batch: %s (%d jobs)", board_token, len(batch_jobs))
                unseen = [j for j in batch_jobs if j.job_id not in seen]
                if len(unseen) < len(batch_jobs):
                    logger.info(
                        "Dedup: skipping %d already-seen jobs from %s",
                        len(batch_jobs) - len(unseen), board_token,
                    )
                to_score, title_filtered = apply_title_filter(unseen, config.title_filter, run_id)
                results.extend(title_filtered)
                if effective_skip_llm:
                    for j in to_score:
                        r = _passthrough_result(j, run_id)
                        store.append_result(r)
                        if out_queue is not None:
                            await out_queue.put((r, j))
                        results.append(r)
                else:
                    results.extend(await asyncio.gather(*[score_one(j) for j in to_score]))
        except Exception:
            logger.exception("Matcher queue loop failed")
        finally:
            for r in results:
                seen.add(r.job_id)
            seen.save()
            shortlisted = [r for r in results if not r.skipped and r.relevance_score >= settings.threshold]
            rejected = [r for r in results if r.skipped or r.relevance_score < settings.threshold]
            shortlisted.sort(key=lambda r: r.relevance_score, reverse=True)
            store.finalise(shortlisted, rejected, started_at, settings.threshold, settings.model, len(results))
            logger.info(
                "Matcher done: %d shortlisted, %d rejected → data/matches/%s/",
                len(shortlisted), len(rejected), run_id,
            )
            if out_queue is not None:
                await out_queue.put(None)  # sentinel — always sent
        return

    # =========================================================
    # Standalone mode: load jobs from disk (existing behaviour)
    # =========================================================
    jobs = load_jobs(run_dir)
    if not jobs:
        if not quiet:
            console.print("[yellow]No jobs found in the latest run. Run python scraper.py first.[/yellow]")
        return

    provider = os.environ.get("LLM_PROVIDER", "gemini")
    if not quiet:
        console.print(
            f"Scoring [bold]{len(jobs)}[/bold] jobs with [bold]{provider}/{settings.model}[/bold] "
            f"(threshold: {settings.threshold}/100)\n"
        )

    prior_results = store.load_progress()
    scored_ids = {r.job_id for r in prior_results}
    if prior_results and not quiet:
        console.print(
            f"[yellow]Resuming partial run — {len(prior_results)} already scored, "
            f"{len(jobs) - len(scored_ids)} remaining.[/yellow]\n"
        )

    not_in_run = [j for j in jobs if j.job_id not in scored_ids]
    unscored = [j for j in not_in_run if j.job_id not in seen]
    dedup_skipped = len(not_in_run) - len(unscored)
    if dedup_skipped > 0 and not quiet:
        console.print(f"[yellow]Dedup: {dedup_skipped} jobs skipped (already scored in a previous run)[/yellow]\n")
    jobs_to_score, title_filtered = apply_title_filter(unscored, config.title_filter, run_id)
    if title_filtered and not quiet:
        console.print(
            f"Title filter: [yellow]{len(title_filtered)} filtered out[/yellow], "
            f"[green]{len(jobs_to_score)} sent to LLM scoring[/green]\n"
        )

    results = list(prior_results) + title_filtered

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
        task = progress.add_task("Scoring jobs...", total=len(jobs_to_score))

        if effective_skip_llm:
            for j in jobs_to_score:
                r = _passthrough_result(j, run_id)
                store.append_result(r)
                results.append(r)
                progress.advance(task)
        else:
            async def score_one_p(job):
                try:
                    return await score_one(job)
                finally:
                    progress.advance(task)

            results += list(await asyncio.gather(*[score_one_p(j) for j in jobs_to_score]))

    shortlisted = [r for r in results if not r.skipped and r.relevance_score >= settings.threshold]
    rejected = [r for r in results if r.skipped or r.relevance_score < settings.threshold]
    shortlisted.sort(key=lambda r: r.relevance_score, reverse=True)
    store.finalise(shortlisted, rejected, started_at, settings.threshold, settings.model, len(jobs))
    for r in results:
        seen.add(r.job_id)
    seen.save()

    if not quiet:
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

        title_filtered_count = sum(
            1 for r in results if r.skip_reason in ("title_excluded", "title_no_include_match")
        )
        other_skipped_count = sum(
            1 for r in results
            if r.skipped and r.skip_reason not in ("title_excluded", "title_no_include_match")
        )
        llm_skipped_count = sum(1 for r in results if r.skip_reason == "llm_skipped")
        console.print(
            f"\n[bold]{len(shortlisted)} shortlisted[/bold], "
            f"{len(rejected) - title_filtered_count - other_skipped_count} rejected by LLM, "
            f"{title_filtered_count} title-filtered, "
            + (f"{llm_skipped_count} LLM-skipped (auto-shortlisted), " if llm_skipped_count else "")
            + f"{other_skipped_count} skipped "
            f"→ [cyan]data/matches/{run_id}/[/cyan]"
        )


if __name__ == "__main__":
    asyncio.run(main())
