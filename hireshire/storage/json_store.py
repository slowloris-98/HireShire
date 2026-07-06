from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from hireshire.models.job import Job

logger = logging.getLogger(__name__)

# Characters illegal in Windows filenames (Workday tokens contain '|').
_UNSAFE_FILENAME_RE = re.compile(r'[|<>:"/\\?*]')


class CompanyResult:
    def __init__(self, board_token: str):
        self.board_token = board_token
        self.status: str = "ok"
        self.job_count: int = 0
        self.error: Optional[str] = None
        self.jobs: list[dict] = []
        self.fetch_time_s: Optional[float] = None


class RunStore:
    def __init__(self, base_dir: Path, run_id: str):
        self.run_dir = base_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._results: list[CompanyResult] = []

    def save_company(self, board_token: str, jobs: list[Job], fetch_time_s: Optional[float] = None) -> None:
        result = CompanyResult(board_token)
        result.job_count = len(jobs)
        result.jobs = [job.model_dump(mode="json") for job in jobs]
        result.fetch_time_s = fetch_time_s
        self._results.append(result)

        out_path = self.run_dir / f"{_UNSAFE_FILENAME_RE.sub('_', board_token)}.json"
        out_path.write_text(json.dumps(result.jobs, indent=2, default=str), encoding="utf-8")
        logger.info("Saved %d jobs for %s -> %s", len(jobs), board_token, out_path)

    def record_error(self, board_token: str, status: str, error: str, fetch_time_s: Optional[float] = None) -> None:
        result = CompanyResult(board_token)
        result.status = status
        result.error = error
        result.fetch_time_s = fetch_time_s
        self._results.append(result)

    def save_manifest(self, started_at: datetime) -> None:
        finished_at = datetime.now(timezone.utc)
        total_jobs = sum(r.job_count for r in self._results)

        companies = {}
        for r in self._results:
            companies[r.board_token] = {
                "status": r.status,
                "job_count": r.job_count,
                "fetch_time_s": round(r.fetch_time_s, 2) if r.fetch_time_s is not None else None,
                "error": r.error,
                "jobs": r.jobs,
            }

        manifest = {
            "run_id": self.run_id,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "total_jobs": total_jobs,
            "companies": companies,
        }

        manifest_path = self.run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info("Manifest saved: %d total jobs across %d companies", total_jobs, len(companies))

    @staticmethod
    def latest_run(base_dir: Path) -> Optional[Path]:
        runs = sorted([p for p in base_dir.iterdir() if p.is_dir() and p.name[:4].isdigit()], reverse=True) if base_dir.exists() else []
        return runs[0] if runs else None
