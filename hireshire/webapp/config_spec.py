"""Whitelist of dashboard-editable config fields, their YAML locations, UI hints,
and per-file validation via the pipeline's own pydantic settings models.

Only fields listed here are readable/writable from the UI; a PUT touching any
other key is rejected. matcher + funnel share config/matcher.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from hireshire.applier.config import ApplierSettings
from hireshire.config import ScraperSettings
from hireshire.funnel.config import FunnelConfig
from hireshire.matcher.config import MatcherSettings, TitleFilterConfig
from hireshire.tuner.config import TunerSettings

PROVIDERS = ["gemini", "openai", "anthropic"]


@dataclass
class FieldSpec:
    path: tuple[str, ...]          # key path into the YAML document
    type: str                      # bool | int | float | str | str_list | enum
    doc: str                       # human-readable description (also fed to the chat)
    options: Optional[list[str]] = None


@dataclass
class PhaseSpec:
    file: str
    fields: dict[str, FieldSpec]
    validate: Callable[[dict], Any]
    extra_docs: dict[str, str] = field(default_factory=dict)


def _validate_scraper(d: dict) -> Any:
    return ScraperSettings(**d.get("settings", {}))


def _validate_matcher_file(d: dict) -> Any:
    MatcherSettings(**d.get("settings", {}))
    TitleFilterConfig(**d.get("title_filter", {}))
    FunnelConfig(**d.get("funnel", {}))
    return True


def _validate_tuner(d: dict) -> Any:
    return TunerSettings(**d.get("settings", {}))


def _validate_applier(d: dict) -> Any:
    return ApplierSettings(**d.get("settings", {}))


PHASE_SPECS: dict[str, PhaseSpec] = {
    "scraper": PhaseSpec(
        file="config/scraper.yaml",
        validate=_validate_scraper,
        fields={
            "location_filter": FieldSpec(
                ("settings", "location_filter"), "str_list",
                "Case-insensitive substrings a job's location must contain to be kept "
                "(e.g. 'remote', 'united states'). Empty = keep all locations."),
            "max_age_hours": FieldSpec(
                ("settings", "max_age_hours"), "int",
                "Only keep jobs updated within the last N hours. Null = fetch all ages."),
        },
    ),
    "matcher": PhaseSpec(
        file="config/matcher.yaml",
        validate=_validate_matcher_file,
        fields={
            "threshold": FieldSpec(
                ("settings", "threshold"), "int",
                "Minimum relevance_score (0-100) an LLM-scored job needs to be shortlisted."),
            "provider": FieldSpec(
                ("settings", "provider"), "enum",
                "LLM provider used to score jobs. Null falls back to the LLM_PROVIDER env var.",
                options=PROVIDERS),
            "model": FieldSpec(
                ("settings", "model"), "str",
                "Model name for the chosen scoring provider (e.g. gpt-5-nano, gemini-2.0-flash)."),
            "skip_llm": FieldSpec(
                ("settings", "skip_llm"), "bool",
                "Skip LLM scoring entirely — every title/funnel-passing job is auto-shortlisted "
                "with score 100. Saves API calls when you only want the title filter."),
            "include_keywords": FieldSpec(
                ("title_filter", "include_keywords"), "str_list",
                "A job title must contain at least one of these (case-insensitive) to survive. "
                "Empty disables the include check."),
            "exclude_keywords": FieldSpec(
                ("title_filter", "exclude_keywords"), "str_list",
                "A job title containing any of these is dropped (e.g. senior, manager, staff)."),
        },
    ),
    "funnel": PhaseSpec(
        file="config/matcher.yaml",
        validate=_validate_matcher_file,
        fields={
            "enabled": FieldSpec(
                ("funnel", "enabled"), "bool",
                "Enable the semantic funnel: after the keyword title filter, a MiniLM encoder "
                "keeps only titles similar to the target roles, then lazily fetches descriptions."),
            "threshold": FieldSpec(
                ("funnel", "encoder", "threshold"), "float",
                "Minimum cosine similarity (0-1) between a job title and any target role for the "
                "job to pass the funnel. Higher = stricter. ~0.35 is a typical starting point."),
            "targets": FieldSpec(
                ("funnel", "encoder", "targets"), "str_list",
                "Semantic anchor roles the funnel matches titles against (e.g. 'backend engineer'). "
                "Retarget this list to hunt for a different kind of role without code changes."),
        },
    ),
    "tuner": PhaseSpec(
        file="config/tuner.yaml",
        validate=_validate_tuner,
        fields={
            "enable_tuner": FieldSpec(
                ("settings", "enable_tuner"), "bool",
                "Whether the orchestrator/dashboard runs the resume-tuning phase. "
                "The --no-tuner CLI flag still force-skips it."),
            "resume_tex_path": FieldSpec(
                ("settings", "resume_tex_path"), "str",
                "Path to your master resume LaTeX, read by the evaluator (Pass 1)."),
            "resume_template_path": FieldSpec(
                ("settings", "resume_template_path"), "str",
                "LaTeX template the assembler fills with selected projects (Pass 2)."),
            "projects_bullets_path": FieldSpec(
                ("settings", "projects_bullets_path"), "str",
                "YAML of pre-authored project bullets the assembler chooses from."),
            "evaluator_provider": FieldSpec(
                ("settings", "evaluator_provider"), "enum",
                "LLM provider for the recruiter-style critique pass. Null falls back to LLM_PROVIDER.",
                options=PROVIDERS),
            "evaluator_model": FieldSpec(
                ("settings", "evaluator_model"), "str",
                "Model for the evaluator pass (a cheap/fast model is fine)."),
            "optimizer_provider": FieldSpec(
                ("settings", "optimizer_provider"), "enum",
                "LLM provider for the project-selector pass. 'claude_code' routes via the local CLI.",
                options=PROVIDERS + ["claude_code"]),
            "optimizer_model": FieldSpec(
                ("settings", "optimizer_model"), "str",
                "Model for the optimizer/selector pass (returns JSON project selection)."),
        },
    ),
    "applier": PhaseSpec(
        file="config/applier.yaml",
        validate=_validate_applier,
        fields={
            "enable_applier": FieldSpec(
                ("settings", "enable_applier"), "bool",
                "Whether the orchestrator/dashboard runs the applier phase after tuning. "
                "The --apply CLI flag still force-enables it."),
            "dry_run": FieldSpec(
                ("settings", "dry_run"), "bool",
                "CRITICAL safety switch: true = fill forms but never submit; false = actually submit."),
            "first_name": FieldSpec(("settings", "first_name"), "str", "Applicant first name for forms."),
            "last_name": FieldSpec(("settings", "last_name"), "str", "Applicant last name for forms."),
            "email": FieldSpec(("settings", "email"), "str", "Applicant email for forms."),
            "phone": FieldSpec(("settings", "phone"), "str", "Applicant phone number for forms."),
            "generate_cover_letter": FieldSpec(
                ("settings", "generate_cover_letter"), "bool",
                "Auto-generate a cover letter per application when the form asks for one."),
        },
    ),
}


def field_docs(phase: str) -> dict[str, str]:
    spec = PHASE_SPECS[phase]
    return {name: fs.doc for name, fs in spec.fields.items()}
