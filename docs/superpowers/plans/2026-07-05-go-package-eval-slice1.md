# Go Module Package-Analysis Eval (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an eval-first, offline Go module-closure parser and a deterministic dual Docker oracle that **measures the recall gap** between an offline `go.mod`(≥1.17)/`vendor`/`go.work` parse and the toolchain's `go list -m all` build list (and attributes it via a package-loading oracle) — the offline parse is a *subset* of the build list by Go pruning, not equal to it (spec §0.1).

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


def test_closure_registry_replace_respects_old_version(tmp_path):
    # `replace X vOld => Y vN` applies ONLY when the selected version == vOld.
    # Here selected is v1.0.0 but the replace names v0.9.0 -> it must be a no-op.
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "replace github.com/x/y v0.9.0 => github.com/fork/y v1.2.0\n",
    )
    c = module_closure(d)
    assert c.packages == {"github.com/x/y": "v1.0.0"}  # unchanged


def test_closure_local_replace_dropped_and_recorded(tmp_path):
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "replace github.com/x/y => ../local\n",
    )
    c = module_closure(d)
    assert c.packages == {}
    assert c.replace_local == ("github.com/x/y",)


def test_closure_exclude_matching_version_taints(tmp_path):
    # `exclude` FORBIDS a version; MVS re-selects. We cannot compute the next
    # version offline, so an exclude of the selected version taints to
    # resolve-required (spec §3.1) — it does NOT silently drop the module.
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "exclude github.com/x/y v1.0.0\n",
    )
    c = module_closure(d)
    assert c.source == "resolve-required"
    assert c.resolve_required is True


def test_closure_exclude_nonmatching_version_is_noop(tmp_path):
    # excluding a version other than the selected one changes nothing.
    d = _repo(
        tmp_path,
        "module m\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
        "exclude github.com/x/y v0.9.0\n",
    )
    c = module_closure(d)
    assert c.packages == {"github.com/x/y": "v1.0.0"}
    assert c.resolve_required is False


def test_closure_vendor_wins_over_gomod(tmp_path):
    d = _repo(tmp_path, "module m\n\ngo 1.13\n\nrequire github.com/x/y v1.0.0\n")
    (d / "vendor").mkdir()
    (d / "vendor" / "modules.txt").write_text("# github.com/x/y v1.0.0\n## explicit\n")
    c = module_closure(d)
    assert c.source == "vendor"
    assert c.packages == {"github.com/x/y": "v1.0.0"}


def test_closure_workspace_merges_members(tmp_path):
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


def test_closure_workspace_max_version_wins(tmp_path):
    # Two members require the SAME module at different versions. Go runs one
    # global MVS across the workspace -> the MAX version wins, NOT last-write.
    ws = tmp_path / "wsmax"
    ws.mkdir()
    (ws / "go.work").write_text("go 1.21\n\nuse (\n    ./a\n    ./b\n)\n")
    a = ws / "a"
    a.mkdir()
    (a / "go.mod").write_text(
        "module example.com/a\n\ngo 1.21\n\nrequire github.com/x/y v1.2.0\n"
    )
    b = ws / "b"
    b.mkdir()
    (b / "go.mod").write_text(
        "module example.com/b\n\ngo 1.21\n\nrequire github.com/x/y v1.10.0\n"
    )
    c = module_closure(ws)
    assert c.packages == {"github.com/x/y": "v1.10.0"}  # 1.10 > 1.2, not string-last


def test_closure_workspace_missing_member_taints(tmp_path):
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / "go.work").write_text("go 1.21\n\nuse ./missing\n")
    c = module_closure(ws)
    assert c.source == "resolve-required"
    assert c.resolve_required is True


def test_closure_workspace_level_replace_taints(tmp_path):
    # go.work `replace` (and go.work.sum) are not modeled in slice 1 -> taint.
    ws = tmp_path / "ws3"
    ws.mkdir()
    (ws / "go.work").write_text(
        "go 1.21\n\nuse ./a\n\nreplace github.com/x/y => ../fork\n"
    )
    a = ws / "a"
    a.mkdir()
    (a / "go.mod").write_text(
        "module example.com/a\n\ngo 1.21\n\nrequire github.com/x/y v1.0.0\n"
    )
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


