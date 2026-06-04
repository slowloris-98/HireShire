"""
Add/remove companies in config/scraper.yaml, then run:
    python scraper.py
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from hireshire.config import load_config
from hireshire.http_client import build_client
from hireshire.scrapers.greenhouse import GreenhouseScraper
from hireshire.storage.json_store import RunStore

logger = logging.getLogger(__name__)
console = Console()


def _matches_location(job, terms: list[str]) -> bool:
    """Return True if any term is a substring of the job's location or any office location."""
    haystack = [job.location.name.lower()]
    haystack += [o.location.lower() for o in job.offices if o.location]
    return any(term in loc for term in terms for loc in haystack)


class _NoopProgress:
    """Drop-in replacement for Rich Progress used in quiet mode."""
    def update(self, *a, **kw): pass
    def advance(self, *a, **kw): pass
    def add_task(self, *a, **kw): return 0
    def __enter__(self): return self
    def __exit__(self, *a): pass


async def main(
    out_queue: asyncio.Queue | None = None,
    quiet: bool = False,
    run_id: str | None = None,
) -> None:
    if not quiet:
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
        )

    config = load_config("config/scraper.yaml")
    settings = config.settings
    greenhouse_companies = config.greenhouse_companies

    if not greenhouse_companies:
        if not quiet:
            console.print("[yellow]No companies with greenhouse_token found in config.[/yellow]")
        return

    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    store = RunStore(base_dir=Path("data/scraped"), run_id=run_id)
    started_at = datetime.now(timezone.utc)

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=settings.max_age_hours)
        if settings.max_age_hours
        else None
    )
    location_terms = [t.lower() for t in settings.location_filter]

    if not quiet:
        console.print(f"[bold]HireShire Scraper[/bold] — run [cyan]{run_id}[/cyan]")
        if cutoff:
            console.print(
                f"Filtering jobs updated after [cyan]{cutoff.strftime('%Y-%m-%d %H:%M UTC')}[/cyan]"
                f" (last {settings.max_age_hours}h)"
            )
        if location_terms:
            console.print(f"Filtering by location: [cyan]{', '.join(settings.location_filter)}[/cyan]")
        console.print(
            f"Fetching from [bold]{len(greenhouse_companies)}[/bold] companies via Greenhouse API\n"
        )

    sem = asyncio.Semaphore(settings.concurrency)
    results = []

    try:
        async with build_client(settings.request_timeout_s) as client:
            scraper_obj = GreenhouseScraper(client, sem, settings.retry_attempts)

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
                task = progress.add_task("Scraping companies...", total=len(greenhouse_companies))

                async def scrape_one(company):
                    token = company.greenhouse_token
                    progress.update(task, description=f"[cyan]{company.name}[/cyan]")
                    try:
                        jobs = await scraper_obj.fetch_all(token)
                        if cutoff:
                            jobs = [j for j in jobs if j.updated_at >= cutoff]
                        if location_terms:
                            jobs = [j for j in jobs if _matches_location(j, location_terms)]
                        store.save_company(token, jobs)
                        if out_queue is not None:
                            await out_queue.put((token, jobs))
                        logger.info("Scraped %s: %d jobs", company.name, len(jobs))
                        return company.name, len(jobs), None
                    except Exception as exc:
                        store.record_error(token, "error", str(exc))
                        logger.exception("Failed to scrape %s", company.name)
                        return company.name, 0, str(exc)
                    finally:
                        progress.advance(task)

                results = await asyncio.gather(*[scrape_one(c) for c in greenhouse_companies])

        store.save_manifest(started_at)
    finally:
        if out_queue is not None:
            await out_queue.put(None)  # sentinel — always sent, even on error

    if not quiet:
        console.print("\n[bold]Results[/bold]")
        total = 0
        for name, count, error in results:
            if error:
                console.print(f"  [red]✗[/red] {name}: {error}")
            else:
                console.print(f"  [green]✓[/green] {name}: {count} jobs")
                total += count
        console.print(
            f"\n[bold green]{total} total jobs[/bold green] saved to [cyan]data/scraped/{run_id}/[/cyan]"
        )


if __name__ == "__main__":
    asyncio.run(main())
