from __future__ import annotations

import json
import os
import shutil
import sys
from glob import glob

from src.bench_emit.agents import rat, repo2run, v3
from src.bench_emit.meta import bench_meta
from src.bench_emit.types import EmittedEnv

_ADAPTERS = {"v3": v3, "repo2run": repo2run, "rat": rat}


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def emit_run(run_root: str, agent: str, dest: str) -> list[tuple[str, str]]:
    if agent not in _ADAPTERS:
        raise ValueError(f"unknown agent: {agent!r} (expected one of {sorted(_ADAPTERS)})")
    adapt = _ADAPTERS[agent].adapt
    output_root = os.path.join(run_root, "output")

    results: list[tuple[str, str]] = []
    for repo_dir in sorted(glob(os.path.join(output_root, "*", "*"))):
        if not os.path.isdir(repo_dir):
            continue
        owner, name = os.path.normpath(repo_dir).split(os.sep)[-2:]
        full_name = f"{owner}/{name}"
        try:
            env = adapt(repo_dir)
        except Exception as exc:                      # noqa: BLE001 — anti-vanish: never abort the batch
            # A crashed adapter must stay visible — never silently indistinguishable
            # from an expected "no artifact" repo. Warn, then fall back to missing.
            print(f"[bench_emit] {full_name}: adapter error: {exc!r}", file=sys.stderr)
            env = EmittedEnv(dockerfile=None, scripts={}, meta={**bench_meta(agent), "error": repr(exc)})

        dest_dir = os.path.join(dest, owner, name)
        # Re-emit into a non-fresh dest must not leave a repo's prior artifacts behind:
        # a repo that flips ok->missing would otherwise keep a stale Dockerfile next to
        # the fresh error meta, which bench.harvest reads as a bogus "ok". Clean slate.
        shutil.rmtree(dest_dir, ignore_errors=True)
        os.makedirs(dest_dir, exist_ok=True)
        _write(os.path.join(dest_dir, "bench_meta.json"), json.dumps(env.meta, indent=2))

        if env.dockerfile is not None:
            _write(os.path.join(dest_dir, "Dockerfile"), env.dockerfile)
            for fname, content in (env.scripts or {}).items():
                _write(os.path.join(dest_dir, fname), content)
            results.append((full_name, "ok"))
        else:
            results.append((full_name, "missing"))
    return results
