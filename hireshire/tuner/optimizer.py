from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from typing import Protocol, runtime_checkable

from tenacity import retry, retry_if_exception, stop_never

from hireshire.models.job import Job
from hireshire.tuner.config import TunerSettings
from hireshire.tuner.evaluator import EvaluatorResult
from hireshire.tuner.prompts import OPTIMIZER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@runtime_checkable
class OptimizerBackend(Protocol):
    async def call(self, prompt: str, system_prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Retry helpers (same pattern as evaluator.py)
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


# ---------------------------------------------------------------------------
# Gemini backend
# ---------------------------------------------------------------------------

class GeminiOptimizerBackend:
    def __init__(self, settings: TunerSettings, sem: asyncio.Semaphore) -> None:
        from google import genai  # type: ignore[import-untyped]
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError("GOOGLE_API_KEY environment variable is not set.")
        self._client = genai.Client(api_key=api_key)
        self._settings = settings
        self._sem = sem

    @_gemini_retry
    async def call(self, prompt: str, system_prompt: str) -> str:
        from google.genai import types  # type: ignore[import-untyped]
        async with self._sem:
            response = await self._client.aio.models.generate_content(
                model=self._settings.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="text/plain",
                    system_instruction=system_prompt,
                ),
            )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        return response.text


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------

class OpenAIOptimizerBackend:
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
    async def call(self, prompt: str, system_prompt: str) -> str:
        async with self._sem:
            response = await self._client.chat.completions.create(
                model=self._settings.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
            )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicOptimizerBackend:
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
    async def call(self, prompt: str, system_prompt: str) -> str:
        async with self._sem:
            response = await self._client.messages.create(
                model=self._settings.model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        return response.content[0].text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Claude Code CLI backend
# ---------------------------------------------------------------------------

class ClaudeCodeOptimizerBackend:
    def __init__(self, settings: TunerSettings, sem: asyncio.Semaphore) -> None:
        if not shutil.which("claude"):
            raise EnvironmentError("claude CLI not found on PATH. Install Claude Code.")
        self._settings = settings
        self._sem = sem
        self._timeout = settings.claude_cli_timeout_s

    async def call(self, prompt: str, system_prompt: str) -> str:
        async with self._sem:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p",
                "--system-prompt", system_prompt,
                "--model", self._settings.model,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=prompt.encode()),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise RuntimeError(f"claude CLI timed out after {self._timeout}s")
            if proc.returncode != 0:
                raise RuntimeError(
                    f"claude CLI exited {proc.returncode}: {stderr.decode()[:500]}"
                )
            if self._settings.request_interval_s > 0:
                await asyncio.sleep(self._settings.request_interval_s)
        return stdout.decode()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_OPTIMIZER_BACKENDS: dict[str, type] = {
    "gemini": GeminiOptimizerBackend,
    "openai": OpenAIOptimizerBackend,
    "anthropic": AnthropicOptimizerBackend,
    "claude_code": ClaudeCodeOptimizerBackend,
}


def make_optimizer_backend(
    settings: TunerSettings,
    sem: asyncio.Semaphore,
    provider: str | None = None,
    model: str | None = None,
) -> OptimizerBackend:
    resolved_provider = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
    cls = _OPTIMIZER_BACKENDS.get(resolved_provider)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{resolved_provider}'. Choose from: {', '.join(_OPTIMIZER_BACKENDS)}"
        )
    if model and model != settings.model:
        settings = settings.model_copy(update={"model": model})
    return cls(settings, sem)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_preamble(resume_tex: str) -> str:
    marker = r"\begin{document}"
    idx = resume_tex.find(marker)
    return resume_tex[:idx + len(marker)] if idx != -1 else ""


def _extract_links(resume_tex: str) -> list[str]:
    return re.findall(r'\\href\{([^}]+)\}', resume_tex)


def _extract_urls_from_text(text: str) -> list[str]:
    return re.findall(r'https?://\S+', text)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences some models add despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (e.g. ```latex or ```)
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
    if stripped.endswith("```"):
        stripped = stripped.rsplit("```", 1)[0].rstrip()
    return stripped


