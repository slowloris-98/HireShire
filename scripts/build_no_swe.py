"""
Classify Workday / BambooHR companies by whether they actually post software
engineering (or adjacent: ML/AI/data/etc.) roles, and record the ones that DON'T
in config/no_swe.json so the scraper skips them — without ever touching the source
config/{workday,bamboohr}_companies.json lists.

Signal: a company is KEPT if, among jobs posted in the last N days (default 60), any
title matches the matcher's `include_keywords` (config/matcher.yaml). Only the
*include* keywords are used — a company whose only SWE role is "Senior Software
Engineer" still hires SWEs, so the matcher's exclude list (which is about which roles
to apply to) is intentionally ignored here.

Classification is LIST-ONLY (no per-job detail fetches), so it is far lighter than a
real scrape:
  - Workday list entries carry `title` + a relative `postedOn` → the age window is
    applied directly.
  - BambooHR list entries carry only titles (the date lives on the detail endpoint),
    but a careers list exposes only currently-open roles, so any current SWE title
    keeps the company. Pass --bamboohr-detail to instead fetch details and apply a
    strict N-day window there too (slower).

Buckets: HAS_SWE → keep; NO_SWE (fetched OK, nothing qualifies) → add to no_swe.json;
UNKNOWN (404 / blocked / timeout / any error) → keep, never excluded on a transient
failure.

    python scripts/build_no_swe.py                                  # dry-run, both platforms
    python scripts/build_no_swe.py --platform bamboohr --limit 300  # sample
    python scripts/build_no_swe.py --apply                          # write config/no_swe.json
    python scripts/build_no_swe.py --days 90 --bamboohr-detail --apply
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as `python scripts/build_no_swe.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from hireshire.config import load_config
from hireshire.http_client import build_client
from hireshire.matcher.config import load_matcher_config
from hireshire.rate_limit import RateLimiter
from hireshire.scrapers.bamboohr import BambooHRScraper
from hireshire.scrapers.workday import WorkdayScraper
from scraper import NO_SWE_PATH, _load_bad_slugs, _load_no_swe, _save_no_swe

console = Console()

_PLATFORMS = ("bamboohr", "workday")


def _qualifies(title: str, posted, includes: list[str], cutoff: datetime) -> bool:
    """True if the title is SWE/adjacent AND the posting is within the age window.
    A missing/naive/unparseable date is treated as in-window (kept), so we never drop
    a match just because we couldn't date it."""
    t = title.lower()
    if not any(kw in t for kw in includes):
        return False
    if posted is None or posted.tzinfo is None:
        return True
    return posted >= cutoff


async def _classify(scraper, platform, token, includes, cutoff, use_detail):
    """Return 'has_swe' | 'no_swe' | 'unknown' for one company."""
    try:
        if platform == "bamboohr" and use_detail:
            listings = [(j.title, j.updated_at) for j in await scraper.fetch_all(token)]
        else:
            listings = await scraper.fetch_listings(token)
    except Exception:
        # SlugNotFoundError (dead → bad_slugs' job), BoardBlockedError, timeout, network:
        # inconclusive, so keep the company and let a later run resolve it.
        return "unknown"
    return "has_swe" if any(_qualifies(t, p, includes, cutoff) for t, p in listings) else "no_swe"


async def _run_platform(platform, tokens, scraper, includes, cutoff, use_detail, workers, progress, task):
    """Drain a platform's tokens through a fixed worker pool; return {status: [tokens]}."""
    results: dict[str, list[str]] = {"has_swe": [], "no_swe": [], "unknown": []}
    queue: asyncio.Queue = asyncio.Queue()
    for tok in tokens:
        queue.put_nowait(tok)

    async def worker():
        while True:
            try:
                tok = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            status = await _classify(scraper, platform, tok, includes, cutoff, use_detail)
            results[status].append(tok)
            progress.advance(task)

    await asyncio.gather(*(worker() for _ in range(max(1, workers))))
    return results


def _display_name(platform: str, token: str) -> str:
    # Workday tokens are 'company|wd#|site'; show just the company part.
    return token.split("|")[0] if platform == "workday" else token


