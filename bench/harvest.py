# bench/harvest.py
from __future__ import annotations

import json
import os
import re
from glob import glob

from bench.schema import HarvestedEnv, RepoSpec

_COPY = re.compile(r"^\s*COPY\s+(\S+)", re.MULTILINE)
# v3 legacy _meta.json uses the same key names we need; only base_image differs in nesting.
_META_NAMES = ("bench_meta.json", "_meta.json")


def _find_dockerfile(repo_dir: str) -> str | None:
    for cand in (os.path.join(repo_dir, "Dockerfile"), os.path.join(repo_dir, "eval_build", "Dockerfile")):
        if os.path.isfile(cand):
            return cand
    return None


def _load_meta(repo_dir: str) -> dict:
    for name in _META_NAMES:
        p = os.path.join(repo_dir, name)
        if os.path.isfile(p):
            try:
                return json.load(open(p))
            except (json.JSONDecodeError, OSError):
                return {}
    return {}


def _sibling_scripts(df_dir: str, dockerfile: str) -> dict:
    out = {}
    for src in _COPY.findall(dockerfile):
        p = os.path.join(df_dir, os.path.basename(src))
        if os.path.isfile(p):
            out[os.path.basename(src)] = open(p).read()
    return out


def discover(agent_roots: dict) -> list:
    envs = []
    for agent, root in agent_roots.items():
        for repo_dir in sorted(glob(os.path.join(root, "*", "*"))):
            if not os.path.isdir(repo_dir):
                continue
            full_name = "/".join(repo_dir.split(os.sep)[-2:])
            repo = RepoSpec(full_name, f"https://github.com/{full_name}")
            meta = _load_meta(repo_dir)
            df_path = _find_dockerfile(repo_dir)
            if df_path is None:
                envs.append(HarvestedEnv(agent, repo, None, {}, meta.get("base_image"), "missing", meta))
                continue
            df = open(df_path).read()
            scripts = _sibling_scripts(os.path.dirname(df_path), df)
            envs.append(HarvestedEnv(agent, repo, df, scripts, meta.get("base_image"), "ok", meta))
    return envs
