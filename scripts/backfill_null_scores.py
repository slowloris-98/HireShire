"""
Null out the fabricated relevance_score on LLM-skipped match rows.

When `skip_llm` is on the matcher never calls the LLM, but historically it still
stamped every title-passing job with `relevance_score=100` — indistinguishable
from a genuine perfect score. The matcher now records `None` instead; this script
retrofits the same to rows written before that change.

`skip_reason='llm_skipped'` is the discriminator, so real LLM scores of 100 are
left alone. Both the `relevance_score` column and the `relevance_score` key
inside `matches.raw_json` are cleared — raw_json is the dashboard's actual read
path. Idempotent: re-running touches nothing.

    python scripts/backfill_null_scores.py --dry-run   # report only
    python scripts/backfill_null_scores.py
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Allow running as `python scripts/backfill_null_scores.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from hireshire.storage.db import DEFAULT_DB_PATH

console = Console()

# Each step: (label, count-what-remains SQL, update SQL). The count query is the
# exact complement of the update's WHERE clause, so it doubles as the dry-run
# estimate and the post-run idempotency check.
STEPS = [
    (
        "matches.relevance_score",
        "SELECT COUNT(*) FROM matches "
        "WHERE skip_reason='llm_skipped' AND relevance_score IS NOT NULL",
        "UPDATE matches SET relevance_score=NULL "
        "WHERE skip_reason='llm_skipped' AND relevance_score IS NOT NULL",
    ),
    (
        "matches.raw_json",
        "SELECT COUNT(*) FROM matches WHERE skip_reason='llm_skipped' "
        "AND json_extract(raw_json,'$.relevance_score') IS NOT NULL",
        "UPDATE matches SET raw_json=json_set(raw_json,'$.relevance_score',json('null')) "
        "WHERE skip_reason='llm_skipped' "
        "AND json_extract(raw_json,'$.relevance_score') IS NOT NULL",
    ),
    (
        "pipeline_results.relevance_score",
        "SELECT COUNT(*) FROM pipeline_results p WHERE p.relevance_score IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM matches m WHERE m.run_id=p.run_id "
        "AND m.job_id=p.job_id AND m.skip_reason='llm_skipped')",
        "UPDATE pipeline_results SET relevance_score=NULL "
        "WHERE relevance_score IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM matches m WHERE m.run_id=pipeline_results.run_id "
        "AND m.job_id=pipeline_results.job_id AND m.skip_reason='llm_skipped')",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Null the fabricated relevance_score on llm_skipped rows"
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Report only; make no changes")
    args = parser.parse_args()

    if not Path(args.db).exists():
        console.print(f"[red]Database not found: {args.db}[/red]")
        raise SystemExit(1)

    conn = sqlite3.connect(args.db)
    try:
        try:
            conn.execute("SELECT json_set('{}','$.a',json('null'))")
        except sqlite3.OperationalError:
            console.print("[red]This SQLite build lacks the json1 extension — cannot rewrite raw_json.[/red]")
            raise SystemExit(1)

        pending = [(label, conn.execute(count_sql).fetchone()[0], update_sql)
                   for label, count_sql, update_sql in STEPS]

        if not any(n for _, n, _ in pending):
            console.print("[green]Nothing to backfill — all llm_skipped rows already have a null score.[/green]")
            return

        for label, n, _ in pending:
            console.print(f"  {label}: [cyan]{n}[/cyan] row(s)")

        if args.dry_run:
            console.print("\n[yellow]Dry run[/yellow] — no changes written.")
            return

        with conn:  # single transaction; rolls back entirely on error
            for _, _, update_sql in pending:
                conn.execute(update_sql)

        remaining = sum(conn.execute(c).fetchone()[0] for _, c, _ in STEPS)
        if remaining:
            console.print(f"\n[red]{remaining} row(s) still carry a score — backfill incomplete.[/red]")
            raise SystemExit(1)
        console.print(f"\n[green]Backfill complete[/green] — {args.db}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
