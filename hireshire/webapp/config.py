"""Loader for config/frontend.yaml (dashboard settings)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class ChatConfig(BaseModel):
    provider: str = "openai"         # anthropic / openai / gemini
    model: str = "gpt-4o-mini"
    max_tokens: int = 4096
    temperature: float = 0.0


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


class FrontendConfig(BaseModel):
    chat: ChatConfig = ChatConfig()
    server: ServerConfig = ServerConfig()
    db_path: str = "data/hireshire.db"


def load_frontend_config(path: str | Path = "config/frontend.yaml") -> FrontendConfig:
    p = Path(path)
    if not p.exists():
        return FrontendConfig()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return FrontendConfig(
        chat=ChatConfig(**raw.get("chat", {})),
        server=ServerConfig(**raw.get("server", {})),
        db_path=raw.get("db_path", "data/hireshire.db"),
    )
