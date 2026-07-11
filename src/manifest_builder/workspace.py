from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass

from src.manifest_builder.protected import compute_protected, hash_host

SEED_DOCKERFILE = """FROM {base}
WORKDIR /src
COPY . /src
RUN pip install --no-cache-dir -e . || pip install --no-cache-dir . || true
"""

VERIFY_SHIM = """\
#!/bin/sh
# Harness-generated agent oracle. Runs the SAME `certify` the harness uses, so the agent
# optimizes against the real gate. The harness re-certifies independently, so edits here only
# mislead the agent's own loop, never the certificate.
cd "{harness_root}" && exec python3 -m src.manifest_builder verify --workspace "{workspace}"
"""

_STATE_FILE = ".manifest_ws.json"


@dataclass(frozen=True)
class Workspace:
    path: str
    slug: str
    repo_url: str
    commit_sha: str
    src_root: str
    protected: tuple[str, ...]
    pristine_hashes: dict
    base_image: str
    dockerfile_text: str


def repo_slug(repo_url: str) -> str:
    s = re.sub(r"\.git$", "", repo_url.rstrip("/"))
    s = s.split("://")[-1].split("/", 1)[-1] if "://" in s else s
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def prepare_workspace(repo_url, commit_sha, dest, base_image="python:3.11-slim") -> Workspace:
    subprocess.run(["git", "clone", "-q", repo_url, dest], check=True, capture_output=True)
    subprocess.run(["git", "-C", dest, "checkout", "-q", commit_sha], check=True,
                   capture_output=True)
    protected = compute_protected(dest)
    hashes = hash_host(dest, protected)
    df = SEED_DOCKERFILE.format(base=base_image)
    with open(os.path.join(dest, "Dockerfile"), "w") as f:
        f.write(df)
    abs_dest = os.path.abspath(dest)
    harness_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    verify_path = os.path.join(dest, "verify")
    with open(verify_path, "w") as f:
        f.write(VERIFY_SHIM.format(harness_root=harness_root, workspace=abs_dest))
    os.chmod(verify_path, 0o755)
    ws = Workspace(path=dest, slug=repo_slug(repo_url), repo_url=repo_url, commit_sha=commit_sha,
                   src_root="/src", protected=protected, pristine_hashes=hashes,
                   base_image=base_image, dockerfile_text=df)
    save_state(ws)
    return ws


def save_state(ws: Workspace) -> None:
    d = asdict(ws)
    d["protected"] = list(ws.protected)
    with open(os.path.join(ws.path, _STATE_FILE), "w") as f:
        json.dump(d, f, indent=1)


def load_state(path: str) -> Workspace:
    with open(os.path.join(path, _STATE_FILE)) as f:
        d = json.load(f)
    d["protected"] = tuple(d["protected"])
    return Workspace(**d)
