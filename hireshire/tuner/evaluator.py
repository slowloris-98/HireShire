from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_never

from hireshire.models.job import Job
from hireshire.tuner.config import TunerSettings
from hireshire.tuner.prompts import EVALUATOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class EvaluatorResult(BaseModel):
    job_id: str
    shortcomings: list[str]
    missing_keywords: list[str]
    experience_gaps: list[str]
    weak_sections: list[str]
    overall_assessment: str


@runtime_checkable
class EvaluatorBackend(Protocol):
    async def call(self, prompt: str, system_prompt: str) -> EvaluatorResult: ...


# ---------------------------------------------------------------------------
# Gemini backend
# ---------------------------------------------------------------------------

def _is_gemini_retryable(exc: BaseException) -> bool:
    try:
        from google.genai import errors as genai_errors  # type: ignore[import-untyped]
        if isinstance(exc, genai_errors.ClientError):
            return getattr(exc, "code", None) == 429
        if isinstance(exc, genai_errors.ServerError):
            return True
    except ImportError:
        pass
    try:
        from google.api_core import exceptions as gexc
        return isinstance(exc, (gexc.ResourceExhausted, gexc.ServiceUnavailable, gexc.InternalServerError))
    except ImportError:
        pass
    return False


def _gemini_wait(retry_state) -> float:
    exc = retry_state.outcome.exception()
    if exc:
        m = re.search(r"'retryDelay':\s*'(\d+)s'", str(exc))
        if m:
            return float(m.group(1)) + 5
    return 90.0


_gemini_retry = retry(
    retry=retry_if_exception(_is_gemini_retryable),
    stop=stop_never,
    wait=_gemini_wait,
    reraise=True,
)


class GeminiEvaluatorBackend:
    def __init__(self, settings: TunerSettings, sem: asyncio.Semaphore) -> None:
        from google import genai  # type: ignore[import-untyped]
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError("GOOGLE_API_KEY environment variable is not set.")
        self._client = genai.Client(api_key=api_key)
        self._settings = settings
        self._sem = sem

    @_gemini_retry
    async def call(self, prompt: str, system_prompt: str) -> EvaluatorResult:
        from google.genai import types  # type: ignore[import-untyped]
        async with self._sem:
            response = await self._client.aio.models.generate_content(
                model=self._settings.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=EvaluatorResult.model_json_schema(),
                    system_instruction=system_prompt,
                ),
            )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        return EvaluatorResult.model_validate_json(response.text)


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

def _is_openai_retryable(exc: BaseException) -> bool:
    try:
        import openai
        return isinstance(exc, (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError))
    except ImportError:
        return False


def _openai_wait(retry_state) -> float:
    exc = retry_state.outcome.exception()
    if exc and hasattr(exc, "response") and exc.response is not None:
        after = exc.response.headers.get("Retry-After")
        if after:
            return float(after) + 2
    return 60.0


_openai_retry = retry(
    retry=retry_if_exception(_is_openai_retryable),
    stop=stop_never,
    wait=_openai_wait,
    reraise=True,
)


class OpenAIEvaluatorBackend:
    def __init__(self, settings: TunerSettings, sem: asyncio.Semaphore) -> None:
        try:
            import openai
        except ImportError:
            raise ImportError("openai package required. Install with: pip install openai")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._settings = settings
        self._sem = sem

    @_openai_retry
    async def call(self, prompt: str, system_prompt: str) -> EvaluatorResult:
        async with self._sem:
            response = await self._client.beta.chat.completions.parse(
                model=self._settings.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format=EvaluatorResult,
            )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        return response.choices[0].message.parsed


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

def _is_anthropic_retryable(exc: BaseException) -> bool:
    try:
        import anthropic
        return isinstance(exc, (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError))
    except ImportError:
        return False


def _anthropic_wait(retry_state) -> float:
    exc = retry_state.outcome.exception()
    if exc and hasattr(exc, "response") and exc.response is not None:
        after = exc.response.headers.get("Retry-After")
        if after:
            return float(after) + 2
    return 60.0


_anthropic_retry = retry(
    retry=retry_if_exception(_is_anthropic_retryable),
    stop=stop_never,
    wait=_anthropic_wait,
    reraise=True,
)


class AnthropicEvaluatorBackend:
    def __init__(self, settings: TunerSettings, sem: asyncio.Semaphore) -> None:
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package required. Install with: pip install anthropic")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._settings = settings
        self._sem = sem

    @_anthropic_retry
    async def call(self, prompt: str, system_prompt: str) -> EvaluatorResult:
        async with self._sem:
            response = await self._client.messages.create(
                model=self._settings.model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                tools=[{
                    "name": "evaluate_resume",
                    "description": "Return the structured recruiter critique for this resume and job.",
                    "input_schema": EvaluatorResult.model_json_schema(),
                }],
                tool_choice={"type": "tool", "name": "evaluate_resume"},
            )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        tool_use = next(b for b in response.content if b.type == "tool_use")
        return EvaluatorResult.model_validate(tool_use.input)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_EVALUATOR_BACKENDS: dict[str, type] = {
    "gemini": GeminiEvaluatorBackend,
    "openai": OpenAIEvaluatorBackend,
    "anthropic": AnthropicEvaluatorBackend,
}


def make_evaluator_backend(
    settings: TunerSettings,
    sem: asyncio.Semaphore,
    provider: str | None = None,
    model: str | None = None,
) -> EvaluatorBackend:
    resolved_provider = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
    cls = _EVALUATOR_BACKENDS.get(resolved_provider)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{resolved_provider}'. Choose from: {', '.join(_EVALUATOR_BACKENDS)}"
        )
    if model and model != settings.model:
        settings = settings.model_copy(update={"model": model})
    return cls(settings, sem)


# ---------------------------------------------------------------------------
# ResumeEvaluator
# ---------------------------------------------------------------------------

class ResumeEvaluator:
    def __init__(self, settings: TunerSettings, backend: EvaluatorBackend) -> None:
        self._settings = settings
        self._backend = backend

    async def evaluate(self, job: Job, resume_tex: str) -> EvaluatorResult | None:
        if not job.content_text or not job.content_text.strip():
            logger.warning("Job %s has no content_text; skipping evaluation", job.job_id)
            return None

        logger.info("Evaluating job %s/%s", job.board_token, job.job_id)
        prompt = self._build_prompt(job, resume_tex)
        try:
            return await self._backend.call(prompt, EVALUATOR_SYSTEM_PROMPT)
        except Exception as exc:
            logger.warning(
                "Evaluator LLM call failed for job %s/%s: %s",
                job.board_token, job.job_id, exc,
            )
            return None

    def _build_prompt(self, job: Job, resume_tex: str) -> str:
        jd = (job.content_text or "")[:self._settings.max_jd_chars]
        tex = resume_tex[:self._settings.max_tex_chars]
        return (
            f"## Candidate Resume (LaTeX source)\n{tex}\n\n"
            f"## Job: {job.title} at {job.board_token} ({job.location.name})\n"
            f"{jd}\n\n"
            "Evaluate how well this candidate's resume matches this job description. "
            "Identify all shortcomings, missing keywords, experience gaps, and weak sections. "
            "Be specific — reference actual resume content and job requirements."
        )
