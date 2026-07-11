from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
from src.bench_emit.normalize import parse_from
from src.bench_emit.types import EmittedEnv


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def adapt(repo_output_dir: str) -> EmittedEnv:
    eval_build = os.path.join(repo_output_dir, "eval_build")
    df_path = os.path.join(eval_build, "Dockerfile")
    meta_json = _load_json(os.path.join(repo_output_dir, "_meta.json"))

    dockerfile = None
    if os.path.isfile(df_path):
        with open(df_path) as f:
            dockerfile = f.read()

    duration = meta_json.get("duration_s")
    produce_s = round(duration, 2) if isinstance(duration, (int, float)) else None

    meta = bench_meta(
        "claude",
        # The Dockerfile FROM is authoritative for claude (already /testbed-shaped);
        # fall back to the harness-recorded base_image when no Dockerfile was produced.
        base_image=(parse_from(dockerfile) if dockerfile is not None else None)
        or meta_json.get("base_image"),
        produce_s=produce_s,
        # An absent head_sha is often serialized as "" upstream; coerce it to None so
        # bench_meta drops the key entirely rather than emitting "head_sha": "".
        head_sha=(meta_json.get("head_sha") or None),
        turns_used=(meta_json.get("turns") or meta_json.get("turns_used")),
        cost_usd=(meta_json.get("agent_cost_usd") or meta_json.get("cost_usd")),
        dockerfile_source="claudecode_dockerfile",
    )

    if dockerfile is None:
        return EmittedEnv(dockerfile=None, scripts={}, meta=meta)

    # The claudecode-dockerfile build context is Dockerfile-only (no COPY siblings),
    # so the emitted env is self-contained.
    return EmittedEnv(dockerfile=dockerfile, scripts={}, meta=meta)
