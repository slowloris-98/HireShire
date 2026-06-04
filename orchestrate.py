"""
HireShire pipeline orchestrator — runs Scraper → Matcher → Tuner concurrently
via asyncio queues, then repeats on a schedule.

    python orchestrate.py              # wait 4h, then run; repeat every 4h
    python orchestrate.py --now        # run immediately, then every 4h
    python orchestrate.py --once       # run exactly once, no scheduling
    python orchestrate.py --interval 2 # every 2 hours instead of 4
"""

import argparse
import asyncio
import csv
import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

import matcher
import scraper
import tuner

load_dotenv()

console = Console()
logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "orchestrate.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            RichHandler(show_path=False, show_time=False, rich_tracebacks=True),
            file_handler,
        ],
    )
    logging.getLogger("hireshire").setLevel(logging.INFO)
    # Suppress noisy third-party loggers
    for name in ("httpx", "httpcore", "playwright", "browser_use"):
        logging.getLogger(name).setLevel(logging.WARNING)


_CSV_FIELDS = [
    "processed_at", "company", "title", "job_url",
    "relevance_score", "resume_tex", "resume_pdf",
    "job_id", "tuner_run_id",
]


async def _track_results(q: asyncio.Queue, results_dir: Path) -> None:
    json_path = results_dir / "pipeline_results.json"
    csv_path = results_dir / "pipeline_results.csv"

    while True:
        record = await q.get()
        if record is None:
            break

        existing = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else []
        existing.append(record)
        json_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

        write_header = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(record)

        logger.info("Tracked result: %s — %s", record["company"], record["title"])


async def run_pipeline() -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    # q1: scraper → matcher   — items: (board_token, list[Job]) | None
    # q2: matcher → tuner     — items: (MatchResult, Job) | None
    # q3: tuner → tracker     — items: result dict | None
    q1: asyncio.Queue = asyncio.Queue()
    q2: asyncio.Queue = asyncio.Queue()
    q3: asyncio.Queue = asyncio.Queue()

    results_dir = Path("data")
    results_dir.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("Pipeline starting — run %s", run_id)
    logger.info("=" * 60)

    try:
        await asyncio.gather(
            scraper.main(out_queue=q1, quiet=True, run_id=run_id),
            matcher.main(in_queue=q1, out_queue=q2, quiet=True, run_id=run_id),
            tuner.main(in_queue=q2, out_queue=q3, quiet=True),
            _track_results(q3, results_dir),
        )
        logger.info("Pipeline complete — run %s", run_id)
    except Exception:
        logger.exception("Pipeline failed — run %s", run_id)


async def main() -> None:
    parser = argparse.ArgumentParser(description="HireShire pipeline orchestrator")
    parser.add_argument(
        "--now", action="store_true",
        help="Run the pipeline immediately on start, then schedule",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run exactly once and exit (no scheduling)",
    )
    parser.add_argument(
        "--interval", type=float, default=4.0, metavar="HOURS",
        help="Hours between pipeline runs (default: 4)",
    )
    args = parser.parse_args()

    _setup_logging()

    interval_s = args.interval * 3600

    if not args.now and not args.once:
        logger.info("Orchestrator started — first run in %.1fh", args.interval)
        await asyncio.sleep(interval_s)

    while True:
        await run_pipeline()
        if args.once:
            break
        logger.info("Next run in %.1fh", args.interval)
        await asyncio.sleep(interval_s)


if __name__ == "__main__":
    asyncio.run(main())
