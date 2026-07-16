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
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

import matcher
import scraper
import tuner
from hireshire.storage.db import PHASE_PIPELINE, get_db

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


async def _open_csv_append(path: Path, attempts: int = 5, base_delay: float = 0.5):
    """Open `path` in append mode, retrying on a transient Windows lock
    (PermissionError) with exponential backoff. Returns the open file handle,
    or None if it could not be opened after `attempts` tries."""
    for i in range(attempts):
        try:
            return path.open("a", newline="", encoding="utf-8")
        except PermissionError:
            if i == attempts - 1:
                return None
            delay = base_delay * (2 ** i)  # 0.5, 1, 2, 4 s
            logger.warning(
                "CSV %s is locked (attempt %d/%d); retrying in %.1fs",
                path, i + 1, attempts, delay,
            )
            await asyncio.sleep(delay)


async def _track_results(q: asyncio.Queue, results_dir: Path, run_id: str) -> None:
    """Persist each pipeline result to the DB (O(1) per row) and append it to the
    per-run CSV. The CSV handle is opened once for the run's lifetime; a transient
    file lock retries with backoff and, if it never clears, degrades to DB-only
    writes rather than crashing the pipeline (the DB is the source of truth)."""
    db = get_db()
    csv_path = results_dir / "pipeline_results.csv"

    write_header = not csv_path.exists()
    f = await _open_csv_append(csv_path)
    writer = None
    if f is None:
        logger.error(
            "Could not open %s after retries; continuing with DB-only writes",
            csv_path,
        )
    else:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
            f.flush()

    try:
        while True:
            record = await q.get()
            if record is None:
                break

            await asyncio.to_thread(db.record_pipeline_result, run_id, record)

            if writer is not None:
                try:
                    writer.writerow(record)
                    f.flush()
                except OSError as exc:
                    logger.warning("Failed to append row to %s: %s", csv_path, exc)

            logger.info("Tracked result: %s — %s", record["company"], record["title"])
    finally:
        if f is not None:
            f.close()


