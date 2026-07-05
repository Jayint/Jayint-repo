# Go Module Package-Analysis Eval (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an eval-first, offline Go module-closure parser and a deterministic `go list -m all` Docker oracle that measures whether an offline `go.mod`(≥1.17)/`vendor`/`go.work` parse reproduces the toolchain's build list.

**Architecture:** A pure-text parser (`gomod.py`) turns a repo's manifest files into a `{module: version}` closure via an authority ladder (go.work → vendor → gomod-pruned → resolve-required). `run_ours_go.py` emits per-repo OURS JSON; `oracle.py` emits the gold `go list -m all` closure inside a `golang` container; `compare_go.py` scores recall/precision with divergence buckets. No provider wiring, no certify, no cgo (deferred — see spec §8).

**Tech Stack:** Python 3 (stdlib only — `re`, `json`, `pathlib`, `subprocess`, `dataclasses`); pytest; Docker + `golang` image for the integration oracle only.

**Spec:** `docs/superpowers/specs/2026-07-05-go-package-eval-slice1-design.md`

## Global Constraints

- **No new runtime dependencies** — stdlib only. Mirror the existing Node eval (`src/eval/language_package_eval/node/`) in structure and idiom.
- **Immutability** — parsed records are `@dataclass(frozen=True)` (per Python coding-style rules). The one exception is the `Closure.packages` dict, which is a working map (documented).
- **OURS side is pure/offline** — no `go` toolchain, no network, no container. Only `oracle.py` touches Docker/network, and it is env-gated.
- **Imports** — tests import `from src.eval.language_package_eval.go.<module> import ...`; the root `tests/conftest.py` already puts the repo root on `sys.path`.
- **Formatting** — `black` + `ruff` clean before each commit.
- **Version gate** — "pruned" means the parsed `go` directive is `>= (1, 17)` as an integer tuple.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/eval/language_package_eval/go/__init__.py` | Package marker + one-line docstring. |
| `src/eval/language_package_eval/go/gomod.py` | All offline parsing + `module_closure` authority ladder. |
| `src/eval/language_package_eval/go/run_ours_go.py` | OURS extractor → per-repo JSON. |
| `src/eval/language_package_eval/go/compare_go.py` | `score_repo` recall/precision + buckets. |
| `src/eval/language_package_eval/go/oracle.py` | Docker `go list -m all` gold closure (env-gated). |
| `tests/eval/language_package_eval/go/__init__.py` | Test package marker. |
| `tests/eval/language_package_eval/go/test_gomod.py` | Parser + `module_closure` unit tests. |
| `tests/eval/language_package_eval/go/test_compare_go.py` | Comparer unit tests. |
| `tests/eval/language_package_eval/go/test_oracle_go.py` | Docker-gated oracle test. |

---

### Task 1: `parse_go_mod` — go.mod → typed record

**Files:**
- Create: `src/eval/language_package_eval/go/__init__.py`
- Create: `src/eval/language_package_eval/go/gomod.py`
- Create: `tests/eval/language_package_eval/go/__init__.py`
- Test: `tests/eval/language_package_eval/go/test_gomod.py`

**Interfaces:**
- Produces: `parse_go_mod(path: str | Path) -> GoMod`, with frozen dataclasses `GoMod(module_path: str, go_version: str, toolchain: str | None, requires: tuple[Require, ...], replaces: tuple[Replace, ...], excludes: tuple[Exclude, ...])`, `Require(path: str, version: str, indirect: bool)`, `Replace(old_path: str, old_version: str | None, new_path: str, new_version: str | None)`, `Exclude(path: str, version: str)`. Also module-private `_strip_comment`, `_go_version_tuple`.

- [ ] **Step 1: Create package markers**

`src/eval/language_package_eval/go/__init__.py`:
```python
"""Go module PACKAGE-layer fidelity eval (slice 1): offline go.mod/vendor/go.work
parse vs the toolchain's ``go list -m all`` build list. Mirrors the Node eval."""
```

`tests/eval/language_package_eval/go/__init__.py`:
```python
```

- [ ] **Step 2: Write the failing test**

`tests/eval/language_package_eval/go/test_gomod.py`:
```python
from __future__ import annotations

from src.eval.language_package_eval.go.gomod import (
    Exclude,
    Replace,
    Require,
    parse_go_mod,
)

GOMOD_BLOCK = """\
module github.com/acme/app

go 1.21

toolchain go1.21.4

require (
    github.com/spf13/cobra v1.8.0
    github.com/inconshreveable/mousetrap v1.1.0 // indirect
)

require github.com/spf13/pflag v1.0.5

replace github.com/old/mod => github.com/new/mod v1.5.0
replace github.com/local/mod => ../local

exclude github.com/bad/mod v1.1.0
"""


def test_parse_go_mod_full(tmp_path):
    p = tmp_path / "go.mod"
    p.write_text(GOMOD_BLOCK)
    gm = parse_go_mod(p)

    assert gm.module_path == "github.com/acme/app"
    assert gm.go_version == "1.21"
    assert gm.toolchain == "go1.21.4"
    assert Require("github.com/spf13/cobra", "v1.8.0", False) in gm.requires
    assert Require("github.com/inconshreveable/mousetrap", "v1.1.0", True) in gm.requires
    assert Require("github.com/spf13/pflag", "v1.0.5", False) in gm.requires
    assert Replace("github.com/old/mod", None, "github.com/new/mod", "v1.5.0") in gm.replaces
    assert Replace("github.com/local/mod", None, "../local", None) in gm.replaces
    assert Exclude("github.com/bad/mod", "v1.1.0") in gm.excludes


