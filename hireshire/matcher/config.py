from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class MatcherSettings(BaseModel):
    threshold: int = 70
    concurrency: int = 1
    provider: str | None = None  # None = fall back to LLM_PROVIDER env var
    model: str = "gemini-2.0-flash"
    max_content_chars: int = 8000
    resume_path: str = "resume.pdf"
    projects_path: str = ""  # optional markdown file appended to candidate profile
    runs_dir: str = "data/scraped"
    matches_dir: str = "data/matches"
    db_path: str = "data/hireshire.db"
    request_interval_s: float = 13.0  # min seconds between requests; 13s = ~4.6 RPM (safe for 5 RPM free tier)
    skip_llm: bool = False


class TitleFilterConfig(BaseModel):
    include_keywords: list[str] = []  # title must match at least one (if non-empty)
    exclude_keywords: list[str] = []  # title must match none


class MatcherConfig(BaseModel):
    settings: MatcherSettings
    title_filter: TitleFilterConfig = TitleFilterConfig()


def load_matcher_config(path: str | Path = "config/matcher.yaml") -> MatcherConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return MatcherConfig(
        settings=MatcherSettings(**raw.get("settings", {})),
        title_filter=TitleFilterConfig(**raw.get("title_filter", {})),
    )
