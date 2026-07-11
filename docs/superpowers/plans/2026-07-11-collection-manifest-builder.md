# Collection Gold-Manifest Builder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone module that drives a SOTA coding agent to configure a Docker environment for a pinned repo, then independently certifies the *maximum cleanly-collectable* pytest node-ID set as a fixed golden manifest.

**Architecture:** `prepare workspace → run external agent (edits only the Dockerfile) → harness independently certifies (restore pristine tree → docker build → pytest --collect-only ×2 via own plugin → 4-clause gate → certificate)`. The agent's success oracle (`verify`) is the *same code* as the harness's certifier, so the agent optimizes against the real gate. The pure gate + certificate are the tested heart; Docker/agent are injected so most tests need neither.

**Tech Stack:** Python 3.11+, pytest, Docker CLI (x86_64 VM), git. Self-contained under `src/manifest_builder/`.

## Global Constraints

- **Standalone.** No imports from `bench/`, `src/react_repair/`, or the run harness. The module owns its plugin, docker adapter, seed base, and evals.
- **Intra-package imports:** `from src.manifest_builder.<mod> import <x>`. Run as `python -m src.manifest_builder`. Test files start with the sys.path header (see Task 1 Step 1).
- **Exact collect command:** `pytest --collect-only -q -p no:cacheprovider -p manifest_collect_plugin <src_root>` — **never** `--continue-on-collection-errors` (so pytest's exit code stays meaningful: 0=clean, 2=collection error, 5=no tests).
- **Node-ID format:** canonical pytest nodeids (path-based, e.g. `tests/test_x.py::Class::test[param]`).
- **Accept predicate (all four):** `run1.exit_code == 0` ∧ `run2.exit_code == 0` ∧ `collected_count > 0` ∧ `set(run1.collected) == set(run2.collected)` ∧ `protected_ok`.
- **Objective is maximization:** keep the *accepted candidate with the highest `collected_count`* across agent attempts.
- **Protected set:** all files tracked at the pinned SHA **except** `Dockerfile`. Agent may edit **only** the Dockerfile (no sidecar files).
- **Hardened collect container flags:** `--network none --cpus 2 --memory 4g --pids-limit 512 --security-opt no-new-privileges --cap-drop ALL`.
- **Artifacts dir:** `artifacts/<repo_slug>/<sha>/` containing `Dockerfile`, `collected-nodeids.json`, `collection-certificate.json`, `build.log`, `collect-run1.json`, `collect-run2.json`, `agent-transcript.jsonl`.
- **Corpus input:** `datasets/rat_python_hard_subset.pinned.json` (per repo: `clone_url`, `commit`).

---

### Task 1: Types + the pure gate

**Files:**
- Create: `src/manifest_builder/__init__.py`, `src/manifest_builder/types.py`, `src/manifest_builder/gate.py`
- Test: `tests/manifest_builder/test_gate.py`

**Interfaces:**
- Produces: `CollectionResult(exit_code:int, collected:tuple[str,...]=(), collect_errors:tuple=(), skipped_modules:tuple=(), deselected:tuple=())` with property `collected_count:int`; `Verdict(accepted:bool, reasons:tuple[str,...], manifest:tuple[str,...]|None, collected_count:int)`; `accept(r1:CollectionResult, r2:CollectionResult, protected_ok:bool) -> Verdict`; `pick_best(verdicts:list[Verdict]) -> Verdict|None`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_gate.py`:

```python
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.types import CollectionResult
from src.manifest_builder.gate import accept, pick_best


def _clean(ids, exit_code=0):
    return CollectionResult(exit_code=exit_code, collected=tuple(ids))


def test_clean_stable_pristine_accepts():
    r = _clean(["t.py::a", "t.py::b"])
    v = accept(r, r, protected_ok=True)
    assert v.accepted and v.manifest == ("t.py::a", "t.py::b") and v.reasons == ()


def test_partial_collection_nonzero_exit_rejects():
    r = CollectionResult(exit_code=2, collected=("t.py::a",), collect_errors=("ImportError",))
    v = accept(r, r, protected_ok=True)
    assert not v.accepted and any("exit 2" in x for x in v.reasons) and v.manifest is None


def test_protected_modified_rejects():
    r = _clean(["t.py::a"])
    v = accept(r, r, protected_ok=False)
    assert not v.accepted and "protected files modified" in v.reasons


def test_unstable_nodeids_rejects():
    v = accept(_clean(["t.py::a", "t.py::b"]), _clean(["t.py::a"]), protected_ok=True)
    assert not v.accepted and any("unstable" in x for x in v.reasons)


def test_hollow_zero_collected_rejects():
    r = _clean([])
    v = accept(r, r, protected_ok=True)
    assert not v.accepted and any("hollow" in x for x in v.reasons)


def test_accepts_despite_author_skips_and_deselects():
    r = CollectionResult(exit_code=0, collected=("t.py::a",),
                         skipped_modules=("tests/test_opt.py",), deselected=("t.py::slow",))
    v = accept(r, r, protected_ok=True)
    assert v.accepted and v.manifest == ("t.py::a",)


def test_pick_best_returns_highest_count_accepted():
    small = accept(_clean(["a"]), _clean(["a"]), True)
    big = accept(_clean(["a", "b", "c"]), _clean(["a", "b", "c"]), True)
    bad = accept(_clean([], exit_code=2), _clean([], exit_code=2), True)
    assert pick_best([small, big, bad]) is big


def test_pick_best_none_when_all_rejected():
    bad = accept(_clean([], exit_code=2), _clean([], exit_code=2), True)
    assert pick_best([bad]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/__init__.py`:

```python
"""Collection gold-manifest builder: certify the maximum cleanly-collectable pytest node-ID set."""
```

Create `src/manifest_builder/types.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CollectionResult:
    exit_code: int
    collected: tuple[str, ...] = ()
    collect_errors: tuple[str, ...] = ()
    skipped_modules: tuple[str, ...] = ()
    deselected: tuple[str, ...] = ()

    @property
    def collected_count(self) -> int:
        return len(self.collected)


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reasons: tuple[str, ...]
    manifest: tuple[str, ...] | None
    collected_count: int
```

Create `src/manifest_builder/gate.py`:

```python
from __future__ import annotations

from src.manifest_builder.types import CollectionResult, Verdict


def accept(r1: CollectionResult, r2: CollectionResult, protected_ok: bool) -> Verdict:
    reasons: list[str] = []
    if r1.exit_code != 0:
        reasons.append(f"run1 exit {r1.exit_code} != 0")
    if r2.exit_code != 0:
        reasons.append(f"run2 exit {r2.exit_code} != 0")
    if r1.collected_count == 0:
        reasons.append("no items collected (hollow)")
    if set(r1.collected) != set(r2.collected):
        reasons.append("node-id set unstable across runs")
    if not protected_ok:
        reasons.append("protected files modified")
    accepted = not reasons
    manifest = tuple(sorted(set(r1.collected))) if accepted else None
    return Verdict(accepted=accepted, reasons=tuple(reasons), manifest=manifest,
                   collected_count=r1.collected_count)


def pick_best(verdicts: list[Verdict]) -> Verdict | None:
    accepted = [v for v in verdicts if v.accepted]
    return max(accepted, key=lambda v: v.collected_count) if accepted else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_gate.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/__init__.py src/manifest_builder/types.py src/manifest_builder/gate.py tests/manifest_builder/test_gate.py
git commit -m "feat(manifest): pure collection gate + keep-best (the tested heart)"
```

---

### Task 2: Certificate + artifacts

**Files:**
- Create: `src/manifest_builder/certificate.py`
- Test: `tests/manifest_builder/test_certificate.py`

**Interfaces:**
- Consumes: `Verdict`, `CollectionResult` (Task 1).
- Produces: `build_certificate(verdict, r1, r2, *, repo_url, commit_sha, base_image, base_image_digest, collect_command, source_tree_sha256, protected_file_hashes:dict, dockerfile_text, image_id, agent_meta:dict) -> dict`; `write_artifacts(out_dir, verdict, certificate, r1, r2, build_log, transcript_src=None) -> None`; helper `_sha256_json(obj) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_certificate.py`:

```python
import sys, pathlib, json
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.types import CollectionResult
from src.manifest_builder.gate import accept
from src.manifest_builder import certificate as C


def _make():
    r = CollectionResult(exit_code=0, collected=("t.py::a", "t.py::b"),
                         skipped_modules=("tests/test_opt.py",), deselected=("t.py::slow",))
    v = accept(r, r, protected_ok=True)
    cert = C.build_certificate(
        v, r, r, repo_url="https://x/y", commit_sha="deadbeef", base_image="python:3.11-slim",
        base_image_digest="sha256:abc", collect_command="pytest --collect-only -q",
        source_tree_sha256="sha256:tree", protected_file_hashes={"conftest.py": "sha256:cf"},
        dockerfile_text="FROM python:3.11-slim\n", image_id="sha256:img",
        agent_meta={"runner": "claude code", "model": "opus"})
    return v, r, cert


def test_status_certified_and_completeness_records_skips_and_deselects():
    _, _, cert = _make()
    assert cert["status"] == "CERTIFIED"
    comp = cert["completeness"]
    assert comp["skipped_modules"] == ["tests/test_opt.py"] and comp["n_skipped_modules"] == 1
    assert comp["deselected"] == ["t.py::slow"] and comp["n_deselected"] == 1


def test_hashes_present_and_manifest_hash_matches():
    v, _, cert = _make()
    h = cert["hashes"]
    assert h["source_tree_sha256"] == "sha256:tree"
    assert h["dockerfile_sha256"].startswith("sha256:")
    assert cert["manifest_size"] == 2
    assert h["manifest_sha256"] == C._sha256_json(list(v.manifest))


def test_rejected_status_when_not_accepted():
    r = CollectionResult(exit_code=2, collected=("t.py::a",))
    v = accept(r, r, protected_ok=True)
    cert = C.build_certificate(v, r, r, repo_url="u", commit_sha="s", base_image="b",
        base_image_digest="d", collect_command="c", source_tree_sha256="t",
        protected_file_hashes={}, dockerfile_text="", image_id="i", agent_meta={})
    assert cert["status"] == "REJECTED" and cert["accepted"] is False and cert["reject_reasons"]


def test_certificate_is_deterministic():
    _, _, c1 = _make()
    _, _, c2 = _make()
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)


def test_write_artifacts_emits_all_files(tmp_path):
    v, r, cert = _make()
    C.write_artifacts(str(tmp_path), v, cert, r, r, build_log="hello")
    for name in ("collected-nodeids.json", "collection-certificate.json", "build.log",
                 "collect-run1.json", "collect-run2.json"):
        assert (tmp_path / name).exists()
    assert json.load(open(tmp_path / "collected-nodeids.json")) == ["t.py::a", "t.py::b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_certificate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder.certificate'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/certificate.py`:

```python
from __future__ import annotations

import hashlib
import json
import os

TOOL_VERSION = "manifest_builder/0.1"


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode()).hexdigest()


def _sha256_json(obj) -> str:
    return _sha256_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


def build_certificate(verdict, r1, r2, *, repo_url, commit_sha, base_image, base_image_digest,
                      collect_command, source_tree_sha256, protected_file_hashes, dockerfile_text,
                      image_id, agent_meta) -> dict:
    manifest = list(verdict.manifest or ())
    return {
        "status": "CERTIFIED" if verdict.accepted else "REJECTED",
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "base_image": base_image,
        "base_image_digest": base_image_digest,
        "collect_command": collect_command,
        "accepted": verdict.accepted,
        "reject_reasons": list(verdict.reasons),
        "runs": [{"exit_code": r1.exit_code, "collected_count": r1.collected_count},
                 {"exit_code": r2.exit_code, "collected_count": r2.collected_count}],
        "manifest_size": len(manifest),
        "completeness": {
            "collected_count": r1.collected_count,
            "skipped_modules": list(r1.skipped_modules),
            "n_skipped_modules": len(r1.skipped_modules),
            "deselected": list(r1.deselected),
            "n_deselected": len(r1.deselected),
        },
        "hashes": {
            "source_tree_sha256": source_tree_sha256,
            "protected_files": dict(protected_file_hashes),
            "dockerfile_sha256": _sha256_text(dockerfile_text),
            "image_id": image_id,
            "collect_command_sha256": _sha256_text(collect_command),
            "manifest_sha256": _sha256_json(manifest),
        },
        "agent": dict(agent_meta),
        "tool_version": TOOL_VERSION,
    }


def _dump_run(r) -> dict:
    return {"exit_code": r.exit_code, "collected": list(r.collected),
            "collect_errors": list(r.collect_errors), "skipped_modules": list(r.skipped_modules),
            "deselected": list(r.deselected)}


def write_artifacts(out_dir, verdict, certificate, r1, r2, build_log, transcript_src=None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "collected-nodeids.json"), "w") as f:
        json.dump(list(verdict.manifest or ()), f, indent=1)
    with open(os.path.join(out_dir, "collection-certificate.json"), "w") as f:
        json.dump(certificate, f, indent=1)
    with open(os.path.join(out_dir, "build.log"), "w") as f:
        f.write(build_log or "")
    with open(os.path.join(out_dir, "collect-run1.json"), "w") as f:
        json.dump(_dump_run(r1), f, indent=1)
    with open(os.path.join(out_dir, "collect-run2.json"), "w") as f:
        json.dump(_dump_run(r2), f, indent=1)
    if transcript_src and os.path.exists(transcript_src):
        import shutil
        shutil.copyfile(transcript_src, os.path.join(out_dir, "agent-transcript.jsonl"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_certificate.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/certificate.py tests/manifest_builder/test_certificate.py
git commit -m "feat(manifest): certificate + artifacts (completeness records skips/deselects)"
```

---

### Task 3: Protected set + hashing + restore

**Files:**
- Create: `src/manifest_builder/protected.py`
- Test: `tests/manifest_builder/test_protected.py`

**Interfaces:**
- Produces: `compute_protected(worktree:str) -> tuple[str,...]` (tracked files minus `Dockerfile`); `restore_pristine(worktree:str) -> None`; `hash_host(worktree:str, protected) -> dict[str,str]`; `source_tree_sha256(hashes:dict) -> str`; `hash_in_image(exec_fn, src_root:str, protected, chunk:int=400) -> dict[str,str]` where `exec_fn(argv:list)->(rc:int,out:str)`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_protected.py`:

```python
import sys, pathlib, subprocess
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import protected as P


def _git(wt, *args):
    subprocess.run(["git", "-C", str(wt), *args], check=True, capture_output=True)


def _repo(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "pkg.py").write_text("x = 1\n")
    (wt / "test_a.py").write_text("def test_a():\n    assert 1\n")
    (wt / "conftest.py").write_text("# root conftest\n")
    (wt / "Dockerfile").write_text("FROM python:3.11-slim\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "init")
    return wt


def test_compute_protected_excludes_dockerfile(tmp_path):
    wt = _repo(tmp_path)
    prot = P.compute_protected(str(wt))
    assert "Dockerfile" not in prot
    assert set(prot) == {"pkg.py", "test_a.py", "conftest.py"}


def test_restore_reverts_edits_removes_cheat_keeps_dockerfile_and_state(tmp_path):
    wt = _repo(tmp_path)
    (wt / "test_a.py").write_text("def test_a():\n    assert 0  # sabotaged\n")   # edit protected
    (wt / "cheat.py").write_text("# untracked cheat\n")                           # untracked cheat
    (wt / ".manifest_ws.json").write_text('{"state": 1}\n')                       # untracked state
    # NB: this repo TRACKS a Dockerfile (committed in _repo); the agent has edited it.
    (wt / "Dockerfile").write_text("FROM python:3.11-slim\nRUN pip install foo\n")  # agent's work
    P.restore_pristine(str(wt))
    assert "assert 1" in (wt / "test_a.py").read_text()               # protected reverted
    assert not (wt / "cheat.py").exists()                             # untracked cheat removed
    assert (wt / ".manifest_ws.json").exists()                        # manifest state preserved
    assert "RUN pip install foo" in (wt / "Dockerfile").read_text()   # tracked Dockerfile kept


def test_hash_host_changes_when_file_changes(tmp_path):
    wt = _repo(tmp_path)
    prot = P.compute_protected(str(wt))
    h1 = P.hash_host(str(wt), prot)
    (wt / "pkg.py").write_text("x = 2\n")
    h2 = P.hash_host(str(wt), prot)
    assert h1["pkg.py"] != h2["pkg.py"]
    assert P.source_tree_sha256(h1) != P.source_tree_sha256(h2)


def test_hash_in_image_parses_sha256sum(tmp_path):
    prot = ("pkg.py", "test_a.py")

    def fake_exec(argv):
        # emulate `sha256sum /src/pkg.py /src/test_a.py`
        lines = "\n".join(f"{'a'*64}  {p}" for p in argv[1:])
        return 0, lines

    got = P.hash_in_image(fake_exec, "/src", prot)
    assert got == {"pkg.py": "sha256:" + "a" * 64, "test_a.py": "sha256:" + "a" * 64}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_protected.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder.protected'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/protected.py`:

```python
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
    # Preserve the agent's Dockerfile whether the repo tracks one or not, and keep
    # manifest-internal state (.manifest_*). `git checkout -- .` would otherwise revert a
    # *tracked* Dockerfile to its committed version; `git clean` would drop untracked state.
    df_path = os.path.join(worktree, "Dockerfile")
    df = None
    if os.path.exists(df_path):
        with open(df_path) as f:
            df = f.read()
    _git(worktree, "checkout", "--", ".")
    _git(worktree, "clean", "-fdq", "-e", "Dockerfile", "-e", ".manifest_*")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_protected.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/protected.py tests/manifest_builder/test_protected.py
git commit -m "feat(manifest): protected-set compute/restore + host & in-image hashing"
```

---

### Task 4: Collection plugin

**Files:**
- Create: `src/manifest_builder/collect_plugin.py`
- Test: `tests/manifest_builder/test_collect_plugin.py`

**Interfaces:**
- Produces: a pytest plugin registered as `-p manifest_collect_plugin` that writes JSON to `$MANIFEST_COLLECT_OUT` with keys `exit_status:int, collected:list[str], collected_count:int, collect_errors:list[str], skipped_modules:list[str], deselected:list[str]`.

Note: fixtures are generated at runtime inside `tmp_path` (no on-disk `test_*.py` under `tests/` that the outer run would collect).

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_collect_plugin.py`:

```python
import sys, os, json, subprocess, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _ROOT / "src" / "manifest_builder"


def _run_collect(target_dir, out_path):
    env = dict(os.environ)
    env["MANIFEST_COLLECT_OUT"] = str(out_path)
    env["PYTHONPATH"] = str(_PLUGIN_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-p", "manifest_collect_plugin", str(target_dir)],
        capture_output=True, text=True, env=env)
    return p.returncode, json.load(open(out_path))


def _write(d, files):
    for name, content in files.items():
        (d / name).write_text(content)


def test_clean_repo_collects_all(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _write(repo, {"test_math.py": "def test_add():\n    assert 1+1==2\n"
                                  "def test_sub():\n    assert 2-1==1\n"})
    rc, r = _run_collect(repo, tmp_path / "c.json")
    assert rc == 0 and r["exit_status"] == 0
    assert {n.split("::")[-1] for n in r["collected"]} == {"test_add", "test_sub"}
    assert r["collect_errors"] == []


def test_importorskip_hidden_module_recorded_not_collected(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _write(repo, {
        "test_ok.py": "def test_ok():\n    assert 1\n",
        "test_opt.py": "import pytest\npytest.importorskip('nonexistent_pkg_zzz')\n"
                       "def test_opt():\n    assert 1\n",
    })
    rc, r = _run_collect(repo, tmp_path / "c.json")
    assert rc == 0
    assert any("test_opt" in m for m in r["skipped_modules"])
    assert not any("test_opt" in c for c in r["collected"])
    assert any("test_ok" in c for c in r["collected"])


def test_broken_import_nonzero_exit_with_partial_collection(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _write(repo, {
        "test_ok.py": "def test_ok():\n    assert 1\n",
        "test_broken.py": "import nonexistent_pkg_zzz\ndef test_x():\n    assert 1\n",
    })
    rc, r = _run_collect(repo, tmp_path / "c.json")
    assert rc == 2 and r["exit_status"] == 2
    assert r["collect_errors"] and any("test_ok" in c for c in r["collected"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_collect_plugin.py -v`
Expected: FAIL — pytest errors that plugin `manifest_collect_plugin` is not found.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/collect_plugin.py`:

```python
"""Own pytest collection plugin — structured node-IDs via hooks, no stdout regex.

Register with:  pytest -p manifest_collect_plugin   (this file's dir on PYTHONPATH)
Writes JSON to $MANIFEST_COLLECT_OUT (default /tmp/manifest_collect.json).
Collection-only: no runtest hooks.
"""
import json
import os

_state = {"collected": [], "collect_errors": [], "skipped_modules": [], "deselected": [],
          "exit_status": None}


def pytest_collection_finish(session):
    _state["collected"] = [it.nodeid for it in session.items]


def pytest_collectreport(report):
    if report.outcome == "failed":
        _state["collect_errors"].append(report.nodeid)
    elif report.outcome == "skipped":
        _state["skipped_modules"].append(report.nodeid)


def pytest_deselected(items):
    _state["deselected"].extend(getattr(it, "nodeid", str(it)) for it in items)


def pytest_sessionfinish(session, exitstatus):
    _state["exit_status"] = int(exitstatus)
    result = {
        "exit_status": _state["exit_status"],
        "collected": _state["collected"],
        "collected_count": len(_state["collected"]),
        "collect_errors": _state["collect_errors"],
        "skipped_modules": _state["skipped_modules"],
        "deselected": _state["deselected"],
    }
    out = os.environ.get("MANIFEST_COLLECT_OUT", "/tmp/manifest_collect.json")
    with open(out, "w") as fh:
        json.dump(result, fh)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_collect_plugin.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/collect_plugin.py tests/manifest_builder/test_collect_plugin.py
git commit -m "feat(manifest): own collection plugin (node-ids + skips/errors/deselects via hooks)"
```

---

### Task 5: Collect parse + Docker adapter

**Files:**
- Create: `src/manifest_builder/collect.py`
- Test: `tests/manifest_builder/test_collect.py`

**Interfaces:**
- Consumes: `CollectionResult` (Task 1), `hash_in_image` (Task 3), the plugin file (Task 4).
- Produces: `parse_collection_result(exit_code:int, plugin_json:dict) -> CollectionResult`; `COLLECT_CMD:str`; `Docker(run=None)` with `build(tag, context_dir)->(rc,log)`, `image_id(tag)->str`, `run_detached(tag,name,workdir)`, `exec(name, argv, env=None, timeout=None)->(rc,out)`, `cp_in(name,src,dst)`, `cp_out(name,src,dst)`, `rm(name)`; `collect_once(docker, name, src_root, plugin_host_path, tmp_out) -> CollectionResult`; `build_and_collect(docker, workspace, plugin_host_path, tmp_dir, protected) -> (image_id, build_log, r1, r2, in_image_hashes)`; exception `BuildError`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_collect.py`:

```python
import sys, pathlib, json
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.collect import parse_collection_result, Docker, collect_once, COLLECT_CMD


def test_parse_clean():
    pj = {"collected": ["t.py::a", "t.py::b"], "collect_errors": [],
          "skipped_modules": [], "deselected": []}
    r = parse_collection_result(0, pj)
    assert r.exit_code == 0 and r.collected == ("t.py::a", "t.py::b") and r.collected_count == 2


def test_parse_nonzero_exit_with_ids_and_errors():
    pj = {"collected": ["t.py::a"], "collect_errors": ["t.py"], "skipped_modules": [],
          "deselected": ["t.py::slow"]}
    r = parse_collection_result(2, pj)
    assert r.exit_code == 2 and r.collect_errors == ("t.py",) and r.deselected == ("t.py::slow",)


def test_docker_build_argv():
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(argv)
        return 0, "built"

    d = Docker(run=fake_run)
    rc, log = d.build("mytag", "/ctx")
    assert rc == 0 and log == "built"
    assert calls[0] == ["docker", "build", "-t", "mytag", "/ctx"]


def test_docker_exec_hardened_run_and_env():
    calls = []

    def fake_run(argv, timeout=None):
        calls.append(argv)
        return 0, ""

    d = Docker(run=fake_run)
    d.run_detached("mytag", "c1", "/src")
    assert "--network" in calls[0] and "none" in calls[0] and "--cap-drop" in calls[0]
    d.exec("c1", ["echo", "hi"], env={"K": "V"})
    assert calls[1][:4] == ["docker", "exec", "-e", "K=V"]


def test_collect_once_reads_plugin_json(tmp_path):
    canned = {"collected": ["t.py::a"], "collect_errors": [], "skipped_modules": [],
              "deselected": []}

    class FakeDocker:
        def exec(self, name, argv, env=None, timeout=None):
            return 0, ""

        def cp_in(self, name, src, dst):
            pass

        def cp_out(self, name, src, dst):
            with open(dst, "w") as f:
                json.dump(canned, f)

    r = collect_once(FakeDocker(), "c1", "/src", "/plugin.py", str(tmp_path / "r.json"))
    assert r.exit_code == 0 and r.collected == ("t.py::a",)
    assert "manifest_collect_plugin" in COLLECT_CMD
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder.collect'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/collect.py`:

```python
from __future__ import annotations

import json
import os
import subprocess

from src.manifest_builder.protected import hash_in_image
from src.manifest_builder.types import CollectionResult

COLLECT_CMD = "pytest --collect-only -q -p no:cacheprovider -p manifest_collect_plugin"
HARDENED = ["--network", "none", "--cpus", "2", "--memory", "4g", "--pids-limit", "512",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL"]


class BuildError(RuntimeError):
    pass


def _default_run(argv, timeout=None):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_collection_result(exit_code: int, plugin_json: dict) -> CollectionResult:
    return CollectionResult(
        exit_code=exit_code,
        collected=tuple(plugin_json.get("collected", [])),
        collect_errors=tuple(plugin_json.get("collect_errors", [])),
        skipped_modules=tuple(plugin_json.get("skipped_modules", [])),
        deselected=tuple(plugin_json.get("deselected", [])),
    )


class Docker:
    def __init__(self, run=None):
        self._run = run or _default_run

    def build(self, tag, context_dir):
        return self._run(["docker", "build", "-t", tag, context_dir], timeout=3600)

    def image_id(self, tag):
        rc, out = self._run(["docker", "image", "inspect", "-f", "{{.Id}}", tag])
        return out.strip() if rc == 0 else ""

    def run_detached(self, tag, name, workdir):
        self._run(["docker", "run", "-d", "--name", name, *HARDENED, "-w", workdir,
                   tag, "sleep", "infinity"])

    def exec(self, name, argv, env=None, timeout=None):
        cmd = ["docker", "exec"]
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [name, *argv]
        return self._run(cmd, timeout=timeout)

    def cp_in(self, name, src, dst):
        self._run(["docker", "cp", src, f"{name}:{dst}"])

    def cp_out(self, name, src, dst):
        self._run(["docker", "cp", f"{name}:{src}", dst])

    def rm(self, name):
        self._run(["docker", "rm", "-f", name])


def collect_once(docker, name, src_root, plugin_host_path, tmp_out) -> CollectionResult:
    docker.exec(name, ["mkdir", "-p", "/manifest"])
    docker.cp_in(name, plugin_host_path, "/manifest/manifest_collect_plugin.py")
    env = {"PYTHONPATH": "/manifest", "MANIFEST_COLLECT_OUT": "/manifest/out.json",
           "PYTHONDONTWRITEBYTECODE": "1"}
    rc, _ = docker.exec(name, ["bash", "-lc", f"cd {src_root} && {COLLECT_CMD} {src_root}"],
                        env=env, timeout=1800)
    docker.cp_out(name, "/manifest/out.json", tmp_out)
    with open(tmp_out) as f:
        pj = json.load(f)
    return parse_collection_result(rc, pj)


def build_and_collect(docker, workspace, plugin_host_path, tmp_dir, protected):
    tag = f"manifest-{workspace.slug}"
    name = tag + "-run"
    build_rc, build_log = docker.build(tag, workspace.path)
    if build_rc != 0:
        raise BuildError(build_log)
    img = docker.image_id(tag)
    docker.rm(name)
    docker.run_detached(tag, name, workspace.src_root)
    try:
        in_img = hash_in_image(lambda argv: docker.exec(name, argv), workspace.src_root, protected)
        r1 = collect_once(docker, name, workspace.src_root, plugin_host_path,
                          os.path.join(tmp_dir, "r1.json"))
        r2 = collect_once(docker, name, workspace.src_root, plugin_host_path,
                          os.path.join(tmp_dir, "r2.json"))
    finally:
        docker.rm(name)
    return img, build_log, r1, r2, in_img
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_collect.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/collect.py tests/manifest_builder/test_collect.py
git commit -m "feat(manifest): docker adapter + parse + build_and_collect (injectable run)"
```

---

### Task 6: Workspace preparation

**Files:**
- Create: `src/manifest_builder/workspace.py`
- Test: `tests/manifest_builder/test_workspace.py`

**Interfaces:**
- Consumes: `compute_protected`, `hash_host` (Task 3).
- Produces: frozen `Workspace(path, slug, repo_url, commit_sha, src_root, protected, pristine_hashes, base_image, dockerfile_text)`; `SEED_DOCKERFILE:str`; `prepare_workspace(repo_url, commit_sha, dest, base_image="python:3.11-slim") -> Workspace`; `save_state(ws) -> None`; `load_state(path) -> Workspace`; `repo_slug(repo_url) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_workspace.py`:

```python
import sys, pathlib, subprocess
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import workspace as W


def _origin(tmp_path):
    o = tmp_path / "origin"
    o.mkdir()
    subprocess.run(["git", "-C", str(o), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(o), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(o), "config", "user.name", "t"], check=True)
    (o / "pkg.py").write_text("x = 1\n")
    (o / "test_a.py").write_text("def test_a():\n    assert 1\n")
    subprocess.run(["git", "-C", str(o), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(o), "commit", "-qm", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(o), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return str(o), sha


def test_prepare_clones_pins_and_seeds(tmp_path):
    origin, sha = _origin(tmp_path)
    dest = str(tmp_path / "wt")
    ws = W.prepare_workspace(f"file://{origin}", sha, dest, base_image="python:3.11-slim")
    assert ws.commit_sha == sha
    assert "Dockerfile" not in ws.protected and "pkg.py" in ws.protected
    df = (pathlib.Path(dest) / "Dockerfile").read_text()
    assert "COPY . /src" in df and "python:3.11-slim" in df
    assert ws.pristine_hashes["pkg.py"].startswith("sha256:")


def test_save_and_load_state_roundtrip(tmp_path):
    origin, sha = _origin(tmp_path)
    dest = str(tmp_path / "wt")
    ws = W.prepare_workspace(f"file://{origin}", sha, dest)
    W.save_state(ws)
    ws2 = W.load_state(dest)
    assert ws2.protected == ws.protected and ws2.pristine_hashes == ws.pristine_hashes
    assert ws2.commit_sha == sha
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_workspace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder.workspace'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/workspace.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_workspace.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/workspace.py tests/manifest_builder/test_workspace.py
git commit -m "feat(manifest): workspace prep (clone@sha, seed Dockerfile, state save/load)"
```

---

### Task 7: Agent runner seam

**Files:**
- Create: `src/manifest_builder/runner.py`
- Test: `tests/manifest_builder/test_runner.py`

**Interfaces:**
- Produces: frozen `AgentResult(transcript_path:str|None, claimed_done:bool, raw_stdout:str)`; `AgentRunner` (Protocol) with `run(*, cwd, prompt, autonomous) -> AgentResult`; `ClaudeRunner(argv_template=None, run=None)`; `FakeRunner(edit_fn=None)`; `TASK_PROMPT:str` (the maximize-collection instruction).

Note: the agent is **Claude Code** run headlessly (`claude -p`) with `--dangerously-skip-permissions` for fully autonomous execution. `DEFAULT_CLAUDE_ARGV` holds `claude -p "<prompt>" --dangerously-skip-permissions --model <model> --output-format stream-json --verbose`, run with the subprocess CWD set to the workspace (so the agent edits the Dockerfile and runs `./verify` in place — Claude Code has no `--cwd` flag). Overridable via `$MANIFEST_AGENT_CMD` (space-split) or the `model`/`argv_template` constructor args. Default model `opus`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_runner.py`:

```python
import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.runner import ClaudeRunner, FakeRunner, TASK_PROMPT


def test_claude_argv_autonomous_headless(tmp_path):
    calls = []

    def fake_run(argv, timeout=None, cwd=None):
        calls.append((argv, cwd))
        return 0, '{"type":"result"}\n'

    r = ClaudeRunner(run=fake_run)
    res = r.run(cwd=str(tmp_path), prompt="do it", autonomous=True)
    argv, cwd = calls[0]
    assert argv[0] == "claude"
    assert "-p" in argv and "do it" in argv
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--output-format" in argv and "stream-json" in argv and "--verbose" in argv
    assert cwd == str(tmp_path)                       # runs IN the workspace (no --cwd flag)
    assert res.claimed_done is True
    assert res.transcript_path and pathlib.Path(res.transcript_path).exists()


def test_fake_runner_applies_edit(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n")

    def edit(cwd):
        p = pathlib.Path(cwd) / "Dockerfile"
        p.write_text(p.read_text() + "RUN pip install pytest\n")

    res = FakeRunner(edit_fn=edit).run(cwd=str(tmp_path), prompt="x", autonomous=True)
    assert res.claimed_done is True
    assert "RUN pip install pytest" in (tmp_path / "Dockerfile").read_text()


def test_task_prompt_states_dockerfile_only_and_maximize():
    assert "Dockerfile" in TASK_PROMPT and "verify" in TASK_PROMPT.lower()
    assert "maxim" in TASK_PROMPT.lower()
    assert "service" in TASK_PROMPT.lower() and "client library" in TASK_PROMPT.lower()
    assert "import_skipped" in TASK_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder.runner'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/runner.py`:

```python
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Protocol

# Claude Code headless invocation. Placeholders substituted per run: {prompt}, {model}
# ({cwd} is honored too if a $MANIFEST_AGENT_CMD override includes it). The subprocess runs
# with CWD set to the workspace (Claude Code has no --cwd flag), so the agent edits the
# Dockerfile and runs ./verify in place. --dangerously-skip-permissions = fully autonomous
# (bypasses ALL permission prompts, may run docker/pip); stream-json + --verbose = JSONL
# transcript. Overridable via $MANIFEST_AGENT_CMD (space-split).
#   claude -p "<prompt>" --dangerously-skip-permissions --model <model>
#          --output-format stream-json --verbose
DEFAULT_CLAUDE_ARGV = ["claude", "-p", "{prompt}", "--dangerously-skip-permissions",
                       "--model", "{model}", "--output-format", "stream-json", "--verbose"]

TASK_PROMPT = """\
You are configuring a reproducible test-COLLECTION environment for a Python repository. Your ONLY \
editable file is `Dockerfile` in this directory. Your goal: make the repository's full pytest suite \
collect cleanly and maximally.

SUCCESS CRITERION. Run `./verify`. It builds your Dockerfile from scratch, runs `pytest \
--collect-only` inside the image twice, and reports:
- `accepted: true` when collection is clean (no collection errors) and stable across both runs. REQUIRED.
- `collected=N` — number of tests collected. MAXIMIZE this.
- `import_skipped=[modules]` — modules pytest skipped at import time, usually a missing optional \
dependency hiding real tests.
You are done when `./verify` reports accepted AND `collected` is as high as it will go — i.e. \
`import_skipped` contains only modules that are genuinely optional or whose dependency truly cannot \
be installed. A clean collection that hides half the suite behind missing deps is a FAILURE, not a pass.

THE ONLY LEVER IS THE ENVIRONMENT. `pytest --collect-only` works by IMPORTING every test module, so \
collection fails or shrinks only because the environment is missing something: a collection error \
(ImportError/ModuleNotFoundError) means a dependency isn't installed; an import-skipped module means \
an optional dep behind importorskip(...) isn't installed. The fix is always to install the missing \
dependency in the Dockerfile — read the failing import in the traceback, find the PyPI or apt package \
that provides it, and add it.

SERVICES (databases, brokers, etc.). `pytest --collect-only` only imports modules — it never runs \
tests or fixtures — so tests needing a live service (Redis, Postgres, RabbitMQ, ...) to PASS still \
collect fine without that service running. Install the service's Python CLIENT LIBRARY (e.g. redis, \
psycopg2-binary, pika) when a module fails to import it, but do NOT try to start the actual \
database/broker: it isn't needed for collection, and collection runs with NO network access. The one \
exception is a module that opens a connection at import time (top-level code, not inside a test or \
fixture) — live services can't be provided during collection, so leave those in import_skipped, \
install the client library, and move on.

RULES.
- Edit ONLY the `Dockerfile`. Do NOT touch tests, conftest.py, pyproject.toml, pytest.ini, setup.cfg, \
tox.ini, or any source file. The harness restores all of these to their pinned originals and \
hash-checks them before certifying — any edit you make is reverted and rejected, so it cannot help you.
- Do NOT fake a clean collection by hiding tests: no --ignore/-k/-m/--deselect, no \
collect_ignore/norecursedirs, no deleting or emptying test files or narrowing paths. All rejected. \
The only path that works is installing dependencies.
- Do NOT install anything that randomizes collection (e.g. pytest-randomly); the node-ID set must be \
identical across both runs.
- Do NOT run test bodies — only collection matters; tests never need to pass, only to be importable.
- The Dockerfile must build cleanly from scratch; no reliance on host state.

SUGGESTED WORKFLOW. Run `./verify` -> read the first traceback -> install the missing import in the \
Dockerfile, preferring the repo's DECLARED test/dev groups first (`pip install -e .[test]`/`[dev]`/\
`[all]`, requirements-dev.txt, test-requirements.txt) and `apt-get install` for C-library imports -> \
re-run. Repeat until accepted and `import_skipped` is minimal. Declared dependency groups usually \
close most collection gaps at once.
"""


@dataclass(frozen=True)
class AgentResult:
    transcript_path: str | None
    claimed_done: bool
    raw_stdout: str


class AgentRunner(Protocol):
    def run(self, *, cwd: str, prompt: str, autonomous: bool) -> AgentResult: ...


def _default_run(argv, timeout=None, cwd=None):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class ClaudeRunner:
    def __init__(self, *, model="opus", argv_template=None, run=None):
        env = os.environ.get("MANIFEST_AGENT_CMD")
        self.argv_template = argv_template or (env.split() if env else DEFAULT_CLAUDE_ARGV)
        self.model = model
        self._run = run or _default_run

    def run(self, *, cwd, prompt, autonomous):
        # Record the prompt for provenance; pass it inline via -p.
        with open(os.path.join(cwd, ".manifest_prompt.txt"), "w") as f:
            f.write(prompt)
        argv = [a.format(cwd=cwd, prompt=prompt, model=self.model) for a in self.argv_template]
        # Run IN the workspace so the agent edits the Dockerfile / runs ./verify in place.
        rc, out = self._run(argv, timeout=3600, cwd=cwd)
        transcript = os.path.join(cwd, ".manifest_agent_transcript.jsonl")
        with open(transcript, "w") as f:
            f.write(out)   # --output-format stream-json => JSONL event stream
        return AgentResult(transcript_path=transcript, claimed_done=(rc == 0), raw_stdout=out)


class FakeRunner:
    def __init__(self, edit_fn=None):
        self.edit_fn = edit_fn

    def run(self, *, cwd, prompt, autonomous):
        if self.edit_fn:
            self.edit_fn(cwd)
        return AgentResult(transcript_path=None, claimed_done=True, raw_stdout="fake")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_runner.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/manifest_builder/runner.py tests/manifest_builder/test_runner.py
git commit -m "feat(manifest): AgentRunner seam (ClaudeRunner + FakeRunner) + task prompt"
```

---

### Task 8: Orchestration CLI (`certify` / `verify` / `build` / `corpus`)

**Files:**
- Create: `src/manifest_builder/__main__.py`
- Test: `tests/manifest_builder/test_orchestrate.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `certify(docker, workspace, plugin_path, tmp_dir) -> (Verdict, dict, str, r1, r2)` returning `(verdict, certificate, build_log, r1, r2)`; `build_one(repo_url, sha, out_dir, runner, docker, *, attempts=3, base_image="python:3.11-slim") -> dict`; `main(argv=None) -> int` with subcommands `verify --workspace <p>`, `build --repo-url <u> --sha <s> [--out ...] [--attempts N]`, `corpus --corpus <json> [--out ...]`.

- [ ] **Step 1: Write the failing test**

Create `tests/manifest_builder/test_orchestrate.py`:

```python
import sys, pathlib, subprocess, json
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import workspace as W
from src.manifest_builder.__main__ import certify, build_one
from src.manifest_builder.runner import FakeRunner


def _origin(tmp_path):
    o = tmp_path / "origin"; o.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(o), *a], check=True)
    (o / "pkg.py").write_text("x = 1\n")
    (o / "test_a.py").write_text("def test_a():\n    assert 1\ndef test_b():\n    assert 1\n")
    subprocess.run(["git", "-C", str(o), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(o), "commit", "-qm", "init"], check=True)
    sha = subprocess.run(["git", "-C", str(o), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return f"file://{o}", sha


class FakeDocker:
    """Simulates a clean, stable build+collect whose in-image hashes match pristine."""
    def __init__(self, ws):
        self._ws = ws

    def build(self, tag, ctx):
        return 0, "build ok"

    def image_id(self, tag):
        return "sha256:fakeimg"

    def run_detached(self, tag, name, workdir):
        pass

    def rm(self, name):
        pass

    def exec(self, name, argv, env=None, timeout=None):
        if argv and argv[0] == "sha256sum":
            # echo pristine host hashes back (protected_ok == True)
            lines = []
            for p in argv[1:]:
                rel = p[len(self._ws.src_root) + 1:]
                digest = self._ws.pristine_hashes[rel].split(":", 1)[1]
                lines.append(f"{digest}  {p}")
            return 0, "\n".join(lines)
        return 0, ""

    def cp_in(self, name, src, dst):
        pass

    def cp_out(self, name, src, dst):
        with open(dst, "w") as f:
            json.dump({"collected": ["test_a.py::test_a", "test_a.py::test_b"],
                       "collect_errors": [], "skipped_modules": [], "deselected": []}, f)


def test_certify_certifies_clean_env(tmp_path):
    repo_url, sha = _origin(tmp_path)
    ws = W.prepare_workspace(repo_url, sha, str(tmp_path / "wt"))
    plugin = str(_ROOT / "src" / "manifest_builder" / "collect_plugin.py")
    verdict, cert, log, r1, r2 = certify(FakeDocker(ws), ws, plugin, str(tmp_path / "tmp"))
    assert verdict.accepted and cert["status"] == "CERTIFIED"
    assert cert["manifest_size"] == 2


def test_build_one_emits_artifacts(tmp_path):
    repo_url, sha = _origin(tmp_path)
    out = tmp_path / "art"
    # patch prepare_workspace's docker with our fake by driving build_one directly
    ws_holder = {}

    def edit(cwd):
        ws_holder["cwd"] = cwd   # agent "does nothing" — seed already collects cleanly

    class DockerFactory:
        def __call__(self, ws):
            return FakeDocker(ws)

    summary = build_one(repo_url, sha, str(out), FakeRunner(edit_fn=edit),
                        docker_factory=DockerFactory(), attempts=1,
                        workdir=str(tmp_path / "wt2"))
    assert summary["status"] == "CERTIFIED"
    art_dir = pathlib.Path(summary["artifacts_dir"])
    assert (art_dir / "collected-nodeids.json").exists()
    assert json.load(open(art_dir / "collected-nodeids.json")) == \
        ["test_a.py::test_a", "test_a.py::test_b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/manifest_builder/test_orchestrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.manifest_builder.__main__'` (or import error on `certify`).

- [ ] **Step 3: Write minimal implementation**

Create `src/manifest_builder/__main__.py`:

```python
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import certificate as C
from src.manifest_builder import workspace as W
from src.manifest_builder.collect import Docker, build_and_collect, BuildError
from src.manifest_builder.gate import accept, pick_best
from src.manifest_builder.protected import restore_pristine, source_tree_sha256
from src.manifest_builder.runner import ClaudeRunner, TASK_PROMPT

_PLUGIN = str(_REPO_ROOT / "src" / "manifest_builder" / "collect_plugin.py")


def certify(docker, ws, plugin_path, tmp_dir):
    os.makedirs(tmp_dir, exist_ok=True)
    restore_pristine(ws.path)
    try:
        image_id, build_log, r1, r2, in_img = build_and_collect(
            docker, ws, plugin_path, tmp_dir, ws.protected)
    except BuildError as e:
        from src.manifest_builder.types import CollectionResult, Verdict
        empty = CollectionResult(exit_code=1)
        v = Verdict(False, ("docker build failed",), None, 0)
        cert = C.build_certificate(v, empty, empty, repo_url=ws.repo_url, commit_sha=ws.commit_sha,
            base_image=ws.base_image, base_image_digest="", collect_command="",
            source_tree_sha256=source_tree_sha256(ws.pristine_hashes),
            protected_file_hashes=ws.pristine_hashes, dockerfile_text=ws.dockerfile_text,
            image_id="", agent_meta={})
        return v, cert, str(e), empty, empty
    protected_ok = (in_img == ws.pristine_hashes)
    verdict = accept(r1, r2, protected_ok)
    from src.manifest_builder.collect import COLLECT_CMD
    dockerfile_text = (Path(ws.path) / "Dockerfile").read_text()
    cert = C.build_certificate(
        verdict, r1, r2, repo_url=ws.repo_url, commit_sha=ws.commit_sha, base_image=ws.base_image,
        base_image_digest=image_id, collect_command=COLLECT_CMD,
        source_tree_sha256=source_tree_sha256(ws.pristine_hashes),
        protected_file_hashes=in_img, dockerfile_text=dockerfile_text, image_id=image_id,
        agent_meta={"runner": "claude code", "model": "opus"})
    return verdict, cert, build_log, r1, r2


def build_one(repo_url, sha, out_dir, runner, docker=None, *, docker_factory=None,
              attempts=3, base_image="python:3.11-slim", workdir=None):
    workdir = workdir or tempfile.mkdtemp(prefix="manifest-wt-")
    ws = W.prepare_workspace(repo_url, sha, workdir, base_image=base_image)
    dk = docker or (docker_factory(ws) if docker_factory else Docker())
    best = None
    last_transcript = None
    for _ in range(attempts):
        agent_res = runner.run(cwd=ws.path, prompt=TASK_PROMPT, autonomous=True)
        last_transcript = agent_res.transcript_path
        with tempfile.TemporaryDirectory() as td:
            verdict, cert, build_log, r1, r2 = certify(dk, ws, _PLUGIN, td)
        if best is None or (verdict.accepted and (not best[0].accepted or
                            verdict.collected_count > best[0].collected_count)):
            best = (verdict, cert, build_log, r1, r2)
        if verdict.accepted:
            break
    verdict, cert, build_log, r1, r2 = best
    art_dir = os.path.join(out_dir, ws.slug, sha)
    W.save_state(ws)
    _copy_dockerfile(ws, art_dir)
    C.write_artifacts(art_dir, verdict, cert, r1, r2, build_log, transcript_src=last_transcript)
    return {"repo_url": repo_url, "sha": sha, "status": cert["status"],
            "manifest_size": cert["manifest_size"], "artifacts_dir": art_dir}


def _copy_dockerfile(ws, art_dir):
    os.makedirs(art_dir, exist_ok=True)
    with open(os.path.join(art_dir, "Dockerfile"), "w") as f:
        f.write((Path(ws.path) / "Dockerfile").read_text())


def _cmd_verify(args):
    ws = W.load_state(args.workspace)
    with tempfile.TemporaryDirectory() as td:
        verdict, cert, _, _, _ = certify(Docker(), ws, _PLUGIN, td)
    # Surface the maximization signal to the agent: not just accepted/reasons, but the collected
    # count and which modules were skipped at import (the list to keep driving down).
    print(json.dumps({"accepted": verdict.accepted, "reasons": list(verdict.reasons),
                      "collected": verdict.collected_count,
                      "import_skipped": cert["completeness"]["skipped_modules"]}))
    return 0 if verdict.accepted else 1


def _cmd_build(args):
    summary = build_one(args.repo_url, args.sha, args.out, ClaudeRunner(), attempts=args.attempts)
    print(json.dumps(summary, indent=1))
    return 0 if summary["status"] == "CERTIFIED" else 1


def _cmd_corpus(args):
    data = json.load(open(args.corpus))
    repos = data.get("repos", data)
    rc = 0
    for r in repos:
        url = r.get("clone_url") or ("https://github.com/%s" % r["full_name"])
        sha = r.get("commit")
        if not sha:
            print(f"SKIP {url}: no commit", file=sys.stderr); rc = 1; continue
        summary = build_one(url, sha, args.out, ClaudeRunner(), attempts=args.attempts)
        print(json.dumps(summary))
        rc = rc or (0 if summary["status"] == "CERTIFIED" else 1)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.manifest_builder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify"); v.add_argument("--workspace", required=True)
    v.set_defaults(fn=_cmd_verify)
    b = sub.add_parser("build")
    b.add_argument("--repo-url", required=True); b.add_argument("--sha", required=True)
    b.add_argument("--out", default="artifacts"); b.add_argument("--attempts", type=int, default=3)
    b.set_defaults(fn=_cmd_build)
    c = sub.add_parser("corpus")
    c.add_argument("--corpus", required=True); c.add_argument("--out", default="artifacts")
    c.add_argument("--attempts", type=int, default=3); c.set_defaults(fn=_cmd_corpus)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/manifest_builder/test_orchestrate.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the whole module suite + commit**

Run: `python -m pytest tests/manifest_builder/ -v`
Expected: PASS (all tasks' tests green).

```bash
git add src/manifest_builder/__main__.py tests/manifest_builder/test_orchestrate.py
git commit -m "feat(manifest): orchestration CLI (certify/verify/build/corpus) + keep-best loop"
```

---

### Task 9: Live smoke on the VM (ground-truth validation)

**Files:**
- Create: `docs/superpowers/artifacts/manifest_builder/README-smoke.md` (runbook + recorded results)

This task has no unit test; its "test" is that the certified manifest sizes match the known pristine collection for two digest-pinned ground-truth repos (`iniconfig` → 42, `tomli` → 16 from `swesmith-gold-manifest-investigation`). Run on the **x86_64 VM** (Docker + amd64).

> **Claude Code autonomy precondition — check before the first agent run.** `--dangerously-skip-permissions` makes Claude Code fully autonomous: it bypasses **all** permission prompts, so the agent can run `./verify` (which runs `docker build`/`docker run`) and `pip install` while iterating with no interaction — one flag covers both the "may it ask" and "may it execute" axes (simpler than grok's two-axis approval/sandbox split). Preconditions on the VM: (1) `claude` is installed and **authenticated** (the VM already has Claude Code credentials); (2) the user running it can reach the Docker daemon; (3) the account/CLI has the chosen `--model` (default `opus`) — else override via `$MANIFEST_AGENT_CMD` or `ClaudeRunner(model=...)`. This concerns only the *agent's* ability to drive docker while iterating — the harness's own certification always collects under `--network none` regardless.

- [ ] **Step 1: Sync the module to the VM**

```bash
# from local repo root
rsync -a src/manifest_builder tests/manifest_builder \
  <vm>:/opt/manifest_builder/ 2>/dev/null || \
  scp -r src/manifest_builder <vm>:/opt/manifest_builder/src/
```

- [ ] **Step 2: Run the pure suite on the VM (no Docker) to confirm the port**

Run (on VM): `cd /opt/manifest_builder && python -m pytest tests/manifest_builder -v -k "not orchestrate or certify"`
Expected: gate/certificate/protected/plugin/collect/workspace/runner tests PASS.

- [ ] **Step 3: Build a manifest for a small ground-truth repo**

Pick a small pure-Python repo with a known pristine collection (e.g. `pytest-dev/iniconfig` at a pinned SHA). Run:

```bash
python -m src.manifest_builder build \
  --repo-url https://github.com/pytest-dev/iniconfig \
  --sha <PINNED_SHA> --out /opt/manifest_out --attempts 3
```

Expected: prints `{"status": "CERTIFIED", "manifest_size": <N>, ...}`.

- [ ] **Step 4: Verify against ground truth**

Check `collection-certificate.json`:
- `status == "CERTIFIED"`, both `runs[].exit_code == 0`, `runs[0].collected_count == runs[1].collected_count`.
- `manifest_size` equals the independently-known pristine collection (iniconfig: 42 including whitespace-param IDs; tomli: 16). Cross-check by running the plugin directly in the certified image:

```bash
docker run --rm --network none -w /src manifest-iniconfig \
  bash -lc 'MANIFEST_COLLECT_OUT=/tmp/o.json PYTHONPATH=/manifest \
    pytest --collect-only -q -p manifest_collect_plugin /src; cat /tmp/o.json'
```

- [ ] **Step 5: Record results + commit the runbook**

Write the two repos' `manifest_size`, certificate hashes, and any deviation into `README-smoke.md`.

```bash
git add docs/superpowers/artifacts/manifest_builder/README-smoke.md
git commit -m "docs(manifest): VM ground-truth smoke runbook + recorded iniconfig/tomli results"
```

---

## Self-Review

**Spec coverage:**
- Substrate = Docker (§2) → Tasks 5/8/9 (Docker adapter, hardened flags, VM smoke). ✓
- `verify == certify` one code path (§4) → Task 8 `certify` shared by `_cmd_verify` and `build_one`. ✓
- 4-clause accept predicate (§5) → Task 1 `accept`. ✓
- Maximization + keep-best (§5) → Task 1 `pick_best`, Task 8 `build_one` loop. ✓
- Protected set + restore + in-image hash (§6) → Task 3. ✓
- Certificate + completeness + artifacts (§7/§8) → Task 2. ✓
- AgentRunner seam + claude-code binding + task prompt (§9) → Task 7. ✓
- The five required tests (§10): (1) partial-nonzero-exit → `test_partial_collection_nonzero_exit_rejects` + `test_broken_import_nonzero_exit_with_partial_collection`; (2) protected-mod → `test_protected_modified_rejects` + `test_restore_reverts_...`; (3) unstable → `test_unstable_nodeids_rejects`; (4) deselected → `test_...records_skips_and_deselects` + `test_accepts_despite_author_skips_and_deselects`; (5) importorskip → `test_importorskip_hidden_module_recorded_not_collected` + completeness recording. ✓
- Ground-truth pilots (§10) → Task 9. ✓
- Corpus batch over pinned dataset (§13) → Task 8 `_cmd_corpus`. ✓
- Package layout (§13) → Tasks 1–8 create every listed file. ✓

**Placeholder scan:** none. The agent is Claude Code headless (`DEFAULT_CLAUDE_ARGV` = `claude -p … --dangerously-skip-permissions --model opus --output-format stream-json --verbose`), overridable via `$MANIFEST_AGENT_CMD`; every step has complete code. No `TODO`/"handle edge cases"/bare prose steps.

**Type consistency:** `CollectionResult`/`Verdict` fields match across gate/certificate/collect/__main__. `Docker` method names (`build/image_id/run_detached/exec/cp_in/cp_out/rm`) consistent between Task 5 and the FakeDocker in Task 8. `certify` return tuple `(verdict, cert, build_log, r1, r2)` consumed identically in `build_one` and `_cmd_verify`. `Workspace` fields consistent across Tasks 6/8.