def test_parse_go_mod_no_go_directive(tmp_path):
    (tmp_path / "go.mod").write_text("module github.com/x/y\n")
    gm = parse_go_mod(tmp_path / "go.mod")
    assert gm.go_version == ""
    assert gm.requires == ()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py -q`
Expected: FAIL with `ModuleNotFoundError: ...go.gomod` / `ImportError`.

- [ ] **Step 4: Write minimal implementation**

`src/eval/language_package_eval/go/gomod.py`:
```python
"""Offline parsers for Go module manifests + the ``module_closure`` authority
ladder. Pure text/JSON — no ``go`` toolchain, no network. Analog of
``node/lockfile.py``."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_BLOCK_OPEN = re.compile(r"^(require|replace|exclude)\s*\($")
_SINGLE = re.compile(r"^(require|replace|exclude)\s+(.*)$")


@dataclass(frozen=True)
class Require:
    path: str
    version: str
    indirect: bool = False


@dataclass(frozen=True)
class Replace:
    old_path: str
    old_version: str | None
    new_path: str
    new_version: str | None  # None => local filesystem target


@dataclass(frozen=True)
class Exclude:
    path: str
    version: str


@dataclass(frozen=True)
class GoMod:
    module_path: str
    go_version: str
    toolchain: str | None = None
    requires: tuple[Require, ...] = ()
    replaces: tuple[Replace, ...] = ()
    excludes: tuple[Exclude, ...] = ()


def _strip_comment(line: str) -> tuple[str, str]:
    """Split at the first ``//``; returns (code, comment-without-slashes)."""
    idx = line.find("//")
    if idx == -1:
        return line, ""
    return line[:idx], line[idx + 2 :]


def _go_version_tuple(go_version: str) -> tuple[int, ...]:
    """``"1.21"`` -> ``(1, 21)``; ``"go1.21.0"`` -> ``(1, 21, 0)``; ``""`` -> ``()``."""
    cleaned = go_version.lstrip("go")
    return tuple(int(p) for p in cleaned.split(".") if p.isdigit())


def _consume(directive, rest, comment, requires, replaces, excludes) -> None:
    parts = rest.split()
    if directive == "require" and len(parts) >= 2:
        requires.append(Require(parts[0], parts[1], "indirect" in comment.split()))
    elif directive == "exclude" and len(parts) >= 2:
        excludes.append(Exclude(parts[0], parts[1]))
    elif directive == "replace" and "=>" in parts:
        i = parts.index("=>")
        left, right = parts[:i], parts[i + 1 :]
        if not left or not right:
            return
        replaces.append(
            Replace(
                old_path=left[0],
                old_version=left[1] if len(left) > 1 else None,
                new_path=right[0],
                new_version=right[1] if len(right) > 1 else None,
            )
        )


