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
    cost_usd: float | None = None,
    dockerfile_source: str | None = None,
    provisional_installs: list | None = None,
) -> dict:
    """Build a bench_meta.json payload, dropping keys whose value is None.

    ``provisional_installs`` (Stage C Task 3) is the certified-with-provisional set —
    ``[{name, reason, cure_rung}]`` for each fallthrough install (a local-collision
    name installed from PyPI because it did not import under the cure). Pass ``None``
    (or an empty list coerced to ``None`` by the caller) when there are none, so the
    key is dropped and every collision-free run stays byte-identical.
    """
    payload = {
        "agent": agent,
        "base_image": base_image,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_calls": llm_calls,
        "turns_used": turns_used,
        "cost_usd": cost_usd,
        "produce_s": produce_s,
        "head_sha": head_sha,
        "commit": commit,
        "dockerfile_source": dockerfile_source,
        "provisional_installs": provisional_installs,
    }
    return {k: v for k, v in payload.items() if v is not None}
