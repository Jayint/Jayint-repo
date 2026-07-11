from __future__ import annotations

import hashlib
import os
import subprocess


def _git(worktree: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", worktree, *args], check=True, capture_output=True, text=True)
    return p.stdout


def compute_protected(worktree: str) -> tuple[str, ...]:
    files = [ln for ln in _git(worktree, "ls-files").splitlines() if ln.strip()]
    return tuple(sorted(f for f in files if f != "Dockerfile"))


def restore_pristine(worktree: str) -> None:
    # Preserve the agent's Dockerfile whether the repo tracks one or not, keep
    # manifest-internal state (.manifest_*), and keep the harness-written `verify` oracle shim.
    # `git checkout -- .` would otherwise revert a *tracked* Dockerfile to its committed version;
    # `git clean` would drop untracked state and the untracked shim.
    df_path = os.path.join(worktree, "Dockerfile")
    df = None
    if os.path.exists(df_path):
        with open(df_path) as f:
            df = f.read()
    _git(worktree, "checkout", "HEAD", "--", ".")
    _git(worktree, "clean", "-fdxq", "-e", "Dockerfile", "-e", ".manifest_*", "-e", "verify")
    if df is not None:
        with open(df_path, "w") as f:
            f.write(df)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def hash_host(worktree: str, protected) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in protected:
        out[rel] = _sha256_file(os.path.join(worktree, rel))
    return out


def source_tree_sha256(hashes: dict) -> str:
    blob = "\n".join(f"{p}:{hashes[p]}" for p in sorted(hashes))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def hash_in_image(exec_fn, src_root: str, protected, chunk: int = 400) -> dict[str, str]:
    root = src_root.rstrip("/")
    out: dict[str, str] = {}
    paths = list(protected)
    for i in range(0, len(paths), chunk):
        batch = paths[i:i + chunk]
        rc, text = exec_fn(["sha256sum", *[f"{root}/{p}" for p in batch]])
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            digest, _, path = line.partition("  ")   # sha256sum uses two spaces
            path = path.strip().lstrip("*")
            rel = path[len(root) + 1:] if path.startswith(root + "/") else path
            out[rel] = "sha256:" + digest.strip()
    return out