def parse_go_mod(path: str | Path) -> GoMod:
    text = Path(path).read_text()
    module_path = go_version = ""
    toolchain: str | None = None
    requires: list[Require] = []
    replaces: list[Replace] = []
    excludes: list[Exclude] = []
    block: str | None = None

    for raw in text.splitlines():
        code, comment = _strip_comment(raw)
        s = code.strip()
        if not s:
            continue
        if s == ")":
            block = None
            continue
        m = _BLOCK_OPEN.match(s)
        if m:
            block = m.group(1)
            continue
        if block is not None:
            _consume(block, s, comment, requires, replaces, excludes)
            continue
        m = _SINGLE.match(s)
        if m:
            _consume(m.group(1), m.group(2), comment, requires, replaces, excludes)
            continue
        parts = s.split()
        if parts[0] == "module":
            module_path = parts[1]
        elif parts[0] == "go":
            go_version = parts[1]
        elif parts[0] == "toolchain":
            toolchain = parts[1]

    return GoMod(
        module_path=module_path,
        go_version=go_version,
        toolchain=toolchain,
        requires=tuple(requires),
        replaces=tuple(replaces),
        excludes=tuple(excludes),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
black src/eval/language_package_eval/go tests/eval/language_package_eval/go
ruff check --fix src/eval/language_package_eval/go
git add src/eval/language_package_eval/go tests/eval/language_package_eval/go
git commit -m "feat(eval-go): parse_go_mod — go.mod -> typed record (require/replace/exclude, indirect, version gate)"
```

---

### Task 2: Auxiliary parsers — vendor / go.sum / go.work

**Files:**
- Modify: `src/eval/language_package_eval/go/gomod.py` (append functions)
- Test: `tests/eval/language_package_eval/go/test_gomod.py` (append tests)

**Interfaces:**
- Consumes: `_strip_comment` (Task 1).
- Produces: `parse_vendor_modules_txt(path) -> dict[str, str]`, `parse_go_sum(path) -> frozenset[tuple[str, str]]`, `parse_go_work(path) -> tuple[str, ...]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/language_package_eval/go/test_gomod.py`:
```python
from src.eval.language_package_eval.go.gomod import (  # noqa: E402
    parse_go_sum,
    parse_go_work,
    parse_vendor_modules_txt,
)

MODULES_TXT = """\
# github.com/spf13/cobra v1.8.0
## explicit; go 1.15
github.com/spf13/cobra
# github.com/spf13/pflag v1.0.5
## explicit; go 1.12
github.com/spf13/pflag/...
"""


def test_parse_vendor_modules_txt(tmp_path):
    p = tmp_path / "modules.txt"
    p.write_text(MODULES_TXT)
    assert parse_vendor_modules_txt(p) == {
        "github.com/spf13/cobra": "v1.8.0",
        "github.com/spf13/pflag": "v1.0.5",
    }


def test_parse_go_sum_strips_gomod_suffix(tmp_path):
    p = tmp_path / "go.sum"
    p.write_text(
        "github.com/spf13/cobra v1.8.0 h1:aaa=\n"
        "github.com/spf13/cobra v1.8.0/go.mod h1:bbb=\n"
    )
    assert parse_go_sum(p) == frozenset({("github.com/spf13/cobra", "v1.8.0")})


def test_parse_go_work_block_and_single(tmp_path):
    (tmp_path / "go.work").write_text(
        "go 1.21\n\nuse (\n    ./a\n    ./b\n)\n\nuse ./c\n"
    )
    assert parse_go_work(tmp_path / "go.work") == ("./a", "./b", "./c")


def test_parse_go_work_missing_returns_empty(tmp_path):
    assert parse_go_work(tmp_path / "go.work") == ()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py -q`
Expected: FAIL with `ImportError: cannot import name 'parse_go_sum'`.

- [ ] **Step 3: Implement**

Append to `src/eval/language_package_eval/go/gomod.py`:
```python
_WORK_BLOCK_OPEN = re.compile(r"^use\s*\($")
_WORK_SINGLE = re.compile(r"^use\s+(.+)$")


def parse_vendor_modules_txt(path: str | Path) -> dict[str, str]:
    """``{module: version}`` from ``# <module> <version>`` header lines. ``## ``
    annotation lines and package-path lines are skipped. A ``=> replacement``
    tail takes the replacement's trailing version. Corpus vendored entries are
    chosen without replaces (spec §7); ``=>`` handling here is defensive."""
    out: dict[str, str] = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.rstrip()
        if line.startswith("## ") or not line.startswith("# "):
            continue
        parts = line[2:].split()
        if len(parts) < 2:
            continue
        mod = parts[0]
        if "=>" in parts:
            right = parts[parts.index("=>") + 1 :]
            out[mod] = right[-1] if len(right) >= 2 else parts[1]
        else:
            out[mod] = parts[1]
    return out


def parse_go_sum(path: str | Path) -> frozenset[tuple[str, str]]:
    """``{(module, version)}`` cross-check set — a SUPERSET of the closure, never
    the closure itself. ``<mod> <ver>/go.mod <hash>`` collapses to ``(mod, ver)``."""
    p = Path(path)
    if not p.is_file():
        return frozenset()
    out: set[tuple[str, str]] = set()
    for raw in p.read_text().splitlines():
        parts = raw.split()
        if len(parts) >= 2:
            out.add((parts[0], parts[1].split("/")[0]))
    return frozenset(out)


def parse_go_work(path: str | Path) -> tuple[str, ...]:
    """The ``use ./dir`` member directories from a ``go.work``. Empty if absent."""
    p = Path(path)
    if not p.is_file():
        return ()
    members: list[str] = []
    in_block = False
    for raw in p.read_text().splitlines():
        code, _ = _strip_comment(raw)
        s = code.strip()
        if not s:
            continue
        if s == ")":
            in_block = False
            continue
        if _WORK_BLOCK_OPEN.match(s):
            in_block = True
            continue
        if in_block:
            members.append(s)
            continue
        m = _WORK_SINGLE.match(s)
        if m:
            members.append(m.group(1).strip())
    return tuple(members)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go tests/eval/language_package_eval/go
ruff check --fix src/eval/language_package_eval/go
git add src/eval/language_package_eval/go/gomod.py tests/eval/language_package_eval/go/test_gomod.py
git commit -m "feat(eval-go): vendor/modules.txt, go.sum, go.work parsers"
```

---

### Task 3: `module_closure` — the authority ladder

**Files:**
- Modify: `src/eval/language_package_eval/go/gomod.py` (append `Closure`, `module_closure`, helpers)
- Test: `tests/eval/language_package_eval/go/test_gomod.py` (append tests)

**Interfaces:**
- Consumes: `parse_go_mod`, `parse_vendor_modules_txt`, `parse_go_work`, `_go_version_tuple` (Tasks 1-2).
- Produces: `module_closure(repo_dir: str | Path) -> Closure`, with frozen dataclass `Closure(packages: dict[str, str], source: str, go_version: str, toolchain: str | None, replace_local: tuple[str, ...], direct: int, indirect: int, resolve_required: bool)`. `source ∈ {"workspace", "vendor", "gomod-pruned", "resolve-required"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/eval/language_package_eval/go/test_gomod.py`:
```python
from src.eval.language_package_eval.go.gomod import Closure, module_closure  # noqa: E402


def _repo(tmp_path, gomod_text, name="repo"):
    d = tmp_path / name
    d.mkdir()
    (d / "go.mod").write_text(gomod_text)
    return d


def test_closure_pruned_excludes_main_and_counts(tmp_path):
    d = _repo(
        tmp_path,
        "module github.com/acme/app\n\ngo 1.21\n\n"
        "require (\n    github.com/spf13/cobra v1.8.0\n"
        "    github.com/x/y v0.1.0 // indirect\n)\n",
    )
    c = module_closure(d)
    assert c.source == "gomod-pruned"
    assert c.packages == {"github.com/spf13/cobra": "v1.8.0", "github.com/x/y": "v0.1.0"}
    assert c.direct == 1 and c.indirect == 1
    assert c.resolve_required is False


def test_closure_pre_1_17_is_resolve_required(tmp_path):
    d = _repo(
        tmp_path,
        "module github.com/acme/old\n\ngo 1.16\n\n"
        "require github.com/spf13/cobra v1.8.0\n",
    )
    c = module_closure(d)
    assert c.source == "resolve-required"
    assert c.packages == {}
    assert c.resolve_required is True


def test_closure_registry_replace_rewrites_version(tmp_path):
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "replace github.com/x/y => github.com/fork/y v1.2.0\n",
    )
    c = module_closure(d)
    assert c.packages == {"github.com/x/y": "v1.2.0"}
    assert c.replace_local == ()


def test_closure_local_replace_dropped_and_recorded(tmp_path):
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "replace github.com/x/y => ../local\n",
    )
    c = module_closure(d)
    assert c.packages == {}
    assert c.replace_local == ("github.com/x/y",)


def test_closure_exclude_drops_matching_version(tmp_path):
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "exclude github.com/x/y v1.0.0\n",
    )
    c = module_closure(d)
    assert c.packages == {}


def test_closure_vendor_wins_over_gomod(tmp_path):
    d = _repo(tmp_path, "module m\n\ngo 1.13\n\nrequire github.com/x/y v1.0.0\n")
    (d / "vendor").mkdir()
    (d / "vendor" / "modules.txt").write_text("# github.com/x/y v1.0.0\n## explicit\n")
    c = module_closure(d)
    assert c.source == "vendor"
    assert c.packages == {"github.com/x/y": "v1.0.0"}


def test_closure_workspace_unions_members(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "go.work").write_text("go 1.21\n\nuse (\n    ./a\n    ./b\n)\n")
    a = ws / "a"
    a.mkdir()
    (a / "go.mod").write_text(
        "module example.com/a\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
    )
    b = ws / "b"
    b.mkdir()
    (b / "go.mod").write_text(
        "module example.com/b\n\ngo 1.21\n\nrequire github.com/z/w v2.0.0\n"
    )
    c = module_closure(ws)
    assert c.source == "workspace"
    assert c.packages == {"github.com/x/y": "v1.0.0", "github.com/z/w": "v2.0.0"}


