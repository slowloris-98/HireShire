from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


def make_retry_decorator(attempts: int):
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=2, min=1, max=60),
        reraise=True,
    )


def build_client(timeout_s: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s),
        headers={"User-Agent": "HireShire/0.1 (job scraper)"},
        follow_redirects=True,
    )
