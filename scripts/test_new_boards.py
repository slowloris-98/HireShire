"""
Focused smoke-test for the Workday and BambooHR scrapers without touching the
15k+ Greenhouse/Lever/Ashby lists that a full `python scraper.py` would fetch.

Usage:
    python scripts/test_new_boards.py                        # all slugs in config/{workday,bamboohr}_companies.json
    python scripts/test_new_boards.py --board workday        # one board only
    python scripts/test_new_boards.py --board bamboohr 10web 17live   # explicit slugs
    python scripts/test_new_boards.py --apply-cutoff         # also apply config's max_age_hours filter

Reads settings (concurrency, timeout, max_age_hours) from config/scraper.yaml so
the behaviour matches a real run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as `python scripts/test_new_boards.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hireshire.config import load_config
from hireshire.http_client import build_client
from hireshire.scrapers.bamboohr import BambooHRScraper
from hireshire.scrapers.exceptions import SlugNotFoundError
from hireshire.scrapers.workday import WorkdayScraper

CONFIG_DIR = Path("config")


def _load_slugs(board: str) -> list[str]:
    path = CONFIG_DIR / f"{board}_companies.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


async def _run_board(scraper, board: str, slugs: list[str], cutoff) -> None:
    print(f"\n{'=' * 70}\n{board.upper()} — {len(slugs)} slug(s)\n{'=' * 70}")

    async def one(slug: str):
        try:
            jobs = await scraper.fetch_all(slug)
        except SlugNotFoundError:
            print(f"  {slug:<40} DEAD SLUG (would be cached in bad_slugs.json)")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"  {slug:<40} ERROR: {type(exc).__name__}: {exc}")
            return

        kept = [j for j in jobs if j.updated_at >= cutoff] if cutoff else jobs
        suffix = f" ({len(kept)} within cutoff)" if cutoff else ""
        print(f"  {slug:<40} {len(jobs)} jobs{suffix}")
        if jobs:
            j = jobs[0]
            print(
                f"       e.g. '{j.title[:45]}' | {j.location.name[:25]} | "
                f"desc={len(j.content_text or '')}c | q={len(j.questions)} | "
                f"upd={str(j.updated_at)[:10]} | {j.absolute_url}"
            )

    await asyncio.gather(*(one(s) for s in slugs))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test Workday/BambooHR scrapers")
    parser.add_argument("--board", choices=["workday", "bamboohr"], help="limit to one board")
    parser.add_argument("slugs", nargs="*", help="explicit slugs (requires --board)")
    parser.add_argument("--apply-cutoff", action="store_true", help="apply config max_age_hours filter")
    parser.add_argument("--limit", type=int, help="only test the first N slugs per board (avoids hammering huge lists)")
    args = parser.parse_args()

    settings = load_config("config/scraper.yaml").settings
    cutoff = None
    if args.apply_cutoff and settings.max_age_hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.max_age_hours)
        print(f"Applying cutoff: jobs updated after {cutoff:%Y-%m-%d %H:%M UTC} (last {settings.max_age_hours}h)")

    boards = [args.board] if args.board else ["workday", "bamboohr"]

    async with build_client(settings.request_timeout_s) as client:
        scrapers = {
            "workday": WorkdayScraper(
                client, settings.make_limiter("workday"), settings.retry_attempts, cutoff=cutoff,
                detail_concurrency=settings.detail_concurrency, detail_jitter_s=settings.detail_jitter_s,
            ),
            "bamboohr": BambooHRScraper(
                client, settings.make_limiter("bamboohr"), settings.retry_attempts,
                detail_concurrency=settings.detail_concurrency, detail_jitter_s=settings.detail_jitter_s,
            ),
        }
        for board in boards:
            slugs = args.slugs if (args.slugs and args.board == board) else _load_slugs(board)
            if args.limit:
                slugs = slugs[: args.limit]
            await _run_board(scrapers[board], board, slugs, cutoff)


if __name__ == "__main__":
    asyncio.run(main())
