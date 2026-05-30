from __future__ import annotations

from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl, field_validator


class Location(BaseModel):
    name: str


class Department(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None


class Office(BaseModel):
    id: int
    name: str
    location: Optional[str] = None


class ApplicationQuestion(BaseModel):
    label: str
    required: bool
    field_type: str
    values: list[str] = []


class Job(BaseModel):
    source: str
    board_token: str
    job_id: str
    internal_job_id: Optional[str] = None

    title: str
    location: Location
    departments: list[Department] = []
    offices: list[Office] = []
    absolute_url: HttpUrl
    updated_at: datetime
    requisition_id: Optional[str] = None

    content_html: Optional[str] = None
    content_text: Optional[str] = None

    questions: list[ApplicationQuestion] = []
    detail_fetch_failed: bool = False

    scraped_at: datetime

    @field_validator("content_text", mode="before")
    @classmethod
    def strip_html(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return BeautifulSoup(v, "lxml").get_text(separator=" ", strip=True)
