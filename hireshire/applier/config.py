from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class ApplierSettings(BaseModel):
    enable_applier: bool = False  # dashboard/orchestrator default: run the applier phase after tuning
    dry_run: bool = True
    matches_dir: str = "data/matches"
    applied_dir: str = "data/applied"
    runs_dir: str = "data/scraped"
    db_path: str = "data/hireshire.db"
    resume_path: str = "data/resume_projects/Udayan_Resume.pdf"
    headless: bool = True
    inter_job_delay_s: float = 10.0
    max_steps: int = 40

    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""

    generate_cover_letter: bool = True
    model: str = "gpt-4o-mini"


class ApplierConfig(BaseModel):
    settings: ApplierSettings


def load_applier_config(path: str | Path = "config/applier.yaml") -> ApplierConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ApplierConfig(settings=ApplierSettings(**raw.get("settings", {})))