def _semver_key(v: str) -> tuple:
    """Sort key for a module version. Numeric core first (`v1.10.0` > `v1.2.0`),
    then the raw string as a deterministic tie-break for pseudo/pre-release forms."""
    core = v.lstrip("v").split("-")[0].split("+")[0]
    nums: list[int] = []
    for p in core.split("."):
        if p.isdigit():
            nums.append(int(p))
        else:
            break
    return (tuple(nums), v)


def _max_version(a: str, b: str) -> str:
    return a if _semver_key(a) >= _semver_key(b) else b


def _finalize(gm: "GoMod", pkgs: dict[str, str], source: str) -> Closure:
    """Apply replace/exclude, drop the main module, count direct/indirect."""
    replace_local: list[str] = []
    for r in gm.replaces:
        # Honor an old-version constraint: `replace X vOld => ...` applies ONLY when
        # the selected version of X is vOld. `replace X => ...` (no old version) always applies.
        if r.old_version is not None and pkgs.get(r.old_path) != r.old_version:
            continue
        if r.new_version is None:  # local filesystem replace -> drop (target deps invisible offline)
            if pkgs.pop(r.old_path, None) is not None:
                replace_local.append(r.old_path)
        elif r.old_path in pkgs:   # registry replace -> rewrite version, keep old key (matches `go list`)
            pkgs[r.old_path] = r.new_version
    for e in gm.excludes:
        # `exclude` FORBIDS a version; MVS then selects the next. We cannot compute
        # that offline, so an exclude of the SELECTED version taints to resolve-required.
        # An exclude of any other version is a no-op.
        if pkgs.get(e.path) == e.version:
            return _resolve_required(gm)
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


def _work_has_replace(work_path: Path) -> bool:
    """True if go.work carries a `replace` directive (single-line or block open)."""
    for raw in work_path.read_text().splitlines():
        s = _strip_comment(raw)[0].strip()
        if s.startswith("replace"):
            return True
    return False


def _workspace_closure(repo: Path, members: tuple[str, ...]) -> Closure:
    """One global MVS across all members: the MAX version of each module wins
    (not last-write). Workspace-level `replace`/`go.work.sum` are unmodelled in
    slice 1 -> taint to resolve-required (spec §3.1)."""
    if _work_has_replace(repo / "go.work"):
        return _resolve_required(GoMod("", ""))
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
        for mod, ver in sub.packages.items():
            merged[mod] = _max_version(merged[mod], ver) if mod in merged else ver
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
Expected: PASS (18 passed).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go tests/eval/language_package_eval/go
ruff check --fix src/eval/language_package_eval/go
git add src/eval/language_package_eval/go/gomod.py tests/eval/language_package_eval/go/test_gomod.py
git commit -m "feat(eval-go): module_closure authority ladder — replace vOld-constraint, exclude forbids-version (taint), go.work global-MVS max-version"
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
- Produces: `score_repo(ours: dict, oracle: dict, oracle_loadset: dict | None = None) -> dict`. `ours` has `packages`, `replace_local`, `resolve_required`; `oracle` (build-list) and `oracle_loadset` (package-loading, optional) each have `installed`. Returns `recall_buildlist, recall_loadset, precision, vexact, missing, pruned_superset, recall_defect, extra, replace_local, resolve_required`. Metrics are `None` when `resolve_required`; `recall_loadset`/`pruned_superset`/`recall_defect` are `None` when no `oracle_loadset` is given.

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
    assert s["recall_buildlist"] == 1.0 and s["precision"] == 1.0
    assert s["missing"] == [] and s["extra"] == []
    assert s["vexact"] == 2


def test_missing_and_extra_buckets():
    s = score_repo(
        _ours({"github.com/x/y": "v1.0.0", "github.com/only/ours": "v9"}),
        _oracle({"github.com/x/y": "v1.0.0", "github.com/only/oracle": "v3"}),
    )
    assert s["recall_buildlist"] == 0.5 and s["precision"] == 0.5
    assert s["missing"] == ["github.com/only/oracle"]
    assert s["extra"] == ["github.com/only/ours"]


