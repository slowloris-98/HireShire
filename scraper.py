"""
Company slugs are loaded from config/ashby_companies.json, config/greenhouse_companies.json,
and config/lever_companies.json. Bad slugs (404s) are persisted in config/bad_slugs.json
and skipped on subsequent runs. Run with:
    python scraper.py
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from hireshire.config import load_config
from hireshire.http_client import build_client
from hireshire.scrapers.ashby import AshbyScraper
from hireshire.scrapers.exceptions import SlugNotFoundError
from hireshire.scrapers.greenhouse import GreenhouseScraper
from hireshire.scrapers.lever import LeverScraper
from hireshire.storage.json_store import RunStore

logger = logging.getLogger(__name__)
console = Console()

BAD_SLUGS_PATH = Path("config/bad_slugs.json")
_PLATFORMS = ("ashby", "greenhouse", "lever")


def _load_bad_slugs() -> dict[str, set[str]]:
    if BAD_SLUGS_PATH.exists():
        raw = json.loads(BAD_SLUGS_PATH.read_text(encoding="utf-8"))
        return {p: set(raw.get(p, [])) for p in _PLATFORMS}
    return {p: set() for p in _PLATFORMS}


def _save_bad_slugs(bad: dict[str, set[str]]) -> None:
    BAD_SLUGS_PATH.write_text(
        json.dumps({p: sorted(bad[p]) for p in _PLATFORMS}, indent=2),
        encoding="utf-8",
    )


def _matches_location(job, terms: list[str]) -> bool:
    haystack = [job.location.name.lower()]
    haystack += [o.location.lower() for o in job.offices if o.location]
    return any(term in loc for term in terms for loc in haystack)


class _NoopProgress:
    def update(self, *a, **kw): pass
    def advance(self, *a, **kw): pass
    def add_task(self, *a, **kw): return 0
    def __enter__(self): return self
    def __exit__(self, *a): pass


async def main(
    out_queue: asyncio.Queue | None = None,
    quiet: bool = False,
    run_id: str | None = None,
    on_company_start=None,
) -> None:
    if not quiet:
        logging.basicConfig(
            level=logging.WARNING,
            handlers=[RichHandler(show_path=False, rich_tracebacks=True)],
        )

    config = load_config("config/scraper.yaml")
    settings = config.settings

    bad_slugs = _load_bad_slugs()
    total_skipped = sum(len(v) for v in bad_slugs.values())

    ashby_companies = [c for c in config.ashby_companies if c.ashby_token not in bad_slugs["ashby"]]
    greenhouse_companies = [c for c in config.greenhouse_companies if c.greenhouse_token not in bad_slugs["greenhouse"]]
    lever_companies = [c for c in config.lever_companies if c.lever_token not in bad_slugs["lever"]]

    if not greenhouse_companies and not lever_companies and not ashby_companies:
        if not quiet:
            console.print("[yellow]No companies to scrape. All slugs may be in the bad-slugs list.[/yellow]")
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
        sources = []
        if greenhouse_companies:
            sources.append(f"[bold]{len(greenhouse_companies)}[/bold] via Greenhouse")
        if lever_companies:
            sources.append(f"[bold]{len(lever_companies)}[/bold] via Lever")
        if ashby_companies:
            sources.append(f"[bold]{len(ashby_companies)}[/bold] via Ashby")
        console.print(f"Fetching from {' + '.join(sources)}", end="")
        if total_skipped:
            console.print(f"  [dim]({total_skipped} known-bad slugs skipped)[/dim]")
        else:
            console.print()
        console.print()

    sem = asyncio.Semaphore(settings.concurrency)
    lock = asyncio.Lock()
    newly_bad: dict[str, list[str]] = {p: [] for p in _PLATFORMS}

    # Counters shared across tasks (mutated under lock)
    counters = {"jobs": 0, "with_jobs": 0, "errors": 0, "not_found": 0}
    errors_detail: list[tuple[str, str]] = []  # (name, error message)

    try:
        async with build_client(settings.request_timeout_s) as client:
            greenhouse_scraper = GreenhouseScraper(client, sem, settings.retry_attempts)
            lever_scraper = LeverScraper(client, sem, settings.retry_attempts, cutoff=cutoff)
            ashby_scraper = AshbyScraper(client, sem, settings.retry_attempts)

            total_companies = len(greenhouse_companies) + len(lever_companies) + len(ashby_companies)
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
                task = progress.add_task("Scraping companies...", total=total_companies)

                async def scrape_one(company, scraper_instance, token, platform):
                    t0 = time.monotonic()
                    try:
                        jobs = await asyncio.wait_for(
                            scraper_instance.fetch_all(token),
                            timeout=settings.company_timeout_s,
                        )
                        elapsed = time.monotonic() - t0
                        if cutoff:
                            jobs = [j for j in jobs if j.updated_at >= cutoff]
                        if location_terms:
                            jobs = [j for j in jobs if _matches_location(j, location_terms)]
                        store.save_company(token, jobs, fetch_time_s=elapsed)
                        if out_queue is not None:
                            await out_queue.put((token, jobs))
                        async with lock:
                            counters["jobs"] += len(jobs)
                            if jobs:
                                counters["with_jobs"] += 1
                    except SlugNotFoundError as exc:
                        async with lock:
                            newly_bad[exc.platform].append(exc.token)
                            counters["not_found"] += 1
                        if out_queue is not None:
                            await out_queue.put((token, []))
                    except asyncio.TimeoutError:
                        elapsed = time.monotonic() - t0
                        msg = f"timeout after {settings.company_timeout_s}s"
                        logger.warning("Scrape timed out: %s — skipping", company.name)
                        store.record_error(token, "timeout", msg, fetch_time_s=elapsed)
                        if out_queue is not None:
                            await out_queue.put((token, []))
                        async with lock:
                            counters["errors"] += 1
                            errors_detail.append((company.name, msg))
                    except Exception as exc:
                        elapsed = time.monotonic() - t0
                        msg = str(exc)
                        store.record_error(token, "error", msg, fetch_time_s=elapsed)
                        logger.exception("Failed to scrape %s", company.name)
                        async with lock:
                            counters["errors"] += 1
                            errors_detail.append((company.name, msg))
                    finally:
                        progress.advance(task)
                        if on_company_start:
                            on_company_start(company.name)

                gh_tasks = [scrape_one(c, greenhouse_scraper, c.greenhouse_token, "greenhouse") for c in greenhouse_companies]
                lv_tasks = [scrape_one(c, lever_scraper, c.lever_token, "lever") for c in lever_companies]
                as_tasks = [scrape_one(c, ashby_scraper, c.ashby_token, "ashby") for c in ashby_companies]
                await asyncio.gather(*gh_tasks, *lv_tasks, *as_tasks)

        store.save_manifest(started_at)
    finally:
        if out_queue is not None:
            await out_queue.put(None)

    # Persist newly discovered bad slugs
    new_count = sum(len(v) for v in newly_bad.values())
    if new_count:
        for platform, tokens in newly_bad.items():
            bad_slugs[platform].update(tokens)
        _save_bad_slugs(bad_slugs)

    if not quiet:
        console.print("\n[bold]Results[/bold]")
        zero_jobs = total_companies - counters["with_jobs"] - counters["errors"] - counters["not_found"]
        console.print(f"  [green]✓[/green] {counters['with_jobs']} companies had jobs  ({counters['jobs']} total jobs)")
        console.print(f"  [dim]·[/dim] {zero_jobs} companies: 0 jobs")
        if counters["not_found"]:
            console.print(f"  [dim]+[/dim] {counters['not_found']} new bad slugs → [cyan]{BAD_SLUGS_PATH}[/cyan]")
        if counters["errors"]:
            console.print(f"  [red]✗[/red] {counters['errors']} errors")
            for name, msg in errors_detail:
                console.print(f"      [red]{name}[/red]: {msg}")
        console.print(
            f"\n[bold green]{counters['jobs']} total jobs[/bold green] saved to [cyan]data/scraped/{run_id}/[/cyan]"
        )


if __name__ == "__main__":
    asyncio.run(main())
