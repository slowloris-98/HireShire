"""Turn raw browser-extracted records into staged `StagedJob` records.

The `/scrape-direct` skill dumps whatever its JS extractor found straight to
`data/direct/<run_id>/raw/<company>.json` without interpreting it — that is
deliberate, so job payloads never pass through the model's context. All the
judgement lives here instead: relative-date parsing, the age cutoff, the
location filter, and dedupe.

Keeping it in code rather than in the skill means it is deterministic, testable,
and costs nothing to re-run.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from hireshire.direct.locations import normalize_location

logger = logging.getLogger(__name__)

_RELATIVE = re.compile(
    r"(\d+)\+?\s*(minute|min|hour|hr|day|week|month)s?\s*ago", re.I
)
_UNITS = {
    "minute": "minutes", "min": "minutes",
    "hour": "hours", "hr": "hours",
    "day": "days", "week": "weeks",
}
# Formats these portals actually emit, most specific first.
_ABSOLUTE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y")


def parse_posted(raw: Optional[str], now: Optional[datetime] = None) -> datetime:
    """Best-effort posting time. Falls back to `now` when nothing parses.

    Falling back to "now" (rather than dropping the job) is intentional: a job
    with no readable date still deserves scoring, and `seen_jobs` prevents it
    being reprocessed on later runs.
    """
    now = now or datetime.now(timezone.utc)
    if not raw:
        return now

    text = raw.strip()

    m = _RELATIVE.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "month":
            return now - timedelta(days=30 * n)
        return now - timedelta(**{_UNITS[unit]: n})

    if re.search(r"\b(today|just posted|moments? ago)\b", text, re.I):
        return now
    if re.search(r"\byesterday\b", text, re.I):
        return now - timedelta(days=1)

    cleaned = re.sub(r"^posted\s+(on\s+)?", "", text, flags=re.I).strip()
    for fmt in _ABSOLUTE_FORMATS:
        try:
            day = datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        # Date-only precision: treat as end-of-day so a posting made earlier
        # today isn't judged >24h old by a few hours.
        return day + timedelta(hours=23, minutes=59)

    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return now


def location_passes(location: str, terms: list[str]) -> bool:
    """Case-insensitive substring match, after country inference.

    An empty term list means no filtering. A job with no location text is kept —
    the API scrapers behave the same way rather than guessing.
    """
    if not terms:
        return True
    if not location or location == "N/A":
        return True
    low = normalize_location(location).lower()
    return any(t.lower() in low for t in terms)


def normalize_records(
    records: list[dict],
    *,
    location_terms: Optional[list[str]] = None,
    max_age_hours: Optional[int] = None,
    now: Optional[datetime] = None,
) -> tuple[list[dict], dict[str, int]]:
    """Raw extractor output -> staged records, plus a per-reason drop tally.

    Returns records in the `StagedJob` shape (see `staging.py`); malformed rows
    are counted and skipped, never fatal.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours) if max_age_hours else None
    terms = location_terms or []

    out: list[dict] = []
    seen: set[str] = set()
    dropped = {"malformed": 0, "duplicate": 0, "age": 0, "location": 0}

    for rec in records:
        if not isinstance(rec, dict):
            dropped["malformed"] += 1
            continue

        native_id = str(rec.get("native_id") or "").strip()
        title = (rec.get("title") or "").strip()
        url = (rec.get("url") or "").strip()
        if not native_id or not title or not url.startswith("http"):
            dropped["malformed"] += 1
            continue

        if native_id in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(native_id)

        posted = parse_posted(rec.get("posted_raw") or rec.get("updated_at"), now)
        if cutoff and posted < cutoff:
            dropped["age"] += 1
            continue

        raw_location = (rec.get("location") or "").strip()
        if not location_passes(raw_location, terms):
            dropped["location"] += 1
            continue

        out.append({
            "native_id": native_id,
            "title": title,
            "url": url,
            "updated_at": posted.isoformat(),
            "location": normalize_location(raw_location),
            "content_html": rec.get("content_html"),
            "department": rec.get("department"),
            "requisition_id": rec.get("requisition_id"),
            # List-only: the matcher funnel hydrates descriptions for jobs that
            # survive its relevance gate.
            "detail_fetch_failed": not rec.get("content_html"),
        })

    return out, {k: v for k, v in dropped.items() if v}
