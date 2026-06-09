from __future__ import annotations

from pathlib import Path

import yaml

_ITEMIZE_OPEN = r"\begin{itemize}[leftmargin=12pt, topsep=1pt, itemsep=0pt, parsep=0pt]"
_ITEMIZE_CLOSE = r"\end{itemize}"


def load_projects(path: str | Path) -> dict[str, dict]:
    """Load projects_bullets.yaml keyed by project id."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {p["id"]: p for p in data["projects"]}


def _build_entry(project: dict, adjusted_bullets: list[str | None] | None, max_bullets: int | None = None) -> str:
    header = project["header"].rstrip("\n")
    raw_bullets: list[str] = project["bullets"][:max_bullets] if max_bullets is not None else project["bullets"]
    adj = (adjusted_bullets[:max_bullets] if max_bullets is not None else adjusted_bullets) if adjusted_bullets else None
    lines = [header, _ITEMIZE_OPEN]
    for i, bullet in enumerate(raw_bullets):
        text = bullet
        if adj and i < len(adj) and adj[i] is not None:
            text = adj[i]
        lines.append(f"    \\item {text}")
    lines.append(_ITEMIZE_CLOSE)
    return "\n".join(lines)


def assemble_resume(
    template_path: str | Path,
    projects: dict[str, dict],
    selected_project_ids: list[str],
    selected_work_id: str,
    section_order: list[str],
    keyword_adjustments: dict[str, list[str | None]] | None = None,
    bullet_limits: dict[str, int] | None = None,
) -> str:
    """Substitute %{{EXPERIENCE_SECTIONS}} in the template with assembled LaTeX blocks."""
    template = Path(template_path).read_text(encoding="utf-8")
    adj = keyword_adjustments or {}
    lim = bullet_limits or {}

    project_entries = [
        _build_entry(projects[pid], adj.get(pid), lim.get(pid))
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
    return template.replace("%{{EXPERIENCE_SECTIONS}}", combined)