def test_closure_workspace_missing_member_taints(tmp_path):
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / "go.work").write_text("go 1.21\n\nuse ./missing\n")
    c = module_closure(ws)
    assert c.source == "resolve-required"
    assert c.resolve_required is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py -q`
Expected: FAIL with `ImportError: cannot import name 'module_closure'`.

- [ ] **Step 3: Implement**

Append to `src/eval/language_package_eval/go/gomod.py`:
```python
@dataclass(frozen=True)
class Closure:
    packages: dict[str, str]           # {module: version}; working map (not hashed)
    source: str                        # workspace | vendor | gomod-pruned | resolve-required
    go_version: str
    toolchain: str | None
    replace_local: tuple[str, ...]     # module keys dropped due to a local replace
    direct: int
    indirect: int
    resolve_required: bool


def _resolve_required(gm: "GoMod") -> Closure:
    return Closure(
        packages={},
        source="resolve-required",
        go_version=gm.go_version,
        toolchain=gm.toolchain,
        replace_local=(),
        direct=0,
        indirect=0,
        resolve_required=True,
    )


def _finalize(gm: "GoMod", pkgs: dict[str, str], source: str) -> Closure:
    """Apply replace/exclude, drop the main module, count direct/indirect."""
    replace_local: list[str] = []
    for r in gm.replaces:
        if r.new_version is None:  # local filesystem replace -> drop
            if pkgs.pop(r.old_path, None) is not None:
                replace_local.append(r.old_path)
        elif r.old_path in pkgs:   # registry replace -> rewrite version, keep old key
            pkgs[r.old_path] = r.new_version
    for e in gm.excludes:
        if pkgs.get(e.path) == e.version:
            pkgs.pop(e.path, None)
    pkgs.pop(gm.module_path, None)
    return Closure(
        packages=pkgs,
        source=source,
        go_version=gm.go_version,
        toolchain=gm.toolchain,
        replace_local=tuple(replace_local),
        direct=sum(1 for r in gm.requires if not r.indirect),
        indirect=sum(1 for r in gm.requires if r.indirect),
        resolve_required=False,
    )


def _workspace_closure(repo: Path, members: tuple[str, ...]) -> Closure:
    merged: dict[str, str] = {}
    replace_local: list[str] = []
    go_version = ""
    for rel in members:
        member = (repo / rel).resolve()
        if not (member / "go.mod").is_file():
            return _resolve_required(GoMod("", go_version))  # missing member taints
        sub = module_closure(member)
        if sub.resolve_required:
            return _resolve_required(GoMod("", sub.go_version))
        merged.update(sub.packages)
        replace_local.extend(sub.replace_local)
        go_version = go_version or sub.go_version
    for rel in members:                     # a member is never an external dep of another
        member = (repo / rel).resolve()
        merged.pop(parse_go_mod(member / "go.mod").module_path, None)
    return Closure(
        packages=merged,
        source="workspace",
        go_version=go_version,
        toolchain=None,
        replace_local=tuple(replace_local),
        direct=0,
        indirect=0,
        resolve_required=False,
    )


def module_closure(repo_dir: str | Path) -> Closure:
    """Offline ``{module: version}`` closure via the authority ladder
    (go.work -> vendor -> gomod-pruned -> resolve-required). Spec §3.1."""
    repo = Path(repo_dir)
    if (repo / "go.work").is_file():
        return _workspace_closure(repo, parse_go_work(repo / "go.work"))
    gm = parse_go_mod(repo / "go.mod")
    vendor = repo / "vendor" / "modules.txt"
    if vendor.is_file():
        pkgs = parse_vendor_modules_txt(vendor)
        return _finalize(gm, pkgs, "vendor")
    if _go_version_tuple(gm.go_version) >= (1, 17):
        pkgs = {r.path: r.version for r in gm.requires}
        return _finalize(gm, pkgs, "gomod-pruned")
    return _resolve_required(gm)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py -q`
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go tests/eval/language_package_eval/go
ruff check --fix src/eval/language_package_eval/go
git add src/eval/language_package_eval/go/gomod.py tests/eval/language_package_eval/go/test_gomod.py
git commit -m "feat(eval-go): module_closure authority ladder (workspace/vendor/pruned/resolve-required + replace/exclude)"
```

---

### Task 4: `run_ours_go.py` — OURS extractor

**Files:**
- Create: `src/eval/language_package_eval/go/run_ours_go.py`
- Test: `tests/eval/language_package_eval/go/test_gomod.py` (append `ours_for_repo` test)

**Interfaces:**
- Consumes: `module_closure`, `parse_go_mod` (Task 1-3).
- Produces: `ours_for_repo(repo_dir: str | Path, target: dict | None = None) -> dict` with keys `packages, package_count, closure_source, go_version, toolchain, direct_count, indirect_count, replace_local, resolve_required, target, project`. Also a `main()` CLI mirroring `run_ours_node.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/eval/language_package_eval/go/test_gomod.py`:
```python
from src.eval.language_package_eval.go.run_ours_go import ours_for_repo  # noqa: E402


def test_ours_for_repo_shape(tmp_path):
    d = _repo(
        tmp_path,
        "module github.com/acme/app\n\ngo 1.21\n\n"
        "require github.com/spf13/cobra v1.8.0\n",
    )
    rec = ours_for_repo(d)
    assert rec["packages"] == {"github.com/spf13/cobra": "v1.8.0"}
    assert rec["package_count"] == 1
    assert rec["closure_source"] == "gomod-pruned"
    assert rec["project"] == "github.com/acme/app"
    assert rec["resolve_required"] is False
    assert rec["target"] == {"goos": "linux", "goarch": "amd64"}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py::test_ours_for_repo_shape -q`
Expected: FAIL with `ModuleNotFoundError: ...run_ours_go`.

- [ ] **Step 3: Implement**