def test_local_replace_removed_from_both_sides():
    # locally-replaced module is emitted by `go list -m all` but dropped by OURS;
    # it must be excluded from both denominators (spec §6).
    s = score_repo(
        _ours({"github.com/x/y": "v1.0.0"}, replace_local=["github.com/local/m"]),
        _oracle({"github.com/x/y": "v1.0.0", "github.com/local/m": "v0.0.0"}),
    )
    assert s["recall_buildlist"] == 1.0 and s["precision"] == 1.0
    assert s["replace_local"] == ["github.com/local/m"]


def test_resolve_required_relabels_whole_oracle():
    s = score_repo(
        _ours({}, resolve_required=True),
        _oracle({"github.com/x/y": "v1.0.0"}),
    )
    assert s["recall_buildlist"] is None and s["precision"] is None
    assert s["resolve_required"] is True
    assert s["missing"] == []  # NOT counted as recall misses
    assert s["resolve_required_missing"] == ["github.com/x/y"]


def test_empty_both_sides_no_zero_division():
    s = score_repo(_ours({}), _oracle({}))
    assert s["recall_buildlist"] == 1.0 and s["precision"] == 1.0
    assert s["recall_loadset"] is None  # no load-set oracle supplied


def test_loadset_splits_pruned_superset_from_recall_defect():
    # OURS misses TWO build-list modules. One (`needed`) provides a package the
    # main module loads -> a real recall DEFECT. The other (`sibling`) is a
    # dep-of-dep the main module never imports -> expected PRUNED SUPERSET.
    ours = _ours({"github.com/x/y": "v1.0.0"})
    build = _oracle({
        "github.com/x/y": "v1.0.0",
        "github.com/needed": "v1.0.0",
        "github.com/sibling": "v1.0.0",
    })
    load = _oracle({"github.com/x/y": "v1.0.0", "github.com/needed": "v1.0.0"})
    s = score_repo(ours, build, oracle_loadset=load)
    assert s["recall_buildlist"] == 1 / 3          # only x/y of 3 matched
    assert s["recall_loadset"] == 1 / 2            # x/y matched, needed missed
    assert s["recall_defect"] == ["github.com/needed"]
    assert s["pruned_superset"] == ["github.com/sibling"]


def test_no_loadset_leaves_split_none():
    s = score_repo(
        _ours({"github.com/x/y": "v1.0.0"}),
        _oracle({"github.com/x/y": "v1.0.0", "github.com/z/w": "v1.0.0"}),
    )
    assert s["recall_loadset"] is None
    assert s["pruned_superset"] is None and s["recall_defect"] is None
    assert s["missing"] == ["github.com/z/w"]      # undifferentiated without load-set
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_compare_go.py -q`
Expected: FAIL with `ModuleNotFoundError: ...compare_go`.

- [ ] **Step 3: Implement**

`src/eval/language_package_eval/go/compare_go.py`:
```python
"""Recall/precision + divergence buckets for the Go package-layer eval. OURS
(offline require-block closure) is a SUBSET of the build-list oracle by Go pruning
semantics (spec §0.1), so recall is the story. A second, optional package-loading
oracle splits build-list misses into expected `pruned_superset` vs real
`recall_defect`. Mirrors ``compare_node.py``."""
from __future__ import annotations


