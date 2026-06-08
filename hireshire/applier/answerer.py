from __future__ import annotations

import json
import logging
import os

from hireshire.models.job import Job

logger = logging.getLogger(__name__)


def _openai_token_limit(model: str, n: int) -> dict:
    if any(model.startswith(p) for p in ("o1", "o3", "gpt-5")):
        return {"max_completion_tokens": n}
    return {"max_tokens": n}


SYSTEM_PROMPT = (
    "You are helping fill out a job application. "
    "Based on the resume and job description, provide honest, concise, professional answers "
    "to each application question. Do not fabricate experience or qualifications. "
    "Return only a valid JSON object."
)


class QuestionAnswerer:
    def __init__(self, model: str, generate_cover_letter: bool) -> None:
        self._model = model
        self._generate_cover_letter = generate_cover_letter

    async def generate_answers(
        self,
        job: Job,
        resume_text: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
    ) -> dict[str, str]:
        # Standard fields are always included without an LLM call
        answers: dict[str, str] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
        }
        if phone:
            answers["phone"] = phone

        questions = job.questions
        custom_questions = [
            q for q in questions
            if q.label.lower() not in {"first name", "last name", "email", "phone", "resume", "cover letter"}
        ]

        if not custom_questions and not self._generate_cover_letter:
            return answers

        prompt = self._build_prompt(job, resume_text, custom_questions)
        try:
            llm_answers = await self._call_llm(prompt)
            answers.update(llm_answers)
        except Exception as exc:
            logger.warning("LLM answer generation failed for %s/%s: %s", job.board_token, job.job_id, exc)

        return answers

    def _build_prompt(self, job: Job, resume_text: str, custom_questions) -> str:
        questions_list = [
            {"label": q.label, "field_type": q.field_type, "allowed_values": q.values}
            for q in custom_questions
        ]

        lines = [
            f"## Resume\n{resume_text}",
            f"\n## Job: {job.title} at {job.board_token} ({job.location.name})",
        ]
        if job.content_text:
            lines.append(f"\n## Job Description\n{job.content_text[:5000]}")

        lines.append("\n## Instructions")
        if questions_list:
            lines.append(
                "Answer each question below. For select/dropdown fields, "
                "pick the most appropriate value from `allowed_values`. "
                "Return a JSON object with question labels as keys.\n"
                f"Questions:\n{json.dumps(questions_list, indent=2)}"
            )
        if self._generate_cover_letter:
            lines.append(
                '\nAlso include a "cover_letter" key with a 3-paragraph professional '
                "cover letter tailored to this specific role."
            )

        return "\n".join(lines)

    async def _call_llm(self, prompt: str) -> dict[str, str]:
        provider = os.environ.get("LLM_PROVIDER", "openai").lower()

        if provider == "openai":
            return await self._call_openai(prompt)
        elif provider == "gemini":
            return await self._call_gemini(prompt)
        elif provider == "anthropic":
            return await self._call_anthropic(prompt)
        else:
            raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Choose from: openai, gemini, anthropic")

    async def _call_openai(self, prompt: str) -> dict[str, str]:
        import openai
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY not set")
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            **_openai_token_limit(self._model, 2048),
        )
        return json.loads(response.choices[0].message.content)

    async def _call_gemini(self, prompt: str) -> dict[str, str]:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types  # type: ignore[import-untyped]
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError("GOOGLE_API_KEY not set")
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                system_instruction=SYSTEM_PROMPT,
            ),
        )
        return json.loads(response.text)

    async def _call_anthropic(self, prompt: str) -> dict[str, str]:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY not set")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.content[0].text)