`src/eval/language_package_eval/go/run_ours_go.py`:
```python
#!/usr/bin/env python3
"""OUR side of the Go package-layer eval: parse go.mod(>=1.17)/vendor/go.work into
the offline module build-list closure. Pure text parse: no toolchain, no network
(spec §4). Mirrors ``run_ours_node.py``.

Usage:
    python3 -m src.eval.language_package_eval.go.run_ours_go <repo,repo,...> <out_dir>
where each repo is a subdir of GO_SMOKE_ROOT holding a committed go.mod (+ go.sum,
optionally vendor/modules.txt or go.work). Emits per-repo ``<repo>.json``.

Env knobs: GO_SMOKE_ROOT (corpus dir), GO_TARGET="goos,goarch" (default "linux,amd64").
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))  # repo root

from src.eval.language_package_eval.go.gomod import module_closure, parse_go_mod  # noqa: E402

SMOKE = pathlib.Path(os.environ.get("GO_SMOKE_ROOT", "outputs/graph_fidelity/_smoke_go"))
DEFAULT_TARGET = {"goos": "linux", "goarch": "amd64"}


def _target() -> dict[str, str]:
    raw = os.environ.get("GO_TARGET", "")
    if raw and len(raw.split(",")) == 2:
        goos, goarch = raw.split(",")
        return {"goos": goos, "goarch": goarch}
    return dict(DEFAULT_TARGET)


def _project_name(repo_dir: pathlib.Path) -> str | None:
    gomod = repo_dir / "go.mod"
    if gomod.is_file():
        return parse_go_mod(gomod).module_path or None
    work = repo_dir / "go.work"
    return "<workspace>" if work.is_file() else None


def ours_for_repo(repo_dir: str | pathlib.Path, target: dict | None = None) -> dict:
    """Construction-only OURS closure for one Go repo: {module: version}, offline."""
    repo_dir = pathlib.Path(repo_dir)
    c = module_closure(repo_dir)
    return {
        "packages": dict(c.packages),
        "package_count": len(c.packages),
        "closure_source": c.source,
        "go_version": c.go_version,
        "toolchain": c.toolchain,
        "direct_count": c.direct,
        "indirect_count": c.indirect,
        "replace_local": list(c.replace_local),
        "resolve_required": c.resolve_required,
        "target": target or _target(),
        "project": _project_name(repo_dir),
    }


def main() -> int:
    repos = sys.argv[1].split(",") if len(sys.argv) > 1 else []
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path("/tmp/ours_go")
    out.mkdir(parents=True, exist_ok=True)
    target = _target()
    for name in repos:
        repo_dir = SMOKE / name
        rec = {"repo_dir": name}
        try:
            rec.update(ours_for_repo(repo_dir, target))
            print(f"OK {name}: {rec['package_count']} modules ({rec['closure_source']})")
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"
            print(f"ERR {name}: {rec['error']}")
        (out / f"{name}.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    print(f"DONE ours(go) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/eval/language_package_eval/go/test_gomod.py::test_ours_for_repo_shape -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go tests/eval/language_package_eval/go
ruff check --fix src/eval/language_package_eval/go
git add src/eval/language_package_eval/go/run_ours_go.py tests/eval/language_package_eval/go/test_gomod.py
git commit -m "feat(eval-go): run_ours_go OURS extractor -> per-repo JSON"
```

---

### Task 5: `compare_go.py` — recall/precision + buckets

**Files:**
- Create: `src/eval/language_package_eval/go/compare_go.py`
- Test: `tests/eval/language_package_eval/go/test_compare_go.py`

**Interfaces:**
- Produces: `score_repo(ours: dict, oracle: dict) -> dict`. `ours` has `packages`, `replace_local`, `resolve_required`; `oracle` has `installed`. Returns `recall, precision, vexact, missing, extra, replace_local, resolve_required` (recall/precision are `None` when `resolve_required`).

- [ ] **Step 1: Write the failing tests**

`tests/eval/language_package_eval/go/test_compare_go.py`:
```python
from __future__ import annotations

from src.eval.language_package_eval.go.compare_go import score_repo


def _ours(packages, replace_local=None, resolve_required=False):
    return {
        "packages": packages,
        "replace_local": replace_local or [],
        "resolve_required": resolve_required,
    }


def _oracle(installed):
    return {"installed": installed}


def test_perfect_match_is_recall_precision_one():
    s = score_repo(
        _ours({"github.com/x/y": "v1.0.0", "github.com/a/b": "v2.0.0"}),
        _oracle({"github.com/x/y": "v1.0.0", "github.com/a/b": "v2.0.0"}),
    )
    assert s["recall"] == 1.0 and s["precision"] == 1.0
    assert s["missing"] == [] and s["extra"] == []
    assert s["vexact"] == 2


def test_missing_and_extra_buckets():
    s = score_repo(
        _ours({"github.com/x/y": "v1.0.0", "github.com/only/ours": "v9"}),
        _oracle({"github.com/x/y": "v1.0.0", "github.com/only/oracle": "v3"}),
    )
    assert s["recall"] == 0.5 and s["precision"] == 0.5
    assert s["missing"] == ["github.com/only/oracle"]
    assert s["extra"] == ["github.com/only/ours"]


def test_local_replace_removed_from_both_sides():
    # locally-replaced module is emitted by `go list -m all` but dropped by OURS;
    # it must be excluded from both denominators (spec §6).
    s = score_repo(
        _ours({"github.com/x/y": "v1.0.0"}, replace_local=["github.com/local/m"]),
        _oracle({"github.com/x/y": "v1.0.0", "github.com/local/m": "v0.0.0"}),
    )
    assert s["recall"] == 1.0 and s["precision"] == 1.0
    assert s["replace_local"] == ["github.com/local/m"]


def test_resolve_required_relabels_whole_oracle():
    s = score_repo(
        _ours({}, resolve_required=True),
        _oracle({"github.com/x/y": "v1.0.0"}),
    )
    assert s["recall"] is None and s["precision"] is None
    assert s["resolve_required"] is True
    assert s["missing"] == []  # NOT counted as recall misses
    assert s["resolve_required_missing"] == ["github.com/x/y"]


def test_empty_both_sides_no_zero_division():
    s = score_repo(_ours({}), _oracle({}))
    assert s["recall"] == 1.0 and s["precision"] == 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_compare_go.py -q`
Expected: FAIL with `ModuleNotFoundError: ...compare_go`.

- [ ] **Step 3: Implement**

