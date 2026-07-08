"""
One-time backfill: load legacy per-phase JSON archives into the shared SQLite DB.

The storage layer was migrated to `data/hireshire.db` but the historical data on
disk (data/seen_jobs.json, data/scraped/<run>/, data/matches/<run>/,
data/applied/applied.json) was never imported. This script imports all four,
preserving full per-run history (one DB run per JSON run dir). It is idempotent:
rows use INSERT OR REPLACE, so re-running overwrites rather than duplicates.

    python scripts/backfill_from_json.py --dry-run   # parse + count, write nothing
    python scripts/backfill_from_json.py             # backfill everything (default --all)
    python scripts/backfill_from_json.py --seen       # only one phase
    python scripts/backfill_from_json.py --scraped --matches
    python scripts/backfill_from_json.py --data-dir data --db-path data/hireshire.db
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Allow running as `python scripts/backfill_from_json.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from hireshire.matcher.scorer import MatchResult
from hireshire.models.job import Job
from hireshire.storage.db import (
    DEFAULT_DB_PATH,
    PHASE_MATCH,
    PHASE_SCRAPE,
    Database,
    get_db,
)

console = Console()

# ISO-timestamp run directory, e.g. 2026-05-29T23-24-43Z. Skips stray files
# (test.json) and any loose non-run directories.
RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")


def run_id_to_iso(run_id: str) -> str:
    """`2026-05-29T23-24-43Z` -> `2026-05-29T23:24:43+00:00` (fallback timestamp
    when a manifest is missing or lacks started_at)."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})Z$", run_id)
    if not m:
        return run_id
    date, hh, mm, ss = m.groups()
    return f"{date}T{hh}:{mm}:{ss}+00:00"


def _run_dirs(base: Path) -> list[Path]:
    if not base.exists():
        return []
    return sorted(d for d in base.iterdir() if d.is_dir() and RUN_DIR_RE.match(d.name))


def _load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 1. seen jobs
# ---------------------------------------------------------------------------

def backfill_seen(db: Database, data_dir: Path, dry_run: bool) -> None:
    path = data_dir / "seen_jobs.json"
    if not path.exists():
        console.print(f"[yellow]seen:[/yellow] {path} not found — skipping")
        return
    raw = _load_json(path)
    ids = list(raw.keys()) if isinstance(raw, dict) else list(raw)
    ids = [str(i) for i in ids]
    console.print(f"[bold]seen:[/bold] {len(ids):,} job IDs from {path.name}")
    if not dry_run:
        db.mark_seen(ids)
    console.print(f"  [green]{'would mark' if dry_run else 'marked'} {len(ids):,} seen[/green]")


# ---------------------------------------------------------------------------
# 2. scraped
# ---------------------------------------------------------------------------

def backfill_scraped(db: Database, data_dir: Path, dry_run: bool) -> None:
    base = data_dir / "scraped"
    run_dirs = _run_dirs(base)
    console.print(f"[bold]scraped:[/bold] {len(run_dirs)} run dir(s) under {base}")

    total_jobs = 0
    total_malformed = 0
    for rd in run_dirs:
        run_id = rd.name
        manifest = {}
        mpath = rd / "manifest.json"
        if mpath.exists():
            try:
                manifest = _load_json(mpath)
            except Exception as exc:  # noqa: BLE001
                console.print(f"  [yellow]{run_id}: bad manifest ({exc})[/yellow]")

        jobs: list[Job] = []
        platform_by_token: dict[str, str] = {}
        malformed = 0
        for cfile in rd.glob("*.json"):
            if cfile.name == "manifest.json":
                continue
            try:
                records = _load_json(cfile)
            except Exception:  # noqa: BLE001
                malformed += 1
                continue
            for d in records or []:
                try:
                    job = Job(**d)
                    jobs.append(job)
                    platform_by_token.setdefault(job.board_token, job.source)
                except Exception:  # noqa: BLE001
                    malformed += 1

        # run_companies: manifest is authoritative; enrich platform from loaded jobs.
        companies: list[dict] = []
        man_companies = manifest.get("companies", {}) if isinstance(manifest, dict) else {}
        seen_tokens = set()
        for token, meta in man_companies.items():
            meta = meta or {}
            companies.append({
                "board_token": token,
                "platform": platform_by_token.get(token),
                "status": meta.get("status"),
                "job_count": meta.get("job_count", 0),
                "error": meta.get("error"),
            })
            seen_tokens.add(token)
        # Any company file present but absent from the manifest.
        for token in platform_by_token:
            if token not in seen_tokens:
                companies.append({
                    "board_token": token,
                    "platform": platform_by_token[token],
                    "status": "ok",
                    "job_count": sum(1 for j in jobs if j.board_token == token),
                })

        total_jobs += len(jobs)
        total_malformed += malformed

        if not dry_run:
            db.record_companies(run_id, companies)
            db.insert_jobs(run_id, jobs)
            started_at = (manifest.get("started_at") if isinstance(manifest, dict) else None) \
                or run_id_to_iso(run_id)
            finished_at = manifest.get("finished_at") if isinstance(manifest, dict) else None
            stats = {k: v for k, v in (manifest or {}).items()
                     if k not in ("companies", "started_at", "finished_at")}
            db.finalise_run(run_id, PHASE_SCRAPE, started_at, finished_at, stats)

        note = f" ({malformed} malformed skipped)" if malformed else ""
        console.print(
            f"  [dim]{run_id}[/dim]: {len(jobs):,} jobs, {len(companies)} companies{note}"
        )

    console.print(
        f"  [green]{'would import' if dry_run else 'imported'} {total_jobs:,} jobs "
        f"across {len(run_dirs)} runs[/green]"
        + (f" [yellow]({total_malformed} malformed skipped)[/yellow]" if total_malformed else "")
    )


