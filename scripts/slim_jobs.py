"""
One-time migration: strip the never-read `content_html` and the duplicated
`content_text` out of every `jobs.raw_json` blob, then VACUUM to reclaim disk.

The description is kept in its own `content_text` column (the single source of
truth); raw_json only needs the small structured leftovers (title, url, location,
questions, ids, dates). This shrinks a backfilled DB from ~14 GB to ~4-5 GB.

Idempotent: json_remove on a blob already lacking those keys is a no-op. Never
touches the legacy company-wise JSON archives.

    python scripts/slim_jobs.py --dry-run   # report projected savings, write nothing
    python scripts/slim_jobs.py             # rewrite raw_json + VACUUM
    python scripts/slim_jobs.py --db path   # target a different database file
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running as `python scripts/slim_jobs.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from hireshire.storage.db import DEFAULT_DB_PATH

console = Console()

STRIP = "json_remove(raw_json, '$.content_html', '$.content_text')"


def _gb(nbytes: float) -> str:
    return f"{nbytes / 1e9:.2f} GB"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strip content_html + duplicate content_text from jobs.raw_json and VACUUM"
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Report projected savings; write nothing")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        console.print(f"[yellow]No database at[/yellow] {db_path}")
        return

    file_before = db_path.stat().st_size
    console.print(f"[bold]Database:[/bold] {db_path}  ({_gb(file_before)} on disk)")

    if args.dry_run:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows, cur_raw, new_raw = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(LENGTH(raw_json)),0), "
            f"COALESCE(SUM(LENGTH({STRIP})),0) FROM jobs"
        ).fetchone()
        conn.close()
        console.print(f"  jobs rows: {rows:,}")
        console.print(f"  raw_json total: {_gb(cur_raw)} -> {_gb(new_raw)} "
                      f"([green]-{_gb(cur_raw - new_raw)}[/green])")
        console.print("[yellow]Dry run — nothing written.[/yellow] "
                      "Actual file shrink is realised by VACUUM in a real run.")
        return

    # Real run: autocommit so VACUUM (which cannot run inside a transaction) works.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    console.print(f"  rewriting raw_json for {rows:,} job rows…")
    conn.execute(f"UPDATE jobs SET raw_json = {STRIP}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    console.print("  VACUUM…")
    conn.execute("VACUUM")
    conn.close()

    file_after = db_path.stat().st_size
    console.print(
        f"[green]Done.[/green] {_gb(file_before)} -> {_gb(file_after)} "
        f"([green]-{_gb(file_before - file_after)}[/green]), {rows:,} rows unchanged."
    )


if __name__ == "__main__":
    main()