`src/eval/language_package_eval/go/compare_go.py`:
```python
"""Recall/precision + divergence buckets for the Go package-layer eval, on OURS
(offline closure) vs ORACLE (``go list -m all``). Mirrors ``compare_node.py``."""
from __future__ import annotations


def score_repo(ours: dict, oracle: dict) -> dict:
    replace_local = set(ours.get("replace_local", []))
    o_pkgs = {k: v for k, v in ours["packages"].items() if k not in replace_local}
    g_pkgs = {k: v for k, v in oracle["installed"].items() if k not in replace_local}
    ours_keys, oracle_keys = set(o_pkgs), set(g_pkgs)

    if ours.get("resolve_required"):
        # Whole oracle is a KNOWN offline limitation, not a recall defect (spec §6).
        return {
            "recall": None,
            "precision": None,
            "resolve_required": True,
            "missing": [],
            "extra": [],
            "replace_local": sorted(replace_local),
            "resolve_required_missing": sorted(oracle_keys),
            "vexact": 0,
        }

    inter = ours_keys & oracle_keys
    return {
        "recall": len(inter) / len(oracle_keys) if oracle_keys else 1.0,
        "precision": len(inter) / len(ours_keys) if ours_keys else 1.0,
        "resolve_required": False,
        "missing": sorted(oracle_keys - ours_keys),
        "extra": sorted(ours_keys - oracle_keys),
        "replace_local": sorted(replace_local),
        "vexact": sum(1 for k in inter if o_pkgs[k] == g_pkgs[k]),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/eval/language_package_eval/go/test_compare_go.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go/compare_go.py tests/eval/language_package_eval/go/test_compare_go.py
ruff check --fix src/eval/language_package_eval/go/compare_go.py
git add src/eval/language_package_eval/go/compare_go.py tests/eval/language_package_eval/go/test_compare_go.py
git commit -m "feat(eval-go): compare_go score_repo — recall/precision + missing/extra/replace_local/resolve_required buckets"
```

---

### Task 6: `oracle.py` — Docker `go list -m all` (env-gated)

**Files:**
- Create: `src/eval/language_package_eval/go/oracle.py`
- Test: `tests/eval/language_package_eval/go/test_oracle_go.py`

**Interfaces:**
- Produces: `parse_go_list_m(output: str) -> dict[str, str]` (pure, unit-testable) and `oracle_closure(repo_dir, *, go_image="golang:1.22", vendored=False) -> dict[str, str]` (shells out to Docker). Also a `main()` writing `{installed, repo}` per repo.

- [ ] **Step 1: Write the failing test (pure parser + gated integration)**

`tests/eval/language_package_eval/go/test_oracle_go.py`:
```python
from __future__ import annotations

import os
import shutil

import pytest

from src.eval.language_package_eval.go.oracle import oracle_closure, parse_go_list_m


def test_parse_go_list_m_skips_mains_and_handles_replace():
    # main modules print with no version (workspace-safe); externals print `path version`;
    # a replace prints `old v1 => new v2` -> key old, take replacement version.
    out = (
        "github.com/acme/app\n"                         # main module (no version) -> skip
        "github.com/spf13/cobra v1.8.0\n"
        "github.com/old/mod v1.0.0 => github.com/new/mod v1.2.0\n"
    )
    assert parse_go_list_m(out) == {
        "github.com/spf13/cobra": "v1.8.0",
        "github.com/old/mod": "v1.2.0",
    }


@pytest.mark.skipif(
    os.environ.get("GO_ORACLE_DOCKER") != "1" or shutil.which("docker") is None,
    reason="integration oracle: set GO_ORACLE_DOCKER=1 and have Docker + network",
)
def test_oracle_closure_anchor_matches_ours(tmp_path):
    # Anchor: a clean >=1.17 module -> OURS == ORACLE (Delta 0). Requires the
    # corpus lifted by Task 7 at GO_SMOKE_ROOT.
    from src.eval.language_package_eval.go.compare_go import score_repo
    from src.eval.language_package_eval.go.run_ours_go import SMOKE, ours_for_repo

    anchor = SMOKE / "viper"
    if not (anchor / "go.mod").is_file():
        pytest.skip("anchor corpus not lifted (run Task 7)")
    ours = ours_for_repo(anchor)
    oracle = {"installed": oracle_closure(anchor)}
    s = score_repo(ours, oracle)
    assert s["recall"] == 1.0 and s["precision"] == 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_oracle_go.py::test_parse_go_list_m_skips_mains_and_handles_replace -q`
Expected: FAIL with `ModuleNotFoundError: ...oracle`.

- [ ] **Step 3: Implement**

`src/eval/language_package_eval/go/oracle.py`:
```python
#!/usr/bin/env python3
"""Gold closure for the Go package-layer eval: the toolchain's own build list via
``go list -m all`` inside a golang container. Deterministic — NO agent, unlike the
Python/Node oracles (the MVS build list is emitted directly by the toolchain).
Docker-gated (spec §5).

Usage:
    GO_ORACLE_DOCKER=1 python3 -m src.eval.language_package_eval.go.oracle <repo,...> <out_dir>
Emits per-repo ``<repo>.json`` = {"installed": {module: version}, "repo": name}.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))  # repo root

SMOKE = pathlib.Path(os.environ.get("GO_SMOKE_ROOT", "outputs/graph_fidelity/_smoke_go"))
GO_IMAGE = os.environ.get("GO_IMAGE", "golang:1.22")


def parse_go_list_m(output: str) -> dict[str, str]:
    """``go list -m all`` -> {module: version}. Lines without a version are the
    workspace's main module(s) and are skipped; a ``old v1 => new v2`` line keys
    the ORIGINAL path with the replacement's version (build-list identity)."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[1].startswith("v"):
            continue  # main module (no version) or blank
        mod, ver = parts[0], parts[1]
        if "=>" in parts:
            right = parts[parts.index("=>") + 1 :]
            ver = right[-1] if len(right) >= 2 else ver
        result[mod] = ver
    return result


def oracle_closure(repo_dir, *, go_image: str = GO_IMAGE, vendored: bool = False) -> dict[str, str]:
    repo = pathlib.Path(repo_dir).resolve()
    mode = ["-mod=vendor"] if vendored else []
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{repo}:/src", "-w", "/src",
        go_image, "go", "list", *mode, "-m", "all",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return parse_go_list_m(proc.stdout)


def main() -> int:
    repos = sys.argv[1].split(",") if len(sys.argv) > 1 else []
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path("/tmp/oracle_go")
    out.mkdir(parents=True, exist_ok=True)
    for name in repos:
        repo_dir = SMOKE / name
        vendored = (repo_dir / "vendor" / "modules.txt").is_file()
        rec = {"repo": name}
        try:
            rec["installed"] = oracle_closure(repo_dir, vendored=vendored)
            print(f"OK {name}: {len(rec['installed'])} modules (vendored={vendored})")
        except subprocess.CalledProcessError as exc:
            rec["error"] = exc.stderr.strip()[:500]
            print(f"ERR {name}: {rec['error']}")
        (out / f"{name}.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    print(f"DONE oracle(go) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass (pure parser test only; integration auto-skips)**

Run: `pytest tests/eval/language_package_eval/go/test_oracle_go.py -q`
Expected: PASS (1 passed, 1 skipped).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go/oracle.py tests/eval/language_package_eval/go/test_oracle_go.py
ruff check --fix src/eval/language_package_eval/go/oracle.py
git add src/eval/language_package_eval/go/oracle.py tests/eval/language_package_eval/go/test_oracle_go.py
git commit -m "feat(eval-go): go list -m all Docker oracle + pure parse_go_list_m (deterministic, no agent)"
```