def _validate_latex(text: str, job_id: str) -> str:
    cleaned = _strip_fences(text)
    if not cleaned.startswith(("\\", "%")):
        logger.warning(
            "Optimizer output for job %s may not be valid LaTeX "
            "(does not start with \\ or %%). First 100 chars: %r",
            job_id, cleaned[:100],
        )
    return cleaned


# ---------------------------------------------------------------------------
# ResumeOptimizer
# ---------------------------------------------------------------------------

class ResumeOptimizer:
    def __init__(self, settings: TunerSettings, backend: OptimizerBackend) -> None:
        self._settings = settings
        self._backend = backend

    async def optimize(
        self,
        job: Job,
        resume_tex: str,
        critique: EvaluatorResult,
        projects_text: str,
    ) -> str | None:
        logger.info("Optimizing job %s/%s", job.board_token, job.job_id)
        prompt = self._build_prompt(job, resume_tex, critique, projects_text)
        try:
            raw = await self._backend.call(prompt, OPTIMIZER_SYSTEM_PROMPT)
            return _validate_latex(raw, job.job_id)
        except Exception as exc:
            logger.warning(
                "Optimizer LLM call failed for job %s/%s: %s",
                job.board_token, job.job_id, exc,
            )
            return None

    async def trim_to_one_page(self, current_tex: str, pages: int) -> str | None:
        prompt = (
            f"The LaTeX resume below compiled to {pages} pages but MUST fit on exactly 1 page.\n"
            "Apply reductions in this priority order:\n"
            "1. Remove Coursework lines in Education\n"
            #"2. Remove the least impactful bullet points from projects or work experience\n"
            #"3. Shorten or condense verbose bullet text; reword for conciseness while keeping metrics and keywords\n"
            "4. Slightly reduce \\vspace values if still over (minimum 1pt)\n"
            "Do NOT change the preamble, margins, font size, or document class.\n"
            "Do NOT add or remove sections.\n"
            "Output ONLY the complete LaTeX source.\n\n"
            f"{current_tex}"
        )
        try:
            raw = await self._backend.call(prompt, OPTIMIZER_SYSTEM_PROMPT)
            return _validate_latex(raw, "trim")
        except Exception as exc:
            logger.warning("Trim LLM call failed: %s", exc)
            return None

    def _build_prompt(
        self,
        job: Job,
        resume_tex: str,
        critique: EvaluatorResult,
        projects_text: str,
    ) -> str:
        jd = (job.content_text or "")[:self._settings.max_jd_chars]
        tex = resume_tex[:self._settings.max_tex_chars]

        preamble = _extract_preamble(resume_tex)
        resume_links = _extract_links(resume_tex)
        pool_links = _extract_urls_from_text(projects_text) if projects_text else []
        seen: set[str] = set()
        all_links: list[str] = []
        for url in resume_links + pool_links:
            if url not in seen:
                seen.add(url)
                all_links.append(url)
        links_block = "\n".join(f"- {url}" for url in all_links) if all_links else "(none)"

        critique_block = "\n".join([
            f"**Shortcomings:** {', '.join(critique.shortcomings) or 'none'}",
            f"**Missing keywords:** {', '.join(critique.missing_keywords) or 'none'}",
            f"**Experience gaps:** {', '.join(critique.experience_gaps) or 'none'}",
            f"**Weak sections:** {', '.join(critique.weak_sections) or 'none'}",
            f"**Overall assessment:** {critique.overall_assessment}",
        ])

        sections = [
            f"## PREAMBLE — COPY THIS EXACTLY, UNCHANGED\n{preamble}",
            f"## LINKS — USE THESE EXACT URLS, DO NOT MODIFY\n{links_block}",
            f"## Job: {job.title} at {job.board_token} ({job.location.name})",
            f"### Job Description\n{jd}",
            f"### Recruiter Critique\n{critique_block}",
            f"### Additional Projects Pool\n{projects_text or '(none provided)'}",
            f"### Current Resume (LaTeX source)\n{tex}",
            "Now rewrite the LaTeX resume for this specific job. Output ONLY the LaTeX source.",
        ]
        return "\n\n".join(sections)
