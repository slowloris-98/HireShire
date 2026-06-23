from __future__ import annotations

from pathlib import Path

import yaml

_ITEMIZE_OPEN = r"\begin{itemize}[leftmargin=12pt, topsep=1pt, itemsep=0pt, parsep=0pt]"
_ITEMIZE_CLOSE = r"\end{itemize}"


def load_projects(path: str | Path) -> dict[str, dict]:
    """Load projects_bullets.yaml keyed by project id."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {p["id"]: p for p in data["projects"]}


def load_resume_assets(path: str | Path) -> dict:
    """Load the non-project option pools (summaries, skills) from projects_bullets.yaml.

    Returns {"summaries": {...}, "skills": {...}} with empty defaults when keys are absent.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {
        "summaries": data.get("summaries", {}) or {},
        "skills": data.get("skills", {}) or {},
    }


def _build_entry(
    project: dict,
    adjusted_bullets: list[str | None] | None,
    max_bullets: int | None = None,
    catch_text: str | None = None,
) -> str:
    header = project["header"].rstrip("\n")
    raw_bullets: list[str] = project["bullets"][:max_bullets] if max_bullets is not None else project["bullets"]
    adj = (adjusted_bullets[:max_bullets] if max_bullets is not None else adjusted_bullets) if adjusted_bullets else None
    lines = [header, _ITEMIZE_OPEN]
    rendered: list[str] = []
    for i, bullet in enumerate(raw_bullets):
        text = bullet
        if adj and i < len(adj) and adj[i] is not None:
            text = adj[i]
        rendered.append(text)
    # Domain-matched catch replaces the final rendered bullet (per master-prompt placement rule).
    if catch_text and rendered:
        rendered[-1] = catch_text
    for text in rendered:
        lines.append(f"    \\item {text}")
    lines.append(_ITEMIZE_CLOSE)
    return "\n".join(lines)


def _build_summary_section(summary_text: str | None) -> str:
    if not summary_text:
        return ""
    return f"\\resumesection{{SUMMARY}}\n\n\\noindent {summary_text}"


def _esc_amp(text: str) -> str:
    """Escape bare ampersands for LaTeX (labels/items are stored unescaped for clean matching)."""
    return text.replace("\\&", "&").replace("&", "\\&")


def _build_skills_section(
    skills: dict | None,
    skills_rows: list[dict] | None,
) -> str:
    """Render the TECHNICAL SKILLS section. Falls back to default_rows on missing/invalid input."""
    skills = skills or {}
    rows = skills_rows if skills_rows else skills.get("default_rows", [])
    languages_row = skills.get("languages_row", "Python, Typescript, Java, C\\#, SQL")

    lines = [
        f"\\noindent\\textbf{{{_esc_amp(r['label'])}:}} {_esc_amp(r['items'])}\\\\"
        for r in rows
    ]
    lines.append(f"\\noindent\\textbf{{Languages:}} {languages_row}")
    body = "\n".join(lines)
    return f"\\resumesection{{TECHNICAL SKILLS}}\n\n{body}"


def assemble_resume(
    template_path: str | Path,
    projects: dict[str, dict],
    selected_project_ids: list[str],
    selected_work_id: str,
    section_order: list[str],
    keyword_adjustments: dict[str, list[str | None]] | None = None,
    bullet_limits: dict[str, int] | None = None,
    *,
    skills: dict | None = None,
    summaries: dict | None = None,
    summary_variant: str | None = None,
    skills_rows: list[dict] | None = None,
    catch_domain: str | None = None,
    include_summary: bool = True,
) -> str:
    """Substitute the experience, skills, and summary placeholders in the template.

    skills/summaries are the option pools from load_resume_assets; summary_variant/skills_rows/
    catch_domain are the optimizer's validated selections. Any of these may be None, in which case
    the assembler falls back to the original (pre-enrichment) behavior.
    """
    template = Path(template_path).read_text(encoding="utf-8")
    adj = keyword_adjustments or {}
    lim = bullet_limits or {}
    summaries = summaries or {}

    def catch_for(pid: str) -> str | None:
        if not catch_domain:
            return None
        return (projects.get(pid, {}).get("catch_bullets") or {}).get(catch_domain)

    project_entries = [
        _build_entry(projects[pid], adj.get(pid), lim.get(pid), catch_text=catch_for(pid))
        for pid in selected_project_ids
        if pid in projects
    ]
    work_entry = (
        _build_entry(projects[selected_work_id], adj.get(selected_work_id), lim.get(selected_work_id))
        if selected_work_id in projects
        else ""
    )

    def make_section(title: str, entries: list[str]) -> str:
        block = ("\n\n\\vspace{\\entrygap}\n\n").join(entries)
        return f"\\resumesection{{{title}}}\n\n{block}"

    if section_order and section_order[0] == "work":
        sections = []
        if work_entry:
            sections.append(make_section("WORK EXPERIENCE", [work_entry]))
        if project_entries:
            sections.append(make_section("PROJECT EXPERIENCE", project_entries))
    else:
        sections = []
        if project_entries:
            sections.append(make_section("PROJECT EXPERIENCE", project_entries))
        if work_entry:
            sections.append(make_section("WORK EXPERIENCE", [work_entry]))

    combined = "\n\n".join(sections)

    summary_text = summaries.get(summary_variant) if (include_summary and summary_variant) else None
    summary_section = _build_summary_section(summary_text)
    skills_section = _build_skills_section(skills, skills_rows)

    return (
        template
        .replace("%{{SUMMARY_SECTION}}", summary_section)
        .replace("%{{SKILLS_SECTION}}", skills_section)
        .replace("%{{EXPERIENCE_SECTIONS}}", combined)
    )
