from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = "config/direct_boards.yaml"


class DirectSettings(BaseModel):
    enable_direct: bool = False  # orchestrator default; --direct forces on
    db_path: str = "data/hireshire.db"
    staging_dir: str = "data/direct"
    max_pages_per_company: int = 5
    detail_fetch: str = "title_filtered"  # all | title_filtered | none


class BoardConfig(BaseModel):
    enabled: bool = True
    list_url: str
    detail_url: Optional[str] = None
    page_size: int = 20
    # Per-board override of settings.max_pages_per_company (Meta's pagination is
    # unsolved, so it is pinned to 1).
    max_pages: Optional[int] = None
    # Verified DOM selectors, stored so the skill never re-probes for them.
    card_selector: Optional[str] = None
    link_selector: Optional[str] = None
    # How the board expresses a posting date: relative ("Posted 3 hours ago"),
    # absolute, or none at all (fall back to scrape time).
    date_style: str = "none"


class DirectConfig(BaseModel):
    settings: DirectSettings
    boards: dict[str, BoardConfig] = {}

    @property
    def enabled_boards(self) -> dict[str, BoardConfig]:
        return {name: b for name, b in self.boards.items() if b.enabled}


def load_direct_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DirectConfig:
    p = Path(path)
    if not p.exists():
        # Absent config means the feature is simply off — same tolerance the
        # scraper shows for a missing bad_slugs/no_swe file.
        return DirectConfig(settings=DirectSettings(), boards={})
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return DirectConfig(
        settings=DirectSettings(**(raw.get("settings") or {})),
        boards={k: BoardConfig(**v) for k, v in (raw.get("boards") or {}).items()},
    )
