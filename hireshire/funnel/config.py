from __future__ import annotations

from pydantic import BaseModel


class EncoderConfig(BaseModel):
    # A sentence-transformers model name (MiniLM by default). The key is configurable
    # so a lighter ONNX backend can be swapped in without code changes.
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Semantic anchors for the desired job type — retarget this list (e.g. to
    # ["strategy", "strategist"]) to reuse the funnel for a different hunt.
    targets: list[str] = []
    # A title passes when its max cosine similarity to any target >= threshold.
    threshold: float = 0.35


class DetailFetchConfig(BaseModel):
    concurrency: int = 10   # max concurrent detail hydrations in flight
    jitter_s: float = 0.3   # random pre-fetch sleep to avoid bursting a tenant
    timeout_s: float = 20.0  # per-call httpx timeout for the detail client


class FunnelConfig(BaseModel):
    enabled: bool = False
    encoder: EncoderConfig = EncoderConfig()
    detail_fetch: DetailFetchConfig = DetailFetchConfig()
