"""Safely reset one interrupted pipeline run for a fresh retry.

Defaults to a dry run. The destructive mode requires explicit flags because it
also clears cross-run dedupe state and can optionally delete application records.

    python scripts/cleanup_run.py --run-id 2026-09-14T22-51-43Z
    python scripts/cleanup_run.py --run-id 2026-09-14T22-51-43Z \
        --apply --include-applied --allow-incomplete
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from rich.console import Console

# Allow running as `python scripts/cleanup_run.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hireshire.storage.db import DEFAULT_DB_PATH, get_db


console = Console()
_ARTIFACT_BASES = (Path("data/direct"), Path("data/pipeline"), Path("data/tuned"))


def _artifact_dirs(run_id: str) -> list[Path]:
    """Return only artifact paths directly beneath the approved run roots."""
    targets: list[Path] = []
    for base in _ARTIFACT_BASES:
        resolved_base = base.resolve()
        target = (resolved_base / run_id).resolve()
        if target.parent != resolved_base:
            raise ValueError(f"Unsafe run ID: {run_id!r}")
        if target.is_dir():
            targets.append(target)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset one HireShire pipeline run (dry-run by default)")
    parser.add_argument("--run-id", required=True, help="Exact pipeline run ID to reset")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument("--apply", action="store_true", help="Perform the cleanup; otherwise report only")
    parser.add_argument("--include-applied", action="store_true",
                        help="Also delete applied records for the run's job IDs")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="Allow cleanup when the run has an unfinished phase row")
    args = parser.parse_args()

    if Path(args.run_id).name != args.run_id:
        parser.error("--run-id must be a single run directory name")

    db = get_db(args.db)
    incomplete = db.unfinished_run_phases(args.run_id)
    if incomplete and not args.allow_incomplete:
        parser.error(
            f"run {args.run_id} has unfinished phase(s): {', '.join(incomplete)}; "
            "re-run with --allow-incomplete after confirming it is stopped"
        )

    counts = db.cleanup_run_preview(args.run_id)
    artifact_dirs = _artifact_dirs(args.run_id)
    console.print(f"[bold]{'Apply' if args.apply else 'Dry run'}[/bold] for [cyan]{args.run_id}[/cyan]")
    for table, count in counts.items():
        effective_count = count if table != "applied" or args.include_applied else 0
        suffix = " (preserved; pass --include-applied to delete)" if table == "applied" and not args.include_applied else ""
        console.print(f"  {table}: {effective_count}{suffix}")
    console.print(f"  artifact directories: {len(artifact_dirs)}")
    for directory in artifact_dirs:
        console.print(f"    {directory}")

    if not args.apply:
        return 0

    deleted = db.cleanup_run(args.run_id, include_applied=args.include_applied)
    for directory in artifact_dirs:
        shutil.rmtree(directory)
    console.print(f"[green]Reset {args.run_id}[/green]")
    console.print(", ".join(f"{table}={count}" for table, count in deleted.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
