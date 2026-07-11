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
