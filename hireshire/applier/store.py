from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ApplyRecord(BaseModel):
    job_id: str
    board_token: str
    title: str
    absolute_url: str
    applied_at: datetime
    status: str  # "dry_run" | "submitted" | "error" | "skipped"
    dry_run: bool
    screenshot: Optional[str] = None
    error: Optional[str] = None


class AppliedStore:
    def __init__(self, base_dir: Path) -> None:
        base_dir.mkdir(parents=True, exist_ok=True)
        self._path = base_dir / "applied.json"
        self._records: list[ApplyRecord] = self._load()
        self._applied_ids: set[str] = {r.job_id for r in self._records}

    def _load(self) -> list[ApplyRecord]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return [ApplyRecord(**r) for r in raw]
        except Exception as exc:
            logger.warning("Could not load %s: %s", self._path, exc)
            return []

    def is_applied(self, job_id: str) -> bool:
        return job_id in self._applied_ids

    def append(self, record: ApplyRecord) -> None:
        self._records.append(record)
        self._applied_ids.add(record.job_id)
        self._path.write_text(
            json.dumps([r.model_dump(mode="json") for r in self._records], indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Saved apply record for %s (status=%s)", record.job_id, record.status)

    @property
    def records(self) -> list[ApplyRecord]:
        return list(self._records)
