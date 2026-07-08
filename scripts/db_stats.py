"""
Quick health/inspection view of the shared SQLite datastore: every table with its
row count, plus the latest finalised run per phase. Read-only.

    python scripts/db_stats.py                 # all tables + latest run per phase
    python scripts/db_stats.py --run <run_id>  # also show per-table counts for one run
    python scripts/db_stats.py --db path.db     # inspect a different database file
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running as `python scripts/db_stats.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from hireshire.storage.db import (
    DEFAULT_DB_PATH,
    PHASE_MATCH,
    PHASE_PIPELINE,
    PHASE_SCRAPE,
    PHASE_TUNE,
)

console = Console()

PHASES = [PHASE_SCRAPE, PHASE_MATCH, PHASE_TUNE, PHASE_PIPELINE]
# Tables scoped to a single run (carry a run_id column).
RUN_SCOPED = {"runs", "run_companies", "jobs", "matches", "pipeline_results", "tuned_jobs"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show HireShire DB tables, row counts, and latest run per phase"
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument("--run", help="Also show per-table row counts for this run_id")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        console.print(
            f"[yellow]No database at[/yellow] {db_path} - run a phase first "
            "(e.g. [cyan]python scraper.py[/cyan])."
        )
        return

    # Read-only connection so this never creates/writes the DB.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]

    # --- Tables + row counts ---
    counts = Table(title=f"Tables - {db_path}")
    counts.add_column("Table", style="bold")
    counts.add_column("Rows", justify="right")
    if args.run:
        counts.add_column(f"Rows in run", justify="right")
    for name in tables:
        total = conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        row = [name, f"{total:,}"]
        if args.run:
            if name in RUN_SCOPED:
                n = conn.execute(
                    f'SELECT count(*) FROM "{name}" WHERE run_id=?', (args.run,)
                ).fetchone()[0]
                row.append(f"{n:,}")
            else:
                row.append("[dim]-[/dim]")
        counts.add_row(*row)
    console.print(counts)

    # --- Latest finalised run per phase ---
    latest = Table(title="Latest finalised run per phase")
    latest.add_column("Phase", style="bold")
    latest.add_column("run_id", style="cyan")
    latest.add_column("finished_at")
    any_runs = False
    for phase in PHASES:
        r = conn.execute(
            "SELECT run_id, finished_at FROM runs WHERE phase=? "
            "ORDER BY started_at DESC LIMIT 1",
            (phase,),
        ).fetchone()
        if r:
            any_runs = True
            latest.add_row(phase, r["run_id"], r["finished_at"] or "[dim]-[/dim]")
        else:
            latest.add_row(phase, "[dim]none[/dim]", "[dim]-[/dim]")
    console.print(latest)

    if not any_runs:
        has_data = any(
            conn.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone()
            for t in ("jobs", "matches", "pipeline_results") if t in tables
        )
        if has_data:
            console.print(
                "[yellow]No finalised runs, but data is present[/yellow] - a run is in "
                "progress or was interrupted before finalising. `db.latest_run()` returns "
                "None until a run completes cleanly, so standalone matcher/tuner/applier "
                "won't pick this data up."
            )

    conn.close()


if __name__ == "__main__":
    main()
