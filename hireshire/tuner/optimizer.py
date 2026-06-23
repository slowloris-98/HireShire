from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from tenacity import retry, retry_if_exception, stop_never

from hireshire.models.job import Job
from hireshire.tuner.config import TunerSettings
from hireshire.tuner.evaluator import EvaluatorResult
from hireshire.tuner.prompts import SELECTOR_SYSTEM_PROMPT, TRIMMER_SYSTEM_PROMPT

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
# OpenAI helper
# ---------------------------------------------------------------------------

def _openai_token_limit(model: str, n: int) -> dict:
    """Return {max_completion_tokens: n} for newer-generation models that dropped max_tokens."""
    if any(model.startswith(p) for p in ("o1", "o3", "gpt-5")):
        return {"max_completion_tokens": n}
    return {"max_tokens": n}


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
                response_format={"type": "json_object"},
                **_openai_token_limit(self._settings.model, 4096),
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

def _strip_fences(text: str) -> str:
    """Remove markdown code fences some models add despite instructions."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
    if stripped.endswith("```"):
        stripped = stripped.rsplit("```", 1)[0].rstrip()
    return stripped


def _validate_latex(text: str, job_id: str) -> str:
    cleaned = _strip_fences(text)
    if not cleaned.startswith(("\\", "%")):
        logger.warning(
            "Trim output for job %s may not be valid LaTeX "
            "(does not start with \\ or %%). First 100 chars: %r",
            job_id, cleaned[:100],
        )
    return cleaned


# ---------------------------------------------------------------------------
# SelectionResult
# ---------------------------------------------------------------------------

class SelectionResult(BaseModel):
    selected_projects: list[str]
    selected_work: str
    section_order: list[str] = ["projects", "work"]
    keyword_adjustments: dict[str, list[str | None]] = {}
    # Enrichment selections (all optional; None/invalid → assembler falls back to default behavior)
    summary_variant: str | None = None
    skills_rows: list[dict] | None = None
    catch_domain: str | None = None


@dataclass
class OptimizeResult:
    tex: str
    selection: SelectionResult


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
        critique: EvaluatorResult,
        projects: dict[str, dict],
        template_path: str,
        assets: dict | None = None,
    ) -> OptimizeResult | None:
        assets = assets or {}
        logger.info("Selecting projects for job %s/%s", job.board_token, job.job_id)
        prompt = self._build_selection_prompt(job, critique, projects, assets)
        MAX_SELECTOR_RETRIES = 3
        selection = None
        for attempt in range(MAX_SELECTOR_RETRIES):
            try:
                raw = await self._backend.call(prompt, SELECTOR_SYSTEM_PROMPT)
                selection = self._parse_selection(raw, projects, job.job_id, assets)
                break
            except Exception as exc:
                logger.warning(
                    "Selector attempt %d/%d failed for job %s/%s: %s",
                    attempt + 1, MAX_SELECTOR_RETRIES, job.board_token, job.job_id, exc,
                )
                if attempt < MAX_SELECTOR_RETRIES - 1:
                    await asyncio.sleep(2.0)
        if selection is None:
            return None

        from hireshire.tuner.assembler import assemble_resume
        tex = assemble_resume(
            template_path=template_path,
            projects=projects,
            selected_project_ids=selection.selected_projects,
            selected_work_id=selection.selected_work,
            section_order=selection.section_order,
            keyword_adjustments=selection.keyword_adjustments,
            skills=assets.get("skills"),
            summaries=assets.get("summaries"),
            summary_variant=selection.summary_variant,
            skills_rows=selection.skills_rows,
            catch_domain=selection.catch_domain,
        )
        return OptimizeResult(tex=tex, selection=selection)

    def _parse_selection(
        self, raw: str, projects: dict[str, dict], job_id: str, assets: dict | None = None
    ) -> SelectionResult:
        assets = assets or {}
        cleaned = _strip_fences(raw)
        data = json.loads(cleaned)
        result = SelectionResult.model_validate(data)
        valid_ids = set(projects.keys())
        if (
            result.selected_work not in valid_ids
            or projects.get(result.selected_work, {}).get("type") != "work"
        ):
            work_ids = [pid for pid, p in projects.items() if p.get("type") == "work"]
            result.selected_work = work_ids[0] if work_ids else ""
            logger.warning(
                "Selector returned invalid/non-work ID for selected_work on job %s; falling back to %s",
                job_id, result.selected_work,
            )

        seen: set[str] = set()
        clean: list[str] = []
        for p in result.selected_projects:
            if (
                p in valid_ids
                and projects[p].get("type") != "work"
                and p not in seen
                and p != result.selected_work
            ):
                seen.add(p)
                clean.append(p)
        result.selected_projects = clean

        # --- Adjusted-bullet dash guard: revert any rephrase that introduced dash punctuation ---
        for pid, bullets in (result.keyword_adjustments or {}).items():
            for i, b in enumerate(bullets):
                if b is not None and (" -- " in b or "—" in b):
                    logger.info(
                        "Reverting dash-violating keyword adjustment on %s bullet %d (job %s)",
                        pid, i, job_id,
                    )
                    bullets[i] = None

        # --- Enrichment validation against the option pools ---
        result.summary_variant = self._validate_summary(result.summary_variant, assets)
        result.catch_domain = self._validate_catch(result.catch_domain, result.selected_projects, projects)
        result.skills_rows = self._clean_skills_rows(result.skills_rows, assets.get("skills") or {})
        return result

    @staticmethod
    def _validate_summary(variant: str | None, assets: dict) -> str | None:
        summaries = assets.get("summaries") or {}
        return variant if variant in summaries else None

    @staticmethod
    def _validate_catch(
        catch_domain: str | None, selected_projects: list[str], projects: dict[str, dict]
    ) -> str | None:
        if not catch_domain or "agentic_bmc" not in selected_projects:
            return None
        catches = (projects.get("agentic_bmc", {}).get("catch_bullets") or {})
        return catch_domain if catch_domain in catches else None

    @staticmethod
    def _clean_skills_rows(rows: list[dict] | None, skills: dict) -> list[dict] | None:
        """Keep only label_options labels and pool items; drop the rest. None → assembler default."""
        if not rows:
            return None
        labels = set(skills.get("label_options") or [])
        pool = skills.get("pool") or []
        pool_lower = {item.lower(): item for item in pool}
        cleaned: list[dict] = []
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            label = row.get("label")
            if labels and label not in labels:
                continue
            raw_items = row.get("items")
            tokens = raw_items if isinstance(raw_items, list) else str(raw_items or "").split(",")
            kept = [pool_lower[t.strip().lower()] for t in tokens if t.strip().lower() in pool_lower]
            if label and kept:
                cleaned.append({"label": label, "items": ", ".join(kept)})
        return cleaned or None

    def _build_selection_prompt(
        self,
        job: Job,
        critique: EvaluatorResult,
        projects: dict[str, dict],
        assets: dict | None = None,
    ) -> str:
        assets = assets or {}
        jd = (job.content_text or "")[:self._settings.max_jd_chars]

        years = critique.years_experience_required
        num_projects = 2 if (years is not None and years > 3) else 3

        work_lines, project_lines = [], []
        for pid, p in projects.items():
            line = f"  - id: {pid} | title: {p.get('title', pid)} | description: {p.get('description', '')}"
            (work_lines if p.get("type") == "work" else project_lines).append(line)
        roster = (
            "WORK ENTRIES:\n" + "\n".join(work_lines)
            + "\n\nPROJECT ENTRIES:\n" + "\n".join(project_lines)
        )

        bullet_counts = "\n".join(
            f"  {pid}: {len(p.get('bullets', []))} bullets"
            for pid, p in projects.items()
        )

        critique_block = "\n".join([
            f"Missing keywords: {', '.join(critique.missing_keywords) or 'none'}",
            f"Experience gaps: {', '.join(critique.experience_gaps) or 'none'}",
            f"Overall: {critique.overall_assessment}",
        ])

        years_label = str(years) if years is not None else "unknown"
        sections = [
            (
                f"## Job: {job.title} at {job.board_token} ({job.location.name})\n"
                f"Select exactly {num_projects} projects "
                f"(detected experience requirement: {years_label} yrs)"
            ),
            f"### Job Description\n{jd}",
            f"### Recruiter Critique\n{critique_block}",
            f"### Available Entries\n{roster}",
            f"### Bullet Counts (for keyword_adjustments array lengths)\n{bullet_counts}",
        ]

        # --- Enrichment option pools (selector picks keys/items from these only) ---
        skills = assets.get("skills") or {}
        summaries = assets.get("summaries") or {}
        if summaries:
            sections.append(
                "### Summary Archetypes (pick ONE key for summary_variant, or null)\n"
                + "\n".join(f"  - {k}" for k in summaries)
            )
        if skills:
            labels = ", ".join(skills.get("label_options", []))
            pool = ", ".join(skills.get("pool", []))
            sections.append(
                "### Skills Pool (for skills_rows — up to 3 rows)\n"
                f"Allowed labels: {labels}\n"
                f"Allowed items (use ONLY these; Languages row is fixed automatically): {pool}"
            )
        catches = (projects.get("agentic_bmc", {}).get("catch_bullets") or {})
        if catches:
            sections.append(
                "### Catch Domains (set catch_domain ONLY if agentic_bmc is selected, else null)\n"
                + "\n".join(f"  - {k}" for k in catches)
            )
        return "\n\n".join(sections)

