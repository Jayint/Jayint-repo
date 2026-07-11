from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
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

    duration = meta_json.get("duration_s")
    produce_s = round(duration, 2) if isinstance(duration, (int, float)) else None

    meta = bench_meta(
        "v3",
        base_image=meta_json.get("base_image"),
        produce_s=produce_s,
        # An absent head_sha is often serialized as "" upstream; coerce it to None so
        # bench_meta drops the key entirely rather than emitting "head_sha": "".
        head_sha=(meta_json.get("head_sha") or None),
        dockerfile_source="v3_eval_build",
    )

    if not os.path.isfile(df_path):
        return EmittedEnv(dockerfile=None, scripts={}, meta=meta)

    with open(df_path) as f:
        dockerfile = f.read()

    scripts: dict = {}
    setup_path = os.path.join(eval_build, "setup.sh")
    if os.path.isfile(setup_path):
        with open(setup_path) as f:
            scripts["setup.sh"] = f.read()

    return EmittedEnv(dockerfile=dockerfile, scripts=scripts, meta=meta)
