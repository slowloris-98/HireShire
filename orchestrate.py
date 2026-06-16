"""
HireShire pipeline orchestrator — runs Scraper → Matcher → Tuner concurrently
via asyncio queues, then repeats on a schedule.

    python orchestrate.py              # wait 4h, then run; repeat every 4h
    python orchestrate.py --now        # run immediately, then every 4h
    python orchestrate.py --once       # run exactly once, no scheduling
    python orchestrate.py --interval 2 # every 2 hours instead of 4
    python orchestrate.py --no-tuner   # scraper + matcher only (no resume tuning)
    python orchestrate.py --no-matcher # scraper only (no scoring or tuning)
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
from rich.live import Live
from rich.logging import RichHandler
from rich.table import Table

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
            RichHandler(console=console, show_path=False, show_time=False, rich_tracebacks=True),
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
    "tuner_status", "job_id", "tuner_run_id",
]


async def _bypass_tuner(in_q: asyncio.Queue, out_q: asyncio.Queue) -> None:
    while True:
        item = await in_q.get()
        if item is None:
            break
        match_result, job = item
        await out_q.put({
            "job_id": job.job_id,
            "title": job.title,
            "company": job.board_token,
            "job_url": str(match_result.absolute_url),
            "relevance_score": match_result.relevance_score,
            "resume_tex": None,
            "resume_pdf": None,
            "tuner_status": "skipped",
            "tuner_run_id": None,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        })
    await out_q.put(None)


async def _launch_apply() -> None:
    skill_path = Path(".claude/commands/apply.md")
    if not skill_path.exists():
        logger.error("apply skill not found at %s", skill_path)
        return

    skill_prompt = skill_path.read_text(encoding="utf-8")
    logger.info("Launching /apply skill...")

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p",
        "--permission-mode", "auto",
        skill_prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        logger.info("apply output:\n%s", stdout.decode(errors="replace"))
    if proc.returncode != 0:
        logger.error(
            "apply skill exited with code %d\n%s",
            proc.returncode,
            stderr.decode(errors="replace"),
        )
    else:
        logger.info("apply skill completed successfully")


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


def _make_status_table(scrape: str, match: str) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_row("[dim]Scraping:[/dim]", f"[cyan]{scrape}[/cyan]")
    grid.add_row("[dim]Matching:[/dim]", f"[cyan]{match}[/cyan]")
    return grid


async def run_pipeline(skip_matcher: bool = False, skip_tuner: bool = False, skip_llm: bool = False) -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

    results_dir = Path("data/pipeline") / run_id
    results_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Pipeline starting — run %s", run_id)
    logger.info("=" * 60)

    q3: asyncio.Queue = asyncio.Queue()

    status = {"scrape": "—", "match": "—"}

    def on_company_start(name: str) -> None:
        status["scrape"] = name
        live.update(_make_status_table(status["scrape"], status["match"]))

    def on_job_score(board_token: str, title: str) -> None:
        status["match"] = f"{board_token} — {title}"
        live.update(_make_status_table(status["scrape"], status["match"]))

    try:
        with Live(_make_status_table("—", "—"), console=console, refresh_per_second=4) as live:
            if skip_matcher:
                await scraper.main(quiet=True, run_id=run_id, on_company_start=on_company_start)
                await q3.put(None)
                await _track_results(q3, results_dir)
            elif skip_tuner:
                q1: asyncio.Queue = asyncio.Queue()
                q2: asyncio.Queue = asyncio.Queue()
                await asyncio.gather(
                    scraper.main(out_queue=q1, quiet=True, run_id=run_id, on_company_start=on_company_start),
                    matcher.main(in_queue=q1, out_queue=q2, quiet=True, run_id=run_id, skip_llm=skip_llm, on_job_score=on_job_score),
                    _bypass_tuner(q2, q3),
                    _track_results(q3, results_dir),
                )
            else:
                q1: asyncio.Queue = asyncio.Queue()
                q2: asyncio.Queue = asyncio.Queue()
                await asyncio.gather(
                    scraper.main(out_queue=q1, quiet=True, run_id=run_id, on_company_start=on_company_start),
                    matcher.main(in_queue=q1, out_queue=q2, quiet=True, run_id=run_id, skip_llm=skip_llm, on_job_score=on_job_score),
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
    parser.add_argument(
        "--no-tuner", action="store_true",
        help="Run scraper and matcher only; skip tuner",
    )
    parser.add_argument(
        "--no-matcher", action="store_true",
        help="Run scraper only; skip matcher and tuner",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM scoring in the matcher; all title-passing jobs are shortlisted automatically",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="After each pipeline run, invoke the /apply skill to submit applications",
    )
    args = parser.parse_args()

    _setup_logging()

    interval_s = args.interval * 3600

    if not args.now and not args.once:
        logger.info("Orchestrator started — first run in %.1fh", args.interval)
        await asyncio.sleep(interval_s)

    while True:
        await run_pipeline(skip_matcher=args.no_matcher, skip_tuner=args.no_tuner, skip_llm=args.no_llm)
        if args.apply and not args.no_tuner and not args.no_matcher:
            await _launch_apply()
        if args.once:
            break
        logger.info("Next run in %.1fh", args.interval)
        await asyncio.sleep(interval_s)


if __name__ == "__main__":
    asyncio.run(main())
