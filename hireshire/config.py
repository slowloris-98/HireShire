from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class CompanyConfig(BaseModel):
    name: str
    greenhouse_token: Optional[str] = None
    lever_token: Optional[str] = None
    tags: list[str] = []


class ScraperSettings(BaseModel):
    concurrency: int = 10
    request_timeout_s: float = 30.0
    retry_attempts: int = 3
    max_age_hours: Optional[int] = None  # None = fetch all jobs regardless of age
    location_filter: list[str] = []      # empty = no filter; substring match against location + offices


class AppConfig(BaseModel):
    settings: ScraperSettings
    companies: list[CompanyConfig]

    @property
    def greenhouse_companies(self) -> list[CompanyConfig]:
        return [c for c in self.companies if c.greenhouse_token]


def load_config(path: str | Path = "config/scraper.yaml") -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig(
        settings=ScraperSettings(**raw.get("settings", {})),
        companies=[CompanyConfig(**c) for c in raw.get("companies", [])],
    )