async def main() -> None:
    parser = argparse.ArgumentParser(description="Build config/no_swe.json from live job titles.")
    parser.add_argument("--apply", action="store_true", help="Write results to config/no_swe.json (default: dry-run).")
    parser.add_argument("--dry-run", action="store_true", help="Report only, write nothing (the default; --apply overrides).")
    parser.add_argument("--platform", choices=_PLATFORMS, help="Limit to one platform.")
    parser.add_argument("--days", type=int, default=60, help="Age window in days (default: 60).")
    parser.add_argument("--limit", type=int, help="Only classify the first N companies per platform (sampling).")
    parser.add_argument(
        "--bamboohr-detail", action="store_true",
        help="For BambooHR, fetch job details to apply a strict N-day window (slower).",
    )
    parser.add_argument(
        "--workers", type=int,
        help="Company-level worker pool per platform (default: scraper.yaml company_concurrency, "
             "~5). This is list-only, so it tolerates much higher values (e.g. 20-40) than a real "
             "scrape — raise together with --concurrency and watch the 'unknown' count for throttling.",
    )
    parser.add_argument(
        "--concurrency", type=int,
        help="Max in-flight HTTP requests per platform (default: scraper.yaml rate_limits, ~6). "
             "Raise alongside --workers; a rising 'unknown' bucket means you hit the host throttle.",
    )
    args = parser.parse_args()

    config = load_config("config/scraper.yaml")
    settings = config.settings
    includes = [kw.lower() for kw in load_matcher_config().title_filter.include_keywords]
    if not includes:
        console.print("[red]No include_keywords in config/matcher.yaml — nothing to match against.[/red]")
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    bad_slugs = _load_bad_slugs()
    no_swe = _load_no_swe()
    platforms = [args.platform] if args.platform else list(_PLATFORMS)

    # Candidate tokens: skip already-bad and already-no_swe slugs.
    company_lists = {"bamboohr": config.bamboohr_companies, "workday": config.workday_companies}
    token_attr = {"bamboohr": "bamboohr_token", "workday": "workday_token"}
    candidates: dict[str, list[str]] = {}
    for p in platforms:
        toks = [
            getattr(c, token_attr[p]) for c in company_lists[p]
            if getattr(c, token_attr[p]) not in bad_slugs[p] and getattr(c, token_attr[p]) not in no_swe[p]
        ]
        if args.limit:
            toks = toks[: args.limit]
        candidates[p] = toks

    total = sum(len(v) for v in candidates.values())
    if total == 0:
        console.print("[yellow]No candidate companies to classify.[/yellow]")
        return

    mode = "[green]APPLY[/green]" if args.apply else "[cyan]DRY-RUN[/cyan]"
    console.print(
        f"[bold]Building no_swe[/bold] ({mode}) — {total} companies across {', '.join(platforms)}; "
        f"window = last {args.days} days\n"
    )

    def _limiter(platform: str):
        # --concurrency overrides the board's scraper.yaml rate-limit width (no request
        # spacing, since these list endpoints aren't interval-limited).
        if args.concurrency:
            return RateLimiter(args.concurrency, 0.0)
        return settings.make_limiter(platform)

    all_results: dict[str, dict[str, list[str]]] = {}
    async with build_client(settings.request_timeout_s) as client:
        scrapers = {
            "workday": WorkdayScraper(
                client, _limiter("workday"), settings.retry_attempts, cutoff=None,
                detail_concurrency=settings.detail_concurrency, detail_jitter_s=settings.detail_jitter_s,
            ),
            "bamboohr": BambooHRScraper(
                client, _limiter("bamboohr"), settings.retry_attempts,
                detail_concurrency=settings.detail_concurrency, detail_jitter_s=settings.detail_jitter_s,
            ),
        }
        with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), MofNCompleteColumn(), console=console,
        ) as progress:
            for p in platforms:
                if not candidates[p]:
                    continue
                workers = args.workers or settings.company_workers(p)
                task = progress.add_task(f"Classifying {p}", total=len(candidates[p]))
                all_results[p] = await _run_platform(
                    p, candidates[p], scrapers[p], includes, cutoff,
                    args.bamboohr_detail, workers, progress, task,
                )

    # Report + optionally persist
    console.print("\n[bold]Results[/bold]")
    total_no_swe = 0
    for p in platforms:
        res = all_results.get(p, {"has_swe": [], "no_swe": [], "unknown": []})
        total_no_swe += len(res["no_swe"])
        console.print(
            f"  [bold]{p}[/bold]: [green]{len(res['has_swe'])} keep[/green], "
            f"[red]{len(res['no_swe'])} no-SWE[/red], "
            f"[yellow]{len(res['unknown'])} unknown (kept)[/yellow]"
        )
        # De-dup display names (Workday lists one token per career site, so a company
        # can otherwise appear several times) for a cleaner review list.
        names = sorted({_display_name(p, t) for t in res["no_swe"]})
        for name in names[:25]:
            console.print(f"      [red]-[/red] {name}")
        if len(names) > 25:
            console.print(f"      [dim]… and {len(names) - 25} more companies[/dim]")

    if not args.apply:
        console.print(
            f"\n[cyan]DRY-RUN[/cyan] — would add [bold]{total_no_swe}[/bold] companies to "
            f"[cyan]{NO_SWE_PATH}[/cyan]. Re-run with [cyan]--apply[/cyan] to write."
        )
        return

    for p in platforms:
        no_swe[p].update(all_results.get(p, {}).get("no_swe", []))
    _save_no_swe(no_swe)
    console.print(
        f"\n[bold green]Wrote {total_no_swe} new no-SWE companies[/bold green] to [cyan]{NO_SWE_PATH}[/cyan] "
        f"(total now {sum(len(v) for v in no_swe.values())})."
    )


if __name__ == "__main__":
    asyncio.run(main())
