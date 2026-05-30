from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _make_llm(model: str):
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "openai":
        from browser_use import ChatOpenAI  # type: ignore[import-untyped]
        return ChatOpenAI(model=model)
    elif provider == "gemini":
        from browser_use import ChatGoogle  # type: ignore[import-untyped]
        return ChatGoogle(model=model)
    elif provider == "anthropic":
        from browser_use import ChatAnthropic  # type: ignore[import-untyped]
        return ChatAnthropic(model=model)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Choose from: openai, gemini, anthropic")


class FormFiller:
    def __init__(
        self,
        model: str,
        headless: bool,
        max_steps: int,
        screenshots_dir: Path,
    ) -> None:
        self._llm = _make_llm(model)
        self._headless = headless
        self._max_steps = max_steps
        self._screenshots_dir = screenshots_dir
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    async def fill(
        self,
        url: str,
        answers: dict[str, str],
        resume_path: Path,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        job_id: str,
        dry_run: bool,
    ) -> dict:
        from browser_use import Agent, Browser  # type: ignore[import-untyped]

        task = self._build_task(url, answers, resume_path, first_name, last_name, email, phone, dry_run)
        screenshot_path = self._screenshots_dir / f"{job_id}.png"

        browser = Browser(headless=self._headless)
        try:
            agent = Agent(
                task=task,
                llm=self._llm,
                browser=browser,
                available_file_paths=[str(resume_path.resolve())],
                max_steps=self._max_steps,
                extend_system_message=(
                    "When filling forms on dynamic pages (React/SPA), after each field interaction "
                    "wait briefly and re-snapshot the page before proceeding to the next field. "
                    "If an element index is no longer available, re-snapshot and find the correct element. "
                    "Never click the same stale index more than once — refresh your view first."
                ),
            )
            history = await agent.run()

            # Prefer a screenshot already taken by the agent; fall back to None
            agent_screenshots = history.screenshot_paths() if hasattr(history, "screenshot_paths") else []
            if agent_screenshots:
                screenshot_path = Path(agent_screenshots[-1])
            else:
                screenshot_path = None

            if history.has_errors():
                errors = history.errors()
                return {"status": "error", "error": str(errors), "screenshot": str(screenshot_path) if screenshot_path else None}

            status = "dry_run" if dry_run else "submitted"
            return {"status": status, "error": None, "screenshot": str(screenshot_path) if screenshot_path else None}

        except Exception as exc:
            logger.exception("Browser agent failed for job %s", job_id)
            return {"status": "error", "error": str(exc), "screenshot": None}
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    def _build_task(
        self,
        url: str,
        answers: dict[str, str],
        resume_path: Path,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        dry_run: bool,
    ) -> str:
        cover_letter = answers.pop("cover_letter", None)
        custom_answers = {k: v for k, v in answers.items()
                         if k not in {"first_name", "last_name", "email", "phone"}}

        lines = [
            f"Navigate to this job application page: {url}",
            "",
            "Fill in the application form with these values:",
            f"- First name: {first_name}",
            f"- Last name: {last_name}",
            f"- Email: {email}",
        ]
        if phone:
            lines.append(f"- Phone: {phone}")

        lines.append(f"- Resume: upload the file at {resume_path.resolve()}")

        if cover_letter:
            lines.append(f"- Cover letter (type this text into the cover letter field if present):\n{cover_letter}")

        if custom_answers:
            lines.append("")
            lines.append("Additional questions — use these exact answers:")
            for label, answer in custom_answers.items():
                lines.append(f'  - "{label}": {answer}')

        lines.append("")
        if dry_run:
            lines.append(
                "IMPORTANT: After filling all visible fields, take a screenshot of the completed form "
                "and then STOP. Do NOT click any submit, apply, or send button."
            )
        else:
            lines.append("After filling all fields, click the Submit or Apply button to submit the application.")

        return "\n".join(lines)
