"""Read-only data access + shared singletons for the dashboard.

`ReadDB` opens its own sqlite connection with `PRAGMA query_only=ON`, so the
dashboard can never write to the 5.8 GB datastore and (thanks to WAL) never
blocks or is blocked by the pipeline's writer. It reuses the same tables as
`hireshire.storage.db` but exposes only the SELECTs the UI + chat agent need.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

from hireshire.storage.db import (
    PHASE_MATCH,
    PHASE_PIPELINE,
    PHASE_SCRAPE,
    PHASE_TUNE,
)
from hireshire.webapp.config import FrontendConfig, load_frontend_config

PHASES = [PHASE_SCRAPE, PHASE_MATCH, PHASE_TUNE, PHASE_PIPELINE]


class ReadDB:
    """Thin read-only wrapper over the HireShire SQLite datastore."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # query_only guarantees the dashboard can't mutate the datastore; WAL lets
        # us read concurrently with the pipeline writer without lock contention.
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        cur = self._conn.cursor()
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA query_only=ON")

    def _q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -- runs ----------------------------------------------------------------

    def latest_run(self, phase: str) -> str | None:
        rows = self._q(
            "SELECT run_id FROM runs WHERE phase=? ORDER BY started_at DESC LIMIT 1",
            (phase,),
        )
        return rows[0]["run_id"] if rows else None

    def all_run_ids(self) -> list[str]:
        rows = self._q(
            "SELECT run_id, MAX(started_at) AS ts FROM runs "
            "GROUP BY run_id ORDER BY ts DESC"
        )
        return [r["run_id"] for r in rows]

    def latest_runs_by_phase(self) -> dict[str, str | None]:
        return {phase: self.latest_run(phase) for phase in PHASES}

    def run_summary(self, run_id: str) -> list[dict]:
        rows = self._q(
            "SELECT phase, started_at, finished_at, stats_json FROM runs WHERE run_id=?",
            (run_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["stats"] = json.loads(d.pop("stats_json") or "{}")
            except Exception:
                d["stats"] = {}
            out.append(d)
        return out

    # -- pipeline / matches --------------------------------------------------

    def load_pipeline_results(self, run_id: str) -> list[dict]:
        rows = self._q(
            "SELECT job_id, company, title, job_url, relevance_score, resume_tex, "
            "resume_pdf, tuner_status, tuner_run_id, processed_at "
            "FROM pipeline_results WHERE run_id=? ORDER BY relevance_score DESC",
            (run_id,),
        )
        return [dict(r) for r in rows]

    def load_pipeline_results_all(self) -> list[dict]:
        """Every job across every run, collapsed to one row per job.

        A recurring job is kept from its *most informative* run rather than simply
        its newest: a later skip-LLM/no-tuner run would otherwise mask a real score
        and a tuned PDF earned in an earlier one. Priority is has-resume, then
        has-score, then newest (`run_id` is a sortable ISO timestamp). Every field
        comes from that one run, so `run_id` still addresses the resume PDF.

        The matches join supplies `location`, which pipeline_results has no column
        for — the alternative source, the `jobs` table, is multi-GB and has no index
        on job_id alone, so a cross-run lookup there would scan it. The join here
        uses the matches PK (run_id, job_id).
        """
        rows = self._q(
            "SELECT p.run_id, p.job_id, p.company, p.title, p.job_url, "
            "p.relevance_score, p.resume_tex, p.resume_pdf, p.tuner_status, "
            "p.processed_at, m.raw_json "
            "FROM (SELECT *, ROW_NUMBER() OVER ("
            "        PARTITION BY job_id ORDER BY (resume_pdf IS NOT NULL) DESC, "
            "        (relevance_score IS NOT NULL) DESC, run_id DESC) AS rn "
            "      FROM pipeline_results) p "
            "LEFT JOIN matches m ON m.run_id = p.run_id AND m.job_id = p.job_id "
            "WHERE p.rn = 1"
        )
        out = []
        for r in rows:
            d = dict(r)
            raw = d.pop("raw_json", None)
            try:
                d["location"] = json.loads(raw).get("location") if raw else None
            except Exception:
                d["location"] = None
            out.append(d)
        return out

    def load_shortlisted(self, run_id: str) -> list[dict]:
        rows = self._q(
            "SELECT raw_json FROM matches WHERE run_id=? AND shortlisted=1 "
            "ORDER BY relevance_score DESC",
            (run_id,),
        )
        return [json.loads(r["raw_json"]) for r in rows]

    def load_matches(self, run_id: str) -> list[dict]:
        rows = self._q("SELECT raw_json FROM matches WHERE run_id=?", (run_id,))
        return [json.loads(r["raw_json"]) for r in rows]

    # -- applied -------------------------------------------------------------

    def applied_ids(self) -> set[str]:
        return {r["job_id"] for r in self._q("SELECT job_id FROM applied")}

    def applied_by_id(self) -> dict[str, dict]:
        rows = self._q(
            "SELECT job_id, status, dry_run, applied_at, absolute_url FROM applied"
        )
        out: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            d["dry_run"] = bool(d["dry_run"])
            out[d["job_id"]] = d
        return out

    def load_applied(self) -> list[dict]:
        rows = self._q(
            "SELECT job_id, board_token, title, absolute_url, applied_at, status, "
            "dry_run, screenshot, error FROM applied ORDER BY applied_at DESC"
        )
        out = []
        for r in rows:
            d = dict(r)
            d["dry_run"] = bool(d["dry_run"])
            out.append(d)
        return out

    # -- tuned ---------------------------------------------------------------

    def job_locations(self, run_id: str) -> dict[str, str]:
        rows = self._q(
            "SELECT job_id, location FROM jobs WHERE run_id=?", (run_id,)
        )
        return {r["job_id"]: r["location"] for r in rows}

    def tuned_paths(self, run_id: str, job_id: str) -> dict | None:
        rows = self._q(
            "SELECT resume_tex_path, resume_pdf_path, status FROM tuned_jobs "
            "WHERE run_id=? AND job_id=?",
            (run_id, job_id),
        )
        return dict(rows[0]) if rows else None

    # -- stats (for the chat run_stats tool) ---------------------------------

    def run_counts(self, run_id: str) -> dict:
        def n(sql: str) -> int:
            return self._q(sql, (run_id,))[0][0]

        return {
            "run_id": run_id,
            "jobs": n("SELECT COUNT(*) FROM jobs WHERE run_id=?"),
            "matches": n("SELECT COUNT(*) FROM matches WHERE run_id=?"),
            "shortlisted": n(
                "SELECT COUNT(*) FROM matches WHERE run_id=? AND shortlisted=1"
            ),
            "tuned": n(
                "SELECT COUNT(*) FROM tuned_jobs WHERE run_id=? AND status='tuned'"
            ),
            "pipeline_rows": n("SELECT COUNT(*) FROM pipeline_results WHERE run_id=?"),
        }


# ---------------------------------------------------------------------------
# Process-wide singletons.
# ---------------------------------------------------------------------------

_READDB: ReadDB | None = None
_READDB_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def get_settings() -> FrontendConfig:
    return load_frontend_config()


def get_readdb() -> ReadDB:
    global _READDB
    with _READDB_LOCK:
        if _READDB is None:
            _READDB = ReadDB(get_settings().db_path)
        return _READDB
