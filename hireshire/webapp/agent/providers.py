"""Build the LangChain chat model for the agent from config/frontend.yaml.

API keys are read from the environment (.env): ANTHROPIC_API_KEY / OPENAI_API_KEY
/ GOOGLE_API_KEY, matching whichever provider is configured.
"""
from __future__ import annotations

from hireshire.webapp.config import ChatConfig


def build_chat_model(cfg: ChatConfig):
    provider = (cfg.provider or "anthropic").lower()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # Opus 4.8 / Sonnet 5 reject temperature/top_p (400) — omit sampling params.
        return ChatAnthropic(model=cfg.model, max_tokens=cfg.max_tokens)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=cfg.model, temperature=cfg.temperature, max_tokens=cfg.max_tokens,
        )
    if provider in ("gemini", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=cfg.model, temperature=cfg.temperature, max_output_tokens=cfg.max_tokens,
        )
    raise ValueError(f"Unknown chat provider '{cfg.provider}'.")
