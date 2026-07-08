"""
Manually prune old run history from the shared SQLite datastore.

Deletes run-scoped rows (runs, run_companies, jobs, matches, pipeline_results,
tuned_jobs) for old runs and removes the matching on-disk artifact directories
(data/tuned/<run_id>/, data/pipeline/<run_id>/). Cross-run tables (seen_jobs,
applied) are never touched. Nothing runs automatically — use this when you want
to reclaim space.

    python scripts/prune_runs.py --keep 10            # keep the 10 most recent runs
    python scripts/prune_runs.py --before 2026-06-01  # drop runs older than a date
    python scripts/prune_runs.py --keep 10 --dry-run  # show what would be deleted
"""

import argparse
import shutil
import sys
from pathlib import Path

# Allow running as `python scripts/prune_runs.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from hireshire.storage.db import DEFAULT_DB_PATH, get_db

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old run history from the HireShire DB")
    parser.add_argument("--keep", type=int, help="Retain the N most-recent runs")
    parser.add_argument("--before", metavar="YYYY-MM-DD",
                        help="Delete runs whose run_id sorts before this date")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Report only; make no changes")
    args = parser.parse_args()

    if args.keep is None and args.before is None:
        parser.error("provide --keep and/or --before")

    db = get_db(args.db)

    if args.dry_run:
        run_ids = db.all_run_ids()
        doomed = []
        if args.keep is not None:
            doomed.extend(run_ids[args.keep:])
        if args.before is not None:
            doomed.extend(r for r in run_ids if r < args.before)
        doomed = sorted(set(doomed))
        console.print(f"[yellow]Dry run[/yellow] — would delete {len(doomed)} run(s):")
        for rid in doomed:
            console.print(f"  [dim]{rid}[/dim]")
        return

    deleted = db.prune_runs(keep=args.keep, before=args.before)
    if not deleted:
        console.print("[green]Nothing to prune.[/green]")
        return

    for rid in deleted:
        for base in ("data/tuned", "data/pipeline"):
            run_dir = Path(base) / rid
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)

    console.print(f"[green]Pruned {len(deleted)} run(s)[/green] from {args.db} and disk.")
    for rid in deleted:
        console.print(f"  [dim]{rid}[/dim]")


if __name__ == "__main__":
    main()
