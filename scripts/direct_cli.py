"""
Ingest jobs scraped from direct career portals (Google, Microsoft, Meta, Intuit,
Apple) into the shared SQLite datastore. Used by the `/scrape-direct` Claude Code
skill so it writes to the same `jobs` table as the API scrapers and never opens
the database itself.

    python scripts/direct_cli.py new-run                        # print a fresh run_id, create its staging dir
    python scripts/direct_cli.py normalize --run-id <id>        # raw/*.json -> staged <company>.json
    python scripts/direct_cli.py ingest --run-id <id>           # orchestrator path (run is finalised by scraper.py)
    python scripts/direct_cli.py ingest --run-id <id> --finalise  # standalone path
    python scripts/direct_cli.py show --run-id <id>             # what is staged, without writing

The skill stages one JSON array per company at
`data/direct/<run_id>/<company>.json`; see `hireshire/direct/staging.py` for the
record schema.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python scripts/direct_cli.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from rich.console import Console

from hireshire.direct.config import load_direct_config
from hireshire.direct.normalize import normalize_records
from hireshire.direct.staging import SOURCE, load_staged, staging_path
from hireshire.storage.db import PHASE_SCRAPE, get_db

console = Console()

RUN_ID_FMT = "%Y-%m-%dT%H-%M-%SZ"


def _run_id_to_iso(run_id: str) -> str:
    """Recover the run's start time from its id, for finalise_run's started_at."""
    try:
        return datetime.strptime(run_id, RUN_ID_FMT).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def cmd_new_run(args, cfg) -> None:
    run_id = datetime.now(timezone.utc).strftime(RUN_ID_FMT)
    staging_path(run_id, cfg.settings.staging_dir).mkdir(parents=True, exist_ok=True)
    # Bare stdout: the skill captures this to use as the run_id.
    print(run_id)


def _scraper_filters() -> tuple[list[str], int | None]:
    """Age + location filters, read from the scraper's own config so the direct
    boards obey exactly the same window as the API boards."""
    try:
        raw = yaml.safe_load(Path("config/scraper.yaml").read_text(encoding="utf-8")) or {}
        settings = raw.get("settings") or {}
        return list(settings.get("location_filter") or []), settings.get("max_age_hours")
    except OSError:
        return [], None


def cmd_normalize(args, cfg) -> None:
    """Raw browser output -> staged records.

    The skill writes whatever its JS extractor found to `raw/<company>.json`
    without interpreting it; every filtering decision happens here, in code.
    """
    run_dir = staging_path(args.run_id, cfg.settings.staging_dir)
    raw_dir = run_dir / "raw"
    if not raw_dir.is_dir():
        console.print(f"[yellow]No raw extractions at {raw_dir} — nothing to normalize.[/yellow]")
        return

    location_terms, max_age_hours = _scraper_filters()
    console.print(
        f"[bold]Normalizing raw extractions — run {args.run_id}[/bold]  "
        f"[dim](max_age_hours={max_age_hours}, location_filter={location_terms or 'none'})[/dim]"
    )

    total_in = total_out = 0
    for path in sorted(raw_dir.glob("*.json")):
        company = path.stem
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"  [red]{company:<12} unreadable: {exc}[/red]")
            continue
        if not isinstance(records, list):
            console.print(f"  [red]{company:<12} not a JSON array[/red]")
            continue

        staged, dropped = normalize_records(
            records, location_terms=location_terms, max_age_hours=max_age_hours
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / f"{company}.json").write_text(
            json.dumps(staged, indent=1, ensure_ascii=False), encoding="utf-8"
        )

        total_in += len(records)
        total_out += len(staged)
        note = (
            "  [dim](dropped " + ", ".join(f"{v} {k}" for k, v in dropped.items()) + ")[/dim]"
            if dropped else ""
        )
        console.print(f"  {company:<12} {len(records):>4} raw -> [green]{len(staged):>4}[/green] staged{note}")

    console.print(f"[bold green]Staged {total_out} of {total_in} raw records[/bold green]")


def cmd_show(args, cfg) -> None:
    jobs_by_company, malformed = load_staged(args.run_id, cfg.settings.staging_dir)
    if not jobs_by_company:
        console.print(f"[yellow]Nothing staged at {staging_path(args.run_id, cfg.settings.staging_dir)}[/yellow]")
        return
    for company, jobs in sorted(jobs_by_company.items()):
        bad = malformed.get(company, 0)
        suffix = f" [red]({bad} malformed)[/red]" if bad else ""
        console.print(f"  {company:<12} {len(jobs):>4} jobs{suffix}")


def cmd_ingest(args, cfg) -> None:
    db = get_db(args.db or cfg.settings.db_path)
    jobs_by_company, malformed = load_staged(args.run_id, cfg.settings.staging_dir)

    if not jobs_by_company:
        console.print(
            f"[yellow]Nothing staged for run {args.run_id} at "
            f"{staging_path(args.run_id, cfg.settings.staging_dir)} — nothing to ingest.[/yellow]"
        )
        return

    console.print(f"[bold]Ingesting direct-portal jobs — run {args.run_id}[/bold]")

    total_jobs = 0
    total_bad = 0
    companies_with_jobs = 0

    for company, jobs in sorted(jobs_by_company.items()):
        bad = malformed.get(company, 0)
        total_bad += bad

        # Every company gets a run_companies row, including zero-job ones — the
        # same contract RunStore.save_company honours for the API scrapers.
        if not args.dry_run:
            db.record_company(args.run_id, company, SOURCE, "ok", len(jobs), None, None)
            if jobs:
                db.insert_jobs(args.run_id, jobs)

        total_jobs += len(jobs)
        if jobs:
            companies_with_jobs += 1

        note = f" [red]({bad} malformed, skipped)[/red]" if bad else ""
        console.print(f"  {company:<12} [green]{len(jobs):>4}[/green] jobs{note}")

    if args.finalise and not args.dry_run:
        # Standalone only. Under the orchestrator, scraper.main finalises this
        # same run_id itself and calling it here too would clobber its stats.
        db.finalise_run(
            args.run_id, PHASE_SCRAPE, _run_id_to_iso(args.run_id), None,
            {
                "total_jobs": total_jobs,
                "companies": len(jobs_by_company),
                "companies_with_jobs": companies_with_jobs,
                "errors": total_bad,
                "direct": True,
            },
        )
        console.print(f"[dim]Finalised run {args.run_id} as a {PHASE_SCRAPE} run.[/dim]")

    verb = "Would ingest" if args.dry_run else "Ingested"
    console.print(
        f"[bold green]{verb} {total_jobs} jobs[/bold green] across "
        f"{len(jobs_by_company)} companies"
        + (f" ([red]{total_bad} malformed records skipped[/red])" if total_bad else "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest direct career-portal jobs into the HireShire DB"
    )
    parser.add_argument("--config", default=None, help="Path to config/direct_boards.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("new-run", help="Print a fresh run_id and create its staging dir")

    norm = sub.add_parser(
        "normalize",
        help="Convert raw/<company>.json browser output into staged <company>.json",
    )
    norm.add_argument("--run-id", required=True)

    show = sub.add_parser("show", help="List what is staged for a run without writing")
    show.add_argument("--run-id", required=True)

    ing = sub.add_parser("ingest", help="Write staged jobs into the jobs table")
    ing.add_argument("--run-id", required=True)
    ing.add_argument("--db", default=None, help="Override the configured db_path")
    ing.add_argument(
        "--finalise", action="store_true",
        help="Also finalise the run as a scrape run. Standalone use only — the "
             "orchestrator lets scraper.py finalise the shared run.",
    )
    ing.add_argument("--dry-run", action="store_true", help="Report only; write nothing")

    args = parser.parse_args()
    cfg = load_direct_config(args.config) if args.config else load_direct_config()

    {
        "new-run": cmd_new_run,
        "normalize": cmd_normalize,
        "show": cmd_show,
        "ingest": cmd_ingest,
    }[args.command](args, cfg)


if __name__ == "__main__":
    main()
