from __future__ import annotations

from pathlib import Path

import pdfplumber


def extract_resume_text(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Resume not found: {path}. Place resume.pdf in the project root.")

    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())

    text = "\n\n".join(text_parts)
    if not text.strip():
        raise ValueError(f"Could not extract any text from {path}. Is it a scanned PDF?")

    return text