def score_repo(ours: dict, oracle: dict, oracle_loadset: dict | None = None) -> dict:
    replace_local = set(ours.get("replace_local", []))
    o_pkgs = {k: v for k, v in ours["packages"].items() if k not in replace_local}
    g_pkgs = {k: v for k, v in oracle["installed"].items() if k not in replace_local}
    ours_keys, build_keys = set(o_pkgs), set(g_pkgs)
    load_keys = (
        {k for k in oracle_loadset["installed"] if k not in replace_local}
        if oracle_loadset is not None
        else None
    )

    if ours.get("resolve_required"):
        # Whole oracle is a KNOWN offline limitation, not a recall defect (spec §6).
        return {
            "recall_buildlist": None,
            "recall_loadset": None,
            "precision": None,
            "resolve_required": True,
            "missing": [],
            "pruned_superset": None,
            "recall_defect": None,
            "extra": [],
            "replace_local": sorted(replace_local),
            "resolve_required_missing": sorted(build_keys),
            "vexact": 0,
        }

    inter = ours_keys & build_keys
    missing = build_keys - ours_keys
    result = {
        "recall_buildlist": len(inter) / len(build_keys) if build_keys else 1.0,
        "recall_loadset": None,
        "precision": len(inter) / len(ours_keys) if ours_keys else 1.0,
        "resolve_required": False,
        "missing": sorted(missing),
        "pruned_superset": None,
        "recall_defect": None,
        "extra": sorted(ours_keys - build_keys),
        "replace_local": sorted(replace_local),
        "vexact": sum(1 for k in inter if o_pkgs[k] == g_pkgs[k]),
    }
    if load_keys is not None:
        load_inter = ours_keys & load_keys
        result["recall_loadset"] = len(load_inter) / len(load_keys) if load_keys else 1.0
        # A build-list miss that IS in the load-set = a real recall defect (our parser
        # should have found it). One NOT in the load-set = expected pruned superset.
        result["recall_defect"] = sorted(m for m in missing if m in load_keys)
        result["pruned_superset"] = sorted(m for m in missing if m not in load_keys)
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/eval/language_package_eval/go/test_compare_go.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go/compare_go.py tests/eval/language_package_eval/go/test_compare_go.py
ruff check --fix src/eval/language_package_eval/go/compare_go.py
git add src/eval/language_package_eval/go/compare_go.py tests/eval/language_package_eval/go/test_compare_go.py
git commit -m "feat(eval-go): compare_go dual-oracle — recall_buildlist/recall_loadset/precision + pruned_superset vs recall_defect split"
```

---

### Task 6: `oracle.py` — Docker `go list -m all` (env-gated)

**Files:**
- Create: `src/eval/language_package_eval/go/oracle.py`
- Test: `tests/eval/language_package_eval/go/test_oracle_go.py`

**Interfaces:**
- Produces: `parse_go_list_json(output: str) -> dict[str, str]` (pure — parses a `go list -m -json all` object stream), `oracle_closure(repo_dir, *, go_image="golang:1.22", vendored=False) -> dict[str, str]` (build-list, manifest-only, Docker), and `oracle_loadset(repo_dir, *, go_image="golang:1.22") -> dict[str, str]` (package-loading set via `go list -deps -json ./...`; needs repo SOURCE). Also a `main()` writing `{installed, repo}` per repo.

- [ ] **Step 1: Write the failing test (pure parser + gated integration)**

`tests/eval/language_package_eval/go/test_oracle_go.py`:
```python
from __future__ import annotations

import os
import shutil

import pytest

from src.eval.language_package_eval.go.oracle import oracle_closure, parse_go_list_json


def test_parse_go_list_json_skips_main_and_handles_replace():
    # `go list -m -json all` emits a STREAM of JSON objects (not an array). `Main:true`
    # is the workspace's main module(s) -> skipped; a `Replace` keys the ORIGINAL Path
    # with the replacement Version (build-list identity). Robust vs pseudo-versions.
    out = (
        '{"Path":"github.com/acme/app","Main":true}\n'
        '{"Path":"github.com/spf13/cobra","Version":"v1.8.0"}\n'
        '{"Path":"github.com/old/mod","Version":"v1.0.0",'
        '"Replace":{"Path":"github.com/new/mod","Version":"v1.2.0"}}\n'
    )
    assert parse_go_list_json(out) == {
        "github.com/spf13/cobra": "v1.8.0",
        "github.com/old/mod": "v1.2.0",
    }


def test_parse_go_list_json_ignores_versionless_main_in_workspace():
    # a second Main:true (workspace member) with no Version must not appear.
    out = (
        '{"Path":"example.com/a","Main":true}\n'
        '{"Path":"example.com/b","Main":true}\n'
        '{"Path":"github.com/x/y","Version":"v1.0.0"}\n'
    )
    assert parse_go_list_json(out) == {"github.com/x/y": "v1.0.0"}


