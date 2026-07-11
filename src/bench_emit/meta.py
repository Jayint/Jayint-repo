from __future__ import annotations


def bench_meta(
    agent: str,
    *,
    base_image: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    produce_s: float | None = None,
    head_sha: str | None = None,
    commit: str | None = None,
    llm_calls: int | None = None,
    turns_used: int | None = None,
    dockerfile_source: str | None = None,
) -> dict:
    """Build a bench_meta.json payload, dropping keys whose value is None."""
    payload = {
        "agent": agent,
        "base_image": base_image,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_calls": llm_calls,
        "turns_used": turns_used,
        "produce_s": produce_s,
        "head_sha": head_sha,
        "commit": commit,
        "dockerfile_source": dockerfile_source,
    }
    return {k: v for k, v in payload.items() if v is not None}