# ---------------------------------------------------------------------------
# 3. matches
# ---------------------------------------------------------------------------

def _import_match_file(db: Database, run_id: str, path: Path, shortlisted: bool,
                       dry_run: bool) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    try:
        records = _load_json(path)
    except Exception:  # noqa: BLE001
        return 0, 0
    ok = bad = 0
    for d in records or []:
        try:
            result = MatchResult.model_validate(d)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        if not dry_run:
            db.upsert_match(
                run_id,
                result.job_id,
                result.board_token,
                result.title,
                result.relevance_score,
                shortlisted,
                result.skipped,
                result.skip_reason,
                result.source_run_id,
                result.scored_at.isoformat(),
                result.model_dump_json(),
            )
        ok += 1
    return ok, bad


def backfill_matches(db: Database, data_dir: Path, dry_run: bool) -> None:
    base = data_dir / "matches"
    run_dirs = _run_dirs(base)
    console.print(f"[bold]matches:[/bold] {len(run_dirs)} run dir(s) under {base}")

    total_ok = 0
    total_bad = 0
    for rd in run_dirs:
        run_id = rd.name
        manifest = {}
        mpath = rd / "manifest.json"
        if mpath.exists():
            try:
                manifest = _load_json(mpath)
            except Exception:  # noqa: BLE001
                pass

        s_ok, s_bad = _import_match_file(db, run_id, rd / "shortlisted.json", True, dry_run)
        r_ok, r_bad = _import_match_file(db, run_id, rd / "rejected.json", False, dry_run)
        ok, bad = s_ok + r_ok, s_bad + r_bad
        total_ok += ok
        total_bad += bad

        if not dry_run and ok:
            started_at = (manifest.get("started_at") if isinstance(manifest, dict) else None) \
                or run_id_to_iso(run_id)
            finished_at = manifest.get("finished_at") if isinstance(manifest, dict) else None
            stats = {k: v for k, v in (manifest or {}).items()
                     if k not in ("started_at", "finished_at", "run_id")}
            db.finalise_run(run_id, PHASE_MATCH, started_at, finished_at, stats)

        note = f" ({bad} malformed skipped)" if bad else ""
        console.print(
            f"  [dim]{run_id}[/dim]: {s_ok} shortlisted, {r_ok} rejected{note}"
        )

    console.print(
        f"  [green]{'would import' if dry_run else 'imported'} {total_ok:,} match rows "
        f"across {len(run_dirs)} runs[/green]"
        + (f" [yellow]({total_bad} malformed skipped)[/yellow]" if total_bad else "")
    )


# ---------------------------------------------------------------------------
# 4. applied
# ---------------------------------------------------------------------------

def backfill_applied(db: Database, data_dir: Path, dry_run: bool) -> None:
    path = data_dir / "applied" / "applied.json"
    if not path.exists():
        console.print(f"[yellow]applied:[/yellow] {path} not found — skipping")
        return
    records = _load_json(path)
    console.print(f"[bold]applied:[/bold] {len(records)} record(s) from {path.name}")
    bad = 0
    for d in records or []:
        jid = d.get("job_id")
        if not jid:
            bad += 1
            continue
        if not dry_run:
            db.record_applied(
                jid,
                d.get("board_token"),
                d.get("title"),
                d.get("absolute_url"),
                d.get("applied_at"),
                d.get("status"),
                bool(d.get("dry_run", False)),
                d.get("screenshot"),
                d.get("error"),
            )
    n = len(records) - bad
    console.print(
        f"  [green]{'would import' if dry_run else 'imported'} {n} applied[/green]"
        + (f" [yellow]({bad} skipped, no job_id)[/yellow]" if bad else "")
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill legacy JSON archives into the HireShire DB")
    parser.add_argument("--all", action="store_true", help="Backfill every phase (default)")
    parser.add_argument("--seen", action="store_true", help="Backfill seen_jobs")
    parser.add_argument("--scraped", action="store_true", help="Backfill scraped jobs + companies")
    parser.add_argument("--matches", action="store_true", help="Backfill matcher results")
    parser.add_argument("--applied", action="store_true", help="Backfill applied records")
    parser.add_argument("--data-dir", default="data", help="Root of the legacy JSON archives")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to the SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Parse + count; write nothing")
    args = parser.parse_args()

    # No explicit phase flags -> do everything.
    do_all = args.all or not (args.seen or args.scraped or args.matches or args.applied)
    data_dir = Path(args.data_dir)
    db = get_db(args.db_path)

    if args.dry_run:
        console.print("[yellow]DRY RUN — no writes[/yellow]\n")

    # User's stated order: seen -> matches -> applied -> scraped.
    if do_all or args.seen:
        backfill_seen(db, data_dir, args.dry_run)
    if do_all or args.matches:
        backfill_matches(db, data_dir, args.dry_run)
    if do_all or args.applied:
        backfill_applied(db, data_dir, args.dry_run)
    if do_all or args.scraped:
        backfill_scraped(db, data_dir, args.dry_run)

    console.print(f"\n[bold green]Backfill {'rehearsed' if args.dry_run else 'complete'}.[/bold green]")


if __name__ == "__main__":
    main()