---

### Task 7: Corpus assembly + end-to-end validation

**Files:**
- Create: `outputs/graph_fidelity/_smoke_go/<repo>/{go.mod,go.sum}` (manifest-only lifts)
- Create: `src/eval/language_package_eval/go/lift_corpus.sh` (documents exact lift commands)
- Create: `docs/superpowers/loops/2026-07-05-go-eval-slice1-result.md` (result writeup)

**Interfaces:**
- Consumes: `run_ours_go.main`, `oracle.main`, `compare_go.score_repo`.

**Note on manifest-only:** both OURS and `go list -m all` need only the module graph, so each corpus entry is just `go.mod` (+ `go.sum`), lifted from raw GitHub — no clones (spec §5/§7). Pin exact tags so the run is reproducible.

- [ ] **Step 1: Lift the corpus manifests**

`src/eval/language_package_eval/go/lift_corpus.sh`:
```bash
#!/usr/bin/env bash
# Lift manifest-only Go corpus into GO_SMOKE_ROOT. Manifests only — no clones.
# Each repo pinned to a tag so `go list -m all` is reproducible.
set -euo pipefail
ROOT="${GO_SMOKE_ROOT:-outputs/graph_fidelity/_smoke_go}"
mkdir -p "$ROOT"

lift() {  # <name> <owner/repo> <tag> <subpath>
  local name="$1" repo="$2" tag="$3" sub="${4:-}"
  local base="https://raw.githubusercontent.com/$repo/$tag/$sub"
  mkdir -p "$ROOT/$name"
  curl -fsSL "${base}go.mod" -o "$ROOT/$name/go.mod"
  curl -fsSL "${base}go.sum" -o "$ROOT/$name/go.sum" || echo "  (no go.sum for $name)"
  echo "lifted $name <- $repo@$tag ${sub:+($sub)}"
}

# 1 anchor (rich, >=1.17)   2 tiny sanity          6 pre-1.17 (old tag)
lift viper       spf13/viper        v1.18.2
lift cobra       spf13/cobra        v1.8.0
lift cobra_old   spf13/cobra        v1.1.1        # go 1.15 -> resolve-required
# 7 zero-dep                8 large clean closure
lift uuid        google/uuid        v1.6.0
lift prometheus  prometheus/prometheus v2.51.0
echo "NOTE: verify each go.mod's 'go' directive after lift (Step 2)."
echo "NOTE: vendored(#4), registry-replace(#5), go.work(#3), cgo(#9) added manually below."
```

Run:
```bash
bash src/eval/language_package_eval/go/lift_corpus.sh
```
Expected: `lifted viper …`, `lifted cobra …`, etc., under `outputs/graph_fidelity/_smoke_go/`.

- [ ] **Step 2: Verify the lifted `go` directives match the intended axis**

Run:
```bash
for d in outputs/graph_fidelity/_smoke_go/*/; do
  printf "%-14s go=%s\n" "$(basename "$d")" "$(grep -m1 '^go ' "$d/go.mod" | awk '{print $2}')"
done
```
Expected: `viper`, `cobra`, `uuid`, `prometheus` show `go >= 1.17`; `cobra_old` shows `go 1.15` (or ≤1.16). If `cobra_old`'s tag isn't pre-1.17, pick an older tag (e.g. `v1.0.0`) and re-lift.

- [ ] **Step 3: Add the manually-built corpus entries (go.work, registry-replace, vendored)**

`go.work` workspace (`ws_demo`) — two tiny local members:
```bash
ROOT=outputs/graph_fidelity/_smoke_go
mkdir -p "$ROOT/ws_demo/a" "$ROOT/ws_demo/b"
cat > "$ROOT/ws_demo/go.work" <<'EOF'
go 1.21

use (
    ./a
    ./b
)
EOF
cat > "$ROOT/ws_demo/a/go.mod" <<'EOF'
module example.com/ws/a

go 1.21

require github.com/google/uuid v1.6.0
EOF
cat > "$ROOT/ws_demo/b/go.mod" <<'EOF'
module example.com/ws/b

go 1.21

require github.com/spf13/pflag v1.0.5
EOF
```

Registry-replace (`reg_replace`) — a ≥1.17 module that replaces a dep with a fork by version (resolves offline AND under the oracle):
```bash
cat > "$ROOT/reg_replace/go.mod" <<'EOF'
module example.com/regreplace

go 1.21

require github.com/google/uuid v1.5.0

replace github.com/google/uuid => github.com/google/uuid v1.6.0
EOF
# go.sum must cover the replacement target; generate it once in-container:
docker run --rm -v "$PWD/$ROOT/reg_replace:/src" -w /src golang:1.22 \
  sh -c "go mod download github.com/google/uuid@v1.6.0 && go mod tidy" || \
  echo "  (if offline, hand-add the go.sum lines for google/uuid v1.6.0)"
```

