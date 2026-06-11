from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class CompanyConfig(BaseModel):
    name: str
    greenhouse_token: Optional[str] = None
    lever_token: Optional[str] = None
    ashby_token: Optional[str] = None
    tags: list[str] = []


class ScraperSettings(BaseModel):
    concurrency: int = 10
    request_timeout_s: float = 30.0
    retry_attempts: int = 3
    company_timeout_s: float = 120.0
    max_age_hours: Optional[int] = None  # None = fetch all jobs regardless of age
    location_filter: list[str] = []      # empty = no filter; substring match against location + offices


class AppConfig(BaseModel):
    settings: ScraperSettings
    companies: list[CompanyConfig]

    @property
    def greenhouse_companies(self) -> list[CompanyConfig]:
        return [c for c in self.companies if c.greenhouse_token]

    @property
    def lever_companies(self) -> list[CompanyConfig]:
        return [c for c in self.companies if c.lever_token]

    @property
    def ashby_companies(self) -> list[CompanyConfig]:
        return [c for c in self.companies if c.ashby_token]


def _load_companies_from_jsons(
    ashby_path: Path, greenhouse_path: Path, lever_path: Path
) -> list[CompanyConfig]:
    companies: list[CompanyConfig] = []
    for slug in json.loads(ashby_path.read_text(encoding="utf-8")):
        companies.append(CompanyConfig(name=slug, ashby_token=slug))
    for slug in json.loads(greenhouse_path.read_text(encoding="utf-8")):
        companies.append(CompanyConfig(name=slug, greenhouse_token=slug))
    for slug in json.loads(lever_path.read_text(encoding="utf-8")):
        companies.append(CompanyConfig(name=slug, lever_token=slug))
    return companies


def load_config(path: str | Path = "config/scraper.yaml") -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    base = path.parent
    companies = _load_companies_from_jsons(
        ashby_path=base / "ashby_companies.json",
        greenhouse_path=base / "greenhouse_companies.json",
        lever_path=base / "lever_companies.json",
    )
    return AppConfig(
        settings=ScraperSettings(**raw.get("settings", {})),
        companies=companies,
    )