async def _finalise_pipeline(run_id: str, results_dir: Path, started_at: str) -> None:
    """Export the run's pipeline results to JSON once from the DB (read by the
    /apply skill) and record the pipeline run's summary row."""
    db = get_db()
    rows = await asyncio.to_thread(db.load_pipeline_results, run_id)
    (results_dir / "pipeline_results.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    tuned = sum(1 for r in rows if r.get("tuner_status") == "tuned")
    await asyncio.to_thread(
        db.finalise_run, run_id, PHASE_PIPELINE, started_at, None,
        {"total_results": len(rows), "tuned_count": tuned},
    )


def _make_progress() -> Progress:
    """One Progress shared by every phase, rendered inside the single Live.

    A per-task `count_str` field carries the human-readable count so one column
    set renders both the determinate scrape bar and the count-up match/tune/apply
    bars (BarColumn auto-pulses whenever a task's total is None).
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.fields[count_str]}"),
        console=console,
    )


async def run_pipeline(
    skip_matcher: bool = False,
    skip_tuner: bool = False,
    skip_llm: bool = False,
    apply: bool = False,
) -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    started_at = datetime.now(timezone.utc).isoformat()

    results_dir = Path("data/pipeline") / run_id
    results_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("Pipeline starting — run %s", run_id)
    logger.info("=" * 60)

    q3: asyncio.Queue = asyncio.Queue()

    progress = _make_progress()
    tasks: dict[str, int] = {}          # phase → task id (only active phases get one)
    counts = {"match": 0, "tune": 0}    # streaming phases have no known total → count up

    def on_company_start(name: str, board: str, done: int, total: int) -> None:
        if "scrape" not in tasks:
            tasks["scrape"] = progress.add_task(
                "[bold]Scraping[/bold]", total=total, count_str=f"0/{total}"
            )
        progress.update(
            tasks["scrape"],
            total=total,
            completed=done,
            description=f"[bold]Scraping[/bold] ({board}) {name}",
            count_str=f"{done}/{total} ({total - done} left)",
        )

    def on_job_score(board_token: str, title: str) -> None:
        counts["match"] += 1
        progress.update(
            tasks["match"],
            description=f"[bold]Matching[/bold] {board_token} — {title[:45]}",
            count_str=f"{counts['match']} scored",
        )

    def on_tune(status: str, title: str, company: str) -> None:
        counts["tune"] += 1
        progress.update(
            tasks["tune"],
            description=f"[bold]Tuning[/bold] {company} — {title[:45]}",
            count_str=f"{counts['tune']} processed",
        )

    try:
        with Live(progress, console=console, refresh_per_second=4):
            if skip_matcher:
                await scraper.main(quiet=True, run_id=run_id, on_company_start=on_company_start)
                await q3.put(None)
                await _track_results(q3, results_dir, run_id)
            elif skip_tuner:
                tasks["match"] = progress.add_task("[bold]Matching[/bold]", total=None, count_str="0 scored")
                q1: asyncio.Queue = asyncio.Queue()
                q2: asyncio.Queue = asyncio.Queue()
                await asyncio.gather(
                    scraper.main(out_queue=q1, quiet=True, run_id=run_id, on_company_start=on_company_start),
                    matcher.main(in_queue=q1, out_queue=q2, quiet=True, run_id=run_id, skip_llm=skip_llm, on_job_score=on_job_score),
                    _bypass_tuner(q2, q3),
                    _track_results(q3, results_dir, run_id),
                )
            else:
                tasks["match"] = progress.add_task("[bold]Matching[/bold]", total=None, count_str="0 scored")
                tasks["tune"] = progress.add_task("[bold]Tuning[/bold]", total=None, count_str="0 processed")
                q1: asyncio.Queue = asyncio.Queue()
                q2: asyncio.Queue = asyncio.Queue()
                await asyncio.gather(
                    scraper.main(out_queue=q1, quiet=True, run_id=run_id, on_company_start=on_company_start),
                    matcher.main(in_queue=q1, out_queue=q2, quiet=True, run_id=run_id, skip_llm=skip_llm, on_job_score=on_job_score),
                    tuner.main(in_queue=q2, out_queue=q3, quiet=True, run_id=run_id, on_tune=on_tune),
                    _track_results(q3, results_dir, run_id),
                )

            await _finalise_pipeline(run_id, results_dir, started_at)

            # Apply runs inside the same Live so its bar shares this Progress —
            # never a second Live. Requires tuned resumes, so gated like before.
            if apply and not skip_tuner and not skip_matcher:
                apply_task = progress.add_task("[bold]Applying[/bold]", total=None, count_str="running")
                await _launch_apply()
                progress.update(apply_task, count_str="done")

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
        help="Force-skip the tuner (overrides config/tuner.yaml enable_tuner)",
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
        help="Force-enable the applier (overrides config/applier.yaml enable_applier)",
    )
    args = parser.parse_args()

    _setup_logging()

    # Phase toggles default from config (enable_tuner / enable_applier); the CLI
    # flags remain as explicit overrides so existing invocations keep working.
    from hireshire.applier.config import load_applier_config
    from hireshire.tuner.config import load_tuner_config

    skip_tuner = args.no_tuner or not load_tuner_config().settings.enable_tuner
    apply = args.apply or load_applier_config().settings.enable_applier

    interval_s = args.interval * 3600

    if not args.now and not args.once:
        logger.info("Orchestrator started — first run in %.1fh", args.interval)
        await asyncio.sleep(interval_s)

    while True:
        await run_pipeline(
            skip_matcher=args.no_matcher,
            skip_tuner=skip_tuner,
            skip_llm=args.no_llm,
            apply=apply,
        )
        if args.once:
            break
        logger.info("Next run in %.1fh", args.interval)
        await asyncio.sleep(interval_s)


if __name__ == "__main__":
    asyncio.run(main())