@pytest.mark.skipif(
    os.environ.get("GO_ORACLE_DOCKER") != "1" or shutil.which("docker") is None,
    reason="integration oracle: set GO_ORACLE_DOCKER=1 and have Docker + network",
)
def test_oracle_buildlist_anchor_is_superset_of_ours():
    # Corrected expectation (spec §0.1): OURS (require-block) is a SUBSET of the
    # build list, NOT equal to it. So assert NO extras and recall in (0, 1] — never
    # assert Delta=0. Requires the anchor manifest lifted by Task 7.
    from src.eval.language_package_eval.go.compare_go import score_repo
    from src.eval.language_package_eval.go.run_ours_go import SMOKE, ours_for_repo

    anchor = SMOKE / "viper"
    if not (anchor / "go.mod").is_file():
        pytest.skip("anchor corpus not lifted (run Task 7)")
    ours = ours_for_repo(anchor)
    oracle = {"installed": oracle_closure(anchor)}
    s = score_repo(ours, oracle)
    assert s["extra"] == []                        # OURS ⊆ build list (pruning)
    assert 0.0 < s["recall_buildlist"] <= 1.0      # measured gap, not asserted == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/eval/language_package_eval/go/test_oracle_go.py::test_parse_go_list_json_skips_main_and_handles_replace -q`
Expected: FAIL with `ModuleNotFoundError: ...oracle`.

- [ ] **Step 3: Implement**

`src/eval/language_package_eval/go/oracle.py`:
```python
#!/usr/bin/env python3
"""Gold closures for the Go package-layer eval, inside a golang container.
Deterministic — NO agent (the toolchain emits the build list directly). Docker-gated
(spec §5). Two oracles:
  * oracle_closure  — BUILD LIST via `go list -mod=mod -m -json all` (manifest-only).
  * oracle_loadset  — PACKAGE-LOADING set via `go list -deps -json ./...` (needs SOURCE).

Usage:
    GO_ORACLE_DOCKER=1 python3 -m src.eval.language_package_eval.go.oracle <repo,...> <out_dir>
Emits per-repo ``<repo>.json`` = {"installed": {module: version}, "repo": name} (build list).
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


def parse_go_list_json(output: str) -> dict[str, str]:
    """Parse the ``go list -m -json all`` object STREAM -> {module: version}.
    Skips ``Main: true`` objects (main / workspace-member modules, which have no
    Version); a ``Replace`` keys the ORIGINAL Path with the replacement Version."""
    decoder = json.JSONDecoder()
    result: dict[str, str] = {}
    idx, n = 0, len(output)
    while idx < n:
        while idx < n and output[idx].isspace():
            idx += 1
        if idx >= n:
            break
        obj, idx = decoder.raw_decode(output, idx)
        if obj.get("Main"):
            continue
        path, ver = obj.get("Path"), obj.get("Version")
        repl = obj.get("Replace")
        if repl:
            ver = repl.get("Version", ver)
        if path and ver:
            result[path] = ver
    return result


def _docker_go(repo: pathlib.Path, go_image: str, *args: str) -> str:
    cmd = ["docker", "run", "--rm", "-v", f"{repo}:/src", "-w", "/src",
           go_image, "go", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def oracle_closure(repo_dir, *, go_image: str = GO_IMAGE, vendored: bool = False) -> dict[str, str]:
    """BUILD LIST. Force ``-mod=vendor`` when vendored else ``-mod=mod`` so a stray
    vendor/ dir or stale go.mod can't silently change the result (spec §5)."""
    repo = pathlib.Path(repo_dir).resolve()
    mode = "-mod=vendor" if vendored else "-mod=mod"
    return parse_go_list_json(_docker_go(repo, go_image, "list", mode, "-m", "-json", "all"))


def oracle_loadset(repo_dir, *, go_image: str = GO_IMAGE) -> dict[str, str]:
    """PACKAGE-LOADING set: modules that provide packages the main module's packages
    import, via ``go list -deps -json ./...``. Needs the repo SOURCE (NOT manifest-
    only) — run on a full clone of the anchor (spec §2). Std-lib packages have no
    ``Module`` and are skipped."""
    repo = pathlib.Path(repo_dir).resolve()
    out = _docker_go(repo, go_image, "list", "-deps", "-json", "./...")
    decoder = json.JSONDecoder()
    result: dict[str, str] = {}
    idx, n = 0, len(out)
    while idx < n:
        while idx < n and out[idx].isspace():
            idx += 1
        if idx >= n:
            break
        obj, idx = decoder.raw_decode(out, idx)
        mod = obj.get("Module")
        if mod and not mod.get("Main"):
            path, ver = mod.get("Path"), mod.get("Version")
            if path and ver:
                result[path] = ver
    return result


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
            rec["error"] = (exc.stderr or "").strip()[:500]
            print(f"ERR {name}: {rec['error']}")
        (out / f"{name}.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
    print(f"DONE oracle(go) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass (pure parser tests only; integration auto-skips)**

Run: `pytest tests/eval/language_package_eval/go/test_oracle_go.py -q`
Expected: PASS (2 passed, 1 skipped).

- [ ] **Step 5: Commit**

```bash
black src/eval/language_package_eval/go/oracle.py tests/eval/language_package_eval/go/test_oracle_go.py
ruff check --fix src/eval/language_package_eval/go/oracle.py
git add src/eval/language_package_eval/go/oracle.py tests/eval/language_package_eval/go/test_oracle_go.py
git commit -m "feat(eval-go): dual Docker oracle — go list -m -json all (build list) + go list -deps (load-set) + pure parse_go_list_json"
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

