"""
Add/remove companies in config/companies.yaml, then run:
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

logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
)
logger = logging.getLogger(__name__)
console = Console()


async def main() -> None:
    config = load_config("config/companies.yaml")
    settings = config.settings
    greenhouse_companies = config.greenhouse_companies

    if not greenhouse_companies:
        console.print("[yellow]No companies with greenhouse_token found in config.[/yellow]")
        return

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    store = RunStore(base_dir=Path("data/runs"), run_id=run_id)
    started_at = datetime.now(timezone.utc)

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=settings.max_age_hours)
        if settings.max_age_hours
        else None
    )

    console.print(f"[bold]HireShire Scraper[/bold] — run [cyan]{run_id}[/cyan]")
    if cutoff:
        console.print(f"Filtering jobs updated after [cyan]{cutoff.strftime('%Y-%m-%d %H:%M UTC')}[/cyan] (last {settings.max_age_hours}h)")
    console.print(f"Fetching from [bold]{len(greenhouse_companies)}[/bold] companies via Greenhouse API\n")

    sem = asyncio.Semaphore(settings.concurrency)

    async with build_client(settings.request_timeout_s) as client:
        scraper = GreenhouseScraper(client, sem, settings.retry_attempts)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Scraping companies...", total=len(greenhouse_companies))

            async def scrape_one(company):
                token = company.greenhouse_token
                progress.update(task, description=f"[cyan]{company.name}[/cyan]")
                try:
                    jobs = await scraper.fetch_all(token)
                    if cutoff:
                        jobs = [j for j in jobs if j.updated_at >= cutoff]
                    store.save_company(token, jobs)
                    return company.name, len(jobs), None
                except Exception as exc:
                    store.record_error(token, "error", str(exc))
                    logger.exception("Failed to scrape %s", company.name)
                    return company.name, 0, str(exc)
                finally:
                    progress.advance(task)

            results = await asyncio.gather(*[scrape_one(c) for c in greenhouse_companies])

    store.save_manifest(started_at)

    console.print("\n[bold]Results[/bold]")
    total = 0
    for name, count, error in results:
        if error:
            console.print(f"  [red]✗[/red] {name}: {error}")
        else:
            console.print(f"  [green]✓[/green] {name}: {count} jobs")
            total += count

    console.print(f"\n[bold green]{total} total jobs[/bold green] saved to [cyan]data/runs/{run_id}/[/cyan]")


if __name__ == "__main__":
    asyncio.run(main())
