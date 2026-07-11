from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
from src.bench_emit.normalize import link_testbed, parse_from
from src.bench_emit.types import EmittedEnv


def _load_json(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def adapt(repo_output_dir: str) -> EmittedEnv:
    df_path = os.path.join(repo_output_dir, "Dockerfile")
    meta_json = _load_json(os.path.join(repo_output_dir, "_meta.json"))

    duration = meta_json.get("duration_s")
    produce_s = round(duration, 2) if isinstance(duration, (int, float)) else None

    if not os.path.isfile(df_path):
        meta = bench_meta("repo2run", produce_s=produce_s, dockerfile_source="repo2run_normalized")
        return EmittedEnv(dockerfile=None, scripts={}, meta=meta)

    with open(df_path) as f:
        dockerfile = f.read()

    dockerfile = link_testbed(dockerfile, src="/repo")
    meta = bench_meta(
        "repo2run",
        base_image=parse_from(dockerfile),
        produce_s=produce_s,
        dockerfile_source="repo2run_normalized",
    )
    return EmittedEnv(dockerfile=dockerfile, scripts={}, meta=meta)