# PROXY-VERIFIED go directives (2026-07-05) — labels reflect REALITY, not intent:
lift viper       spf13/viper           v1.18.2   # anchor: go 1.18 (>=1.17) VERIFIED
lift prometheus  prometheus/prometheus v2.51.0   # large: verify go>=1.17 AND no local replace (Step 2)
lift cobra       spf13/cobra           v1.8.0    # go 1.15 -> RESOLVE-REQUIRED axis (verified)
lift uuid        google/uuid           v1.6.0    # NO go directive -> RESOLVE-REQUIRED axis (verified)
echo "NOTE: verify each go.mod's 'go' directive after lift (Step 2) — the draft mis-picked cobra/uuid."
echo "NOTE: go.work(#3), registry-replace(#5), vendored(#4) constructed in Step 3; a"
echo "      verified >=1.17 tiny/zero-dep entry is OPTIONAL (unit tests already cover empty closure)."
```

Run:
```bash
bash src/eval/language_package_eval/go/lift_corpus.sh
```
Expected: `lifted viper …`, `lifted prometheus …`, `lifted cobra …`, `lifted uuid …` under `outputs/graph_fidelity/_smoke_go/`.

- [ ] **Step 2: Verify the lifted `go` directives match the intended axis**

Run:
```bash
for d in outputs/graph_fidelity/_smoke_go/*/; do
  n=$(basename "$d"); gv=$(grep -m1 '^go ' "$d/go.mod" | awk '{print $2}')
  lr=$(grep -Ec '=>\s*\.\.?/' "$d/go.mod" || true)
  printf "%-14s go=%-8s local_replaces=%s\n" "$n" "${gv:-<none>}" "$lr"
done
```
Expected (the whole point of the re-verify): `viper go=1.18`, `prometheus go>=1.17` **and** `local_replaces=0` (if prometheus has any `=> ../`, drop it — the oracle can't resolve local paths manifest-only — and pick another large ≥1.17 module without local replaces). `cobra go=1.15` and `uuid go=<none>` → **both correctly land on the resolve-required axis** (this is the design's #6, not a failure).

- [ ] **Step 3: Add the manually-built corpus entries (go.work, registry-replace, vendored)**

`go.work` workspace (`ws_demo`) — two members that require the **same** module at **different** versions, so the run exercises the global-MVS max-version rule (§3.1), plus a disjoint dep each:
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

require github.com/google/uuid v1.4.0
EOF
cat > "$ROOT/ws_demo/b/go.mod" <<'EOF'
module example.com/ws/b

go 1.21

require github.com/google/uuid v1.6.0
EOF
# Populate go.work.sum + each go.sum so the workspace build-list oracle can run offline-ish.
# (Manifest-only workspaces need the sum DBs; `go work sync` fetches the module graph.)
docker run --rm -v "$PWD/$ROOT/ws_demo:/src" -w /src golang:1.22 \
  sh -c "go work sync" || echo "  (needs network once to build go.work.sum)"
```
Expected OURS: `{github.com/google/uuid: v1.6.0}` — the **max** of v1.4.0/v1.6.0, not last-write.

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
  viper,prometheus,cobra,uuid,ws_demo,reg_replace,vendored_demo \
  outputs/graph_fidelity/_smoke_go_ours
```
Expected: one `OK …` line per repo. **`cobra` and `uuid` report `closure_source=resolve-required`** (go 1.15 / no directive); `ws_demo` → `workspace`; `vendored_demo` → `vendor`; `viper`/`prometheus`/`reg_replace` → `gomod-pruned`. No `ERR`.

- [ ] **Step 5: Run the build-list oracle (skip the two resolve-required entries)**

Run:
```bash
GO_ORACLE_DOCKER=1 python3 -m src.eval.language_package_eval.go.oracle \
  viper,prometheus,ws_demo,reg_replace,vendored_demo \
  outputs/graph_fidelity/_smoke_go_oracle
```
Expected: one `OK … N modules` line per repo (network populates the module cache on first run). `vendored_demo` runs `-mod=vendor` automatically. `cobra`/`uuid` are omitted — OURS is empty (resolve-required), so there is nothing to compare.

- [ ] **Step 5b: Full-clone the anchor and run the package-loading oracle**

The load-set oracle needs SOURCE (spec §2), so clone the anchor once:
```bash
git clone --depth 1 --branch v1.18.2 https://github.com/spf13/viper \
  outputs/graph_fidelity/_smoke_go_src/viper
GO_ORACLE_DOCKER=1 python3 - <<'PY'
import json, pathlib
from src.eval.language_package_eval.go.oracle import oracle_loadset
src = pathlib.Path("outputs/graph_fidelity/_smoke_go_src/viper")
rec = {"repo": "viper", "installed": oracle_loadset(src)}
out = pathlib.Path("outputs/graph_fidelity/_smoke_go_loadset"); out.mkdir(parents=True, exist_ok=True)
(out / "viper.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
print(f"load-set(viper): {len(rec['installed'])} modules")
PY
```

- [ ] **Step 6: Score and write the result**

Run this one-off scorer (recall vs the build list; load-set attribution for the anchor):
```bash
python3 - <<'PY'
import json, pathlib
from src.eval.language_package_eval.go.compare_go import score_repo
ours_d = pathlib.Path("outputs/graph_fidelity/_smoke_go_ours")
orac_d = pathlib.Path("outputs/graph_fidelity/_smoke_go_oracle")
load_d = pathlib.Path("outputs/graph_fidelity/_smoke_go_loadset")
rows = []
for of in sorted(ours_d.glob("*.json")):
    name = of.stem
    ours = json.loads(of.read_text())
    if ours.get("resolve_required"):
        rows.append((name, ours.get("closure_source"), "flag", "flag", "resolve-required (expected)"))
        continue
    orac_f = orac_d / f"{name}.json"
    if not orac_f.is_file():
        rows.append((name, ours.get("closure_source"), "—", "—", "no-oracle"))
        continue
    load_f = load_d / f"{name}.json"
    loadset = json.loads(load_f.read_text()) if load_f.is_file() else None
    s = score_repo(ours, json.loads(orac_f.read_text()), oracle_loadset=loadset)
    note = f"missing={len(s['missing'])} extra={len(s['extra'])}"
    if s["recall_loadset"] is not None:
        note += (f" | loadset_recall={s['recall_loadset']:.3f}"
                 f" defect={len(s['recall_defect'])} pruned_superset={len(s['pruned_superset'])}")
    rows.append((name, ours.get("closure_source"),
                 f"{s['recall_buildlist']:.3f}", f"{s['precision']:.3f}", note))
for r in rows:
    print(f"{r[0]:<16} {r[1]:<16} recall_bl={r[2]} prec={r[3]}  {r[4]}")
PY
```
Expected (the *measurement*, NOT a Δ=0 pass/fail — spec §0.1):
- `viper`: `recall_bl < 1.0` (the pruned-superset gap), `prec ≈ 1.0` (no extras), and — the real fidelity check — **`loadset_recall == 1.000` with `defect=0`** (every miss is `pruned_superset`, i.e. Go structure, not a parser bug).
- `ws_demo` OURS `{google/uuid: v1.6.0}` (max wins); `vendored_demo` matches `-mod=vendor`; `reg_replace` keys `google/uuid: v1.6.0`.
- `cobra`/`uuid`: `resolve-required` (correctly flagged).
- **Any `defect > 0` on the anchor is a genuine parser bug to fix** — that, not Δ=0, is the gate.

- [ ] **Step 7: Write the result doc**

`docs/superpowers/loops/2026-07-05-go-eval-slice1-result.md`: record the per-repo table from Step 6 and answer the slice's deliverable question (spec §1): **how big is the offline-`go.mod`-vs-`go list -m all` recall gap, and is it entirely `pruned_superset` (Go structure) or is there `recall_defect` (a parser bug)?** Headline = the anchor's `recall_buildlist` (the measured gap) + `recall_loadset` (must be 1.0 / `defect=0`). Note `cobra`/`uuid` correctly flagged `resolve-required`, and the `ws_demo` max-version / `vendored` / `reg_replace` behaviors. Do **not** report a "Δ=0 pass"; report the gap.

- [ ] **Step 8: Commit**

```bash
git add src/eval/language_package_eval/go/lift_corpus.sh \
        outputs/graph_fidelity/_smoke_go \
        docs/superpowers/loops/2026-07-05-go-eval-slice1-result.md
git commit -m "test(eval-go): corpus (manifest-only) + end-to-end recall-gap measurement vs go list -m all (dual oracle)"
```

> **If `outputs/` is gitignored** (as prior eval corpora were, per project memory), commit only `lift_corpus.sh` + the result doc, and note in the result doc that the corpus is reproducible via `lift_corpus.sh` + the Step-3 heredocs.

---

## Self-Review

**Revised 2026-07-05** to match the Codex-corrected spec (§0): recall-gap framing, dual oracle, replace/exclude/workspace semantics, `-json` oracle, fixed corpus.

**1. Spec coverage:**
- §0/§0.1 corrected premise (recall gap, not Δ=0) → Task 5 dual-oracle scoring + Task 6 build-list-vs-load-set + Task 7 measurement framing. ✓
- §3 parser (parse_go_mod/vendor/go.sum/go.work) → Tasks 1-2. ✓
- §3.1 ladder + **corrected semantics** (replace honors `vOld`; exclude forbids-version→taint; go.work global-MVS max-version; ws-level replace→taint) → Task 3 impl + tests. ✓
- §4 run_ours_go JSON shape (`replace_local` key) → Task 4. ✓
- §5 oracle (`go list -m -json all`, `-mod=mod`/`-mod=vendor`, load-set needs source) → Task 6. ✓
- §6 compare (recall_buildlist/recall_loadset/precision, `pruned_superset` vs `recall_defect` split, replace_local both-sides removal, resolve_required relabel) → Task 5. ✓
- §7 corpus (manifest-only; **proxy-verified** go directives — cobra/uuid→resolve-required; anchor viper full-clone for load-set) → Task 7. ✓
- §8 out-of-scope (certify/cgo/GOOS/provider) → not implemented, by design. ✓
- §9 testing (unit branches incl. exclude-taint / replace-vOld / workspace-max; dual-oracle integration; compare unit) → Tasks 1-6 tests. ✓

**2. Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N". All code steps show complete code. ✓

**3. Type consistency:** `Closure.replace_local` (Task 3) == `ours["replace_local"]` (Task 4) == `ours.get("replace_local")` (Task 5). `oracle["installed"]` produced by Task 6, consumed by Task 5 `score_repo(ours, oracle, oracle_loadset=)`. `parse_go_list_json` + `oracle_closure` + `oracle_loadset` consistent Task 6→7. `_max_version`/`_semver_key`/`_work_has_replace` defined and used in Task 3. Expected pass-counts updated (Task 3: 18; Task 5: 7; Task 6: 2 passed + 1 skipped). ✓

**Correctness note (why this plan changed):** the first draft asserted Δ=0; a Codex review + `proxy.golang.org` checks showed (a) `go list -m all` ⊋ tidy require block → recall gap, (b) cobra@1.8.0 is go 1.15 and uuid has no go directive. Both are now baked in: the eval **measures** the gap and attributes it via the load-set oracle, and the corpus labels reflect verified reality.
