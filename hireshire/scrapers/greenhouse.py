from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from pydantic import ValidationError

from hireshire.http_client import make_retry_decorator
from hireshire.models.job import ApplicationQuestion, Department, Job, Location, Office
from hireshire.scrapers.base import AbstractScraper
from hireshire.scrapers.exceptions import SlugNotFoundError

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def _parse_location(raw: dict | str) -> Location:
    if isinstance(raw, str):
        return Location(name=raw)
    return Location(name=raw.get("name", "") or "")


def _parse_departments(raw: list[dict]) -> list[Department]:
    result = []
    for d in raw:
        try:
            result.append(Department(id=d["id"], name=d["name"], parent_id=d.get("parent_id")))
        except (KeyError, ValidationError):
            pass
    return result


def _parse_offices(raw: list[dict]) -> list[Office]:
    result = []
    for o in raw:
        try:
            loc = o.get("location") or {}
            loc_name = loc if isinstance(loc, str) else loc.get("name")
            result.append(Office(id=o["id"], name=o["name"], location=loc_name))
        except (KeyError, ValidationError):
            pass
    return result


def _parse_questions(raw: list[dict]) -> list[ApplicationQuestion]:
    result = []
    for q in raw:
        try:
            result.append(ApplicationQuestion(
                label=q.get("label", ""),
                required=bool(q.get("required", False)),
                field_type=q.get("type", ""),
                values=[v["label"] for v in q.get("values", []) if "label" in v],
            ))
        except (KeyError, ValidationError):
            pass
    return result


def _parse_job(
    board_token: str,
    list_entry: dict,
    detail: Optional[dict],
    scraped_at: datetime,
) -> Optional[Job]:
    try:
        content_html = list_entry.get("content") or (detail.get("content") if detail else None)
        questions = _parse_questions(detail.get("questions", [])) if detail else []

        return Job(
            source="greenhouse",
            board_token=board_token,
            job_id=str(list_entry["id"]),
            internal_job_id=str(list_entry.get("internal_job_id", "")) or None,
            title=list_entry["title"],
            location=_parse_location(list_entry.get("location") or {}),
            departments=_parse_departments(list_entry.get("departments", [])),
            offices=_parse_offices(list_entry.get("offices", [])),
            absolute_url=list_entry["absolute_url"],
            updated_at=list_entry["updated_at"],
            requisition_id=list_entry.get("requisition_id"),
            content_html=content_html,
            content_text=content_html,
            questions=questions,
            detail_fetch_failed=(detail is None),
            scraped_at=scraped_at,
        )
    except (KeyError, ValidationError, AttributeError) as exc:
        logger.warning("Failed to parse job %s from %s: %s", list_entry.get("id"), board_token, exc)
        return None


class GreenhouseScraper(AbstractScraper):
    source = "greenhouse"

    def __init__(self, client: httpx.AsyncClient, sem: asyncio.Semaphore, retry_attempts: int = 3):
        self._client = client
        self._sem = sem
        self._retry = make_retry_decorator(retry_attempts)

    async def fetch_all(self, board_token: str) -> list[Job]:
        list_entries = await self._fetch_all_pages(board_token)
        if not list_entries:
            return []

        scraped_at = datetime.now(timezone.utc)
        tasks = [self._fetch_detail_and_parse(board_token, entry, scraped_at) for entry in list_entries]
        results = await asyncio.gather(*tasks)
        return [j for j in results if j is not None]

    async def _fetch_all_pages(self, board_token: str) -> list[dict]:
        url = f"{BASE_URL}/{board_token}/jobs?content=true"
        jobs: list[dict] = []

        # Greenhouse uses Link header (RFC-5988) for pagination
        while url:
            try:
                response = await self._get(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise SlugNotFoundError("greenhouse", board_token) from exc
                raise

            data = response.json()
            jobs.extend(data.get("jobs", []))

            # Follow next page link if present
            url = _parse_next_link(response.headers.get("link", ""))

        return jobs

    async def _fetch_detail_and_parse(
        self, board_token: str, list_entry: dict, scraped_at: datetime
    ) -> Optional[Job]:
        job_id = list_entry["id"]
        detail = None
        try:
            response = await self._get(f"{BASE_URL}/{board_token}/jobs/{job_id}?questions=true")
            detail = response.json()
        except Exception as exc:
            logger.warning("Detail fetch failed for job %s/%s: %s", board_token, job_id, exc)

        return _parse_job(board_token, list_entry, detail, scraped_at)

    async def _get(self, url: str) -> httpx.Response:
        @self._retry
        async def _do_get():
            async with self._sem:
                response = await self._client.get(url)
                response.raise_for_status()
                return response

        return await _do_get()


def _parse_next_link(link_header: str) -> Optional[str]:
    """Parse RFC-5988 Link header, return URL for rel="next" or None."""
    if not link_header:
        return None
    for part in link_header.split(","):
        parts = [p.strip() for p in part.split(";")]
        if len(parts) == 2 and parts[1] == 'rel="next"':
            return parts[0].strip("<>")
    return None