Vendored (`vendored_demo`) — self-vendor cobra so the oracle can run `-mod=vendor` (chosen without replaces per spec §7):
```bash
mkdir -p "$ROOT/vendored_demo"
cat > "$ROOT/vendored_demo/go.mod" <<'EOF'
module example.com/vendored

go 1.21

require github.com/spf13/pflag v1.0.5
EOF
docker run --rm -v "$PWD/$ROOT/vendored_demo:/src" -w /src golang:1.22 \
  sh -c "go mod tidy && go mod vendor"
```
Expected: `$ROOT/vendored_demo/vendor/modules.txt` now exists.

- [ ] **Step 4: Run OURS across the whole corpus**

Run:
```bash
python3 -m src.eval.language_package_eval.go.run_ours_go \
  viper,cobra,cobra_old,uuid,prometheus,ws_demo,reg_replace,vendored_demo \
  outputs/graph_fidelity/_smoke_go_ours
```
Expected: one `OK …` line per repo. `cobra_old` reports `closure_source=resolve-required`; `ws_demo` reports `workspace`; `vendored_demo` reports `vendor`; the rest `gomod-pruned`. No `ERR`.

- [ ] **Step 5: Run the Docker oracle across the corpus (excluding the resolve-required one)**

Run:
```bash
GO_ORACLE_DOCKER=1 python3 -m src.eval.language_package_eval.go.oracle \
  viper,cobra,uuid,prometheus,ws_demo,reg_replace,vendored_demo \
  outputs/graph_fidelity/_smoke_go_oracle
```
Expected: one `OK … N modules` line per repo (network populates the module cache on first run). `vendored_demo` runs with `-mod=vendor` automatically.

- [ ] **Step 6: Score and write the result**

Run this one-off scorer:
```bash
python3 - <<'PY'
import json, pathlib
from src.eval.language_package_eval.go.compare_go import score_repo
ours_d = pathlib.Path("outputs/graph_fidelity/_smoke_go_ours")
orac_d = pathlib.Path("outputs/graph_fidelity/_smoke_go_oracle")
rows = []
for of in sorted(ours_d.glob("*.json")):
    name = of.stem
    ours = json.loads(of.read_text())
    orac_f = orac_d / f"{name}.json"
    if not orac_f.is_file():
        rows.append((name, ours.get("closure_source"), "—", "—", "no-oracle"))
        continue
    s = score_repo(ours, json.loads(orac_f.read_text()))
    rows.append((name, ours.get("closure_source"), s["recall"], s["precision"],
                 f"missing={len(s['missing'])} extra={len(s['extra'])}"))
for r in rows:
    print(f"{r[0]:<16} {r[1]:<16} recall={r[2]} prec={r[3]}  {r[4]}")
PY
```
Expected: `viper`/`cobra`/`uuid`/`prometheus`/`ws_demo`/`reg_replace`/`vendored_demo` all show `recall=1.0 prec=1.0` (Δ=0 — the premise holds). Any non-1.0 is a real finding to record, not to paper over.

- [ ] **Step 7: Write the result doc**

`docs/superpowers/loops/2026-07-05-go-eval-slice1-result.md`: record the per-repo table from Step 6, the headline (expected: pruned/vendor/workspace all Δ=0; `cobra_old`→resolve-required flagged), and any divergence with its bucket. State whether the offline `go.mod`≥1.17 parse is validated as a sound proxy for `go list -m all` (the slice's deliverable question, spec §1).

- [ ] **Step 8: Commit**

```bash
git add src/eval/language_package_eval/go/lift_corpus.sh \
        outputs/graph_fidelity/_smoke_go \
        docs/superpowers/loops/2026-07-05-go-eval-slice1-result.md
git commit -m "test(eval-go): corpus (manifest-only, 8 entries) + end-to-end Delta=0 validation vs go list -m all"
```

> **If `outputs/` is gitignored** (as prior eval corpora were, per project memory), commit only `lift_corpus.sh` + the result doc, and note in the result doc that the corpus is reproducible via `lift_corpus.sh` + the Step-3 heredocs.

---

## Self-Review

**1. Spec coverage:**
- §3 parser (parse_go_mod/vendor/go.sum/go.work) → Tasks 1-2. ✓
- §3.1 authority ladder (workspace/vendor/pruned/resolve-required, replace/exclude, main-module drop) → Task 3. ✓
- §4 run_ours_go JSON shape → Task 4. ✓ (spec's `replaced` display key is realized as `replace_local` = module keys, which the comparer needs; noted in Task 3/4 interfaces.)
- §5 oracle (`go list -m all`, `-mod=vendor`, workspace, manifest-only, deterministic no-agent) → Task 6. ✓
- §6 compare (recall/precision/version-agreement, missing/extra/replace_local both-sides removal, resolve_required relabel) → Task 5. ✓
- §7 corpus (manifest-only, 8-9 edge-case entries incl. go.work; local-replace/exclude → fixtures) → Task 7 (corpus) + Task 3 tests (local-replace/exclude/missing-member fixtures). ✓
- §8 out-of-scope (certify/cgo/GOOS/provider) → not implemented, by design. ✓
- §9 testing (unit branches, Docker-gated integration, compare unit) → Tasks 1-6 tests. ✓ (toolchain-directive + patch-version `go 1.21.0` gate: covered by `test_parse_go_mod_full` toolchain assert; add a `go 1.21.0` case if desired — the `_go_version_tuple` already handles it.)

**2. Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N". All code steps show complete code. ✓

**3. Type consistency:** `Closure.replace_local` (Task 3) == `ours["replace_local"]` (Task 4) == `ours.get("replace_local")` (Task 5). `oracle["installed"]` produced by Task 6 `main()`, consumed by Task 5 `score_repo`. `module_closure`/`parse_go_mod`/`parse_go_work` names consistent across Tasks 1-4. `parse_go_list_m` + `oracle_closure` consistent Task 6. ✓

**One gap fixed inline:** added an explicit `go 1.21.0` patch-version note under §9 coverage; `_go_version_tuple` already parses it, and `test_closure_pruned_*` exercises the ≥1.17 path — an extra one-line parametrization can be added during Task 1 if the reviewer wants it explicit.
