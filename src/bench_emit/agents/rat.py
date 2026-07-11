from __future__ import annotations

import json
import os

from src.bench_emit.meta import bench_meta
from src.bench_emit.normalize import clone_lines, link_testbed
from src.bench_emit.types import EmittedEnv

_TOKENS_IN_KEYS = ("tokens_in", "prompt_tokens", "input_tokens", "total_input_tokens")
_TOKENS_OUT_KEYS = ("tokens_out", "completion_tokens", "output_tokens", "total_output_tokens")


def _first_num(d: dict, keys: tuple) -> int | float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return v
    return None


def _render(base: str, repo_url: str, recipe: list) -> str:
    lines = [
        f"FROM {base}",
        "WORKDIR /",
        clone_lines(repo_url, dest="/repo"),
        "WORKDIR /repo",
    ]
    lines += ["RUN " + c for c in recipe]
    dockerfile = "\n".join(lines) + "\n"
    return link_testbed(dockerfile, src="/repo")


def adapt(repo_output_dir: str) -> EmittedEnv:
    cs_path = os.path.join(repo_output_dir, "case_study.json")
    try:
        with open(cs_path) as f:
            cs = json.load(f)
    except (OSError, json.JSONDecodeError):
        return EmittedEnv(dockerfile=None, scripts={},
                          meta=bench_meta("rat", dockerfile_source="rat_reconstructed"))

    env = cs.get("environment", {}) or {}
    base = env.get("base_image") or "python:3.10-slim"
    recipe = env.get("recipe_commands", []) or []

    if not recipe:
        return EmittedEnv(dockerfile=None, scripts={},
                          meta=bench_meta("rat", base_image=base, dockerfile_source="rat_reconstructed"))

    owner, name = os.path.normpath(repo_output_dir).split(os.sep)[-2:]
    repo_url = f"https://github.com/{owner}/{name}"
    dockerfile = _render(base, repo_url, recipe)

    prov = cs.get("provenance", {}) or {}
    produce_s = None
    if isinstance(prov.get("start_ts"), (int, float)) and isinstance(prov.get("end_ts"), (int, float)):
        produce_s = round(prov["end_ts"] - prov["start_ts"], 2)

    cost = cs.get("cost", {}) or {}
    meta = bench_meta(
        "rat",
        base_image=base,
        tokens_in=_first_num(cost, _TOKENS_IN_KEYS),
        tokens_out=_first_num(cost, _TOKENS_OUT_KEYS),
        produce_s=produce_s,
        dockerfile_source="rat_reconstructed",
    )
    return EmittedEnv(dockerfile=dockerfile, scripts={}, meta=meta)
