# Six-Tier Env Model — Config Tier Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the certified dependency graph schema to carry environment *tiers*, then add the first new tier — **Config (env vars)** — so the graph discovers, certifies (presence), and renders env-var needs the agent currently can't see.

**Architecture:** Additive, default-safe schema widening (new `NodeType`s + a `tier` attribute + a `Layer.CONFIG`) followed by a pure static-discovery stage `scan_config` that appends `CONFIG` nodes (with `printenv` check commands and `env:VAR=…` fixes) to the *same* `DepGraph`, wired by `requires` edges to the Project (project-induced) and Package (package-induced) nodes. The existing `certify_all` certifies them; the existing advisory render surfaces them.

**Tech Stack:** Python 3.11, `pytest`, stdlib `ast`/`os`, frozen `dataclasses` (immutability), Docker executor (integration only).

## Global Constraints

- **Immutability:** every "mutation" returns a NEW `DepGraph`/`Node`; never mutate in place (frozen dataclasses; use `with_node`/`with_edge`/`replace`). Verbatim from the spec and `schema.py`.
- **Default-safe / off-state byte-identical:** all new code runs only *inside* `build_dep_graph`, which only runs when the dep-graph feature is enabled (`--enable-dep-graph` / arm `v1gd`). No new flag. With the feature off, output must be byte-identical to today.
- **Certification invariant:** a node's `state` is flipped ONLY by running its `check_command` (host-issued); discovery never sets `state` beyond `UNKNOWN`. Verbatim from `certify.py` / design §3.1.
- **Target:** Python 3.11, manylinux_2_28 (never manylinux2014).
- **Pure discovery stages take no executor:** `scan_config` and its helpers read the repo on disk + the in-progress graph only (mirrors `roots.select_roots` / `seed_predicted_native`).
- **TDD:** write the failing test first, watch it fail, implement minimally, watch it pass, commit. One logical change per commit.

**In scope:** schema generalization (Phase 0) + static Config discovery, certification, advisory render (Phase 1).
**Out of scope (next plan):** fix-application/env-bake, rebuild-and-recertify, `PRESENT_UNVERIFIED`, failure-driven discovery, and the Services / Platform / Data tiers. (See spec §5, §7.5, §10.)

---

### Task 1: Schema scaffolding — new NodeTypes, `Layer.CONFIG`, `config_id`

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (the `NodeType` and `Layer` enums)
- Modify: `src/python_deps/depgraph/ids.py` (add `config_id`)
- Test: `tests/depgraph/test_schema.py`

**Interfaces:**
- Produces: `NodeType.PLATFORM`, `NodeType.SERVICE`, `NodeType.CONFIG`, `NodeType.DATA_ASSET` (values `"Platform"`, `"Service"`, `"Config"`, `"DataAsset"`); `Layer.CONFIG` (value `"config"`); `ids.config_id(name: str) -> str` returning `f"config:{name}"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema.py`:

```python
from python_deps.depgraph.schema import NodeType, Layer


def test_new_environment_node_types_exist():
    assert NodeType.PLATFORM.value == "Platform"
    assert NodeType.SERVICE.value == "Service"
    assert NodeType.CONFIG.value == "Config"
    assert NodeType.DATA_ASSET.value == "DataAsset"


def test_config_layer_exists():
    assert Layer.CONFIG.value == "config"


def test_config_id_format():
    assert ids.config_id("DJANGO_SETTINGS_MODULE") == "config:DJANGO_SETTINGS_MODULE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_schema.py::test_new_environment_node_types_exist tests/depgraph/test_schema.py::test_config_layer_exists tests/depgraph/test_schema.py::test_config_id_format -v`
Expected: FAIL with `AttributeError: PLATFORM` (and `CONFIG`, `config_id`).

- [ ] **Step 3: Write minimal implementation**

In `schema.py`, extend `NodeType`:

```python
class NodeType(enum.Enum):
    TEST = "Test"
    PROJECT = "Project"  # the repo under test; hub for its declared direct deps
    IMPORT = "Import"
    PACKAGE = "Package"
    SYSTEM_LIB = "SystemLib"
    TOOL = "Tool"
    RUNTIME = "Runtime"
    PLATFORM = "Platform"      # tier 1
    SERVICE = "Service"        # tier 5
    CONFIG = "Config"          # tier 6
    DATA_ASSET = "DataAsset"   # tier 6
```

In `schema.py`, extend `Layer` (add `CONFIG` after `TESTS` line):

```python
class Layer(enum.Enum):
    INTERPRETER = "interpreter"
    SYSTEM = "system"
    TOOLCHAIN = "toolchain"
    PIP = "pip"
    NAMING = "naming"
    RUNTIME = "runtime"
    TESTS = "tests"
    CONFIG = "config"
```

In `ids.py`, add:

```python
def config_id(name: str) -> str:
    return f"config:{name}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_schema.py -v`
Expected: PASS (all schema tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schema.py src/python_deps/depgraph/ids.py tests/depgraph/test_schema.py
git commit -m "feat(depgraph): add env-tier NodeTypes, Layer.CONFIG, config_id"
```

---

### Task 2: `tier` attribute on `Node` + `TYPE_TO_TIER` derivation

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (`Node` dataclass, add `TYPE_TO_TIER`, `tier_for_type`, `__post_init__`, `to_dict`)
- Test: `tests/depgraph/test_schema.py`

**Interfaces:**
- Consumes: the `NodeType` members from Task 1.
- Produces: `Node.tier: int` (auto-derived when left at the `0` sentinel); `schema.TYPE_TO_TIER: dict[NodeType, int]`; `schema.tier_for_type(t: NodeType) -> int` (returns `0` for goal types `TEST/PROJECT/IMPORT`). `Node.to_dict()` includes `"tier"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema.py` (uses the existing `make_node` helper at the top of that file):

```python
def test_tier_auto_derived_from_type():
    pkg = make_node("pkg:x", NodeType.PACKAGE, "x", Layer.PIP)
    cfg = make_node("config:X", NodeType.CONFIG, "X", Layer.CONFIG)
    syslib = make_node("syslib:libGL.so.1", NodeType.SYSTEM_LIB, "libGL.so.1", Layer.SYSTEM)
    assert pkg.tier == 4
    assert cfg.tier == 6
    assert syslib.tier == 2


def test_goal_nodes_have_tier_zero():
    test_node = make_node("test:repo_tests_pass", NodeType.TEST, "repo_tests_pass", Layer.TESTS)
    assert test_node.tier == 0


def test_explicit_tier_is_respected():
    from python_deps.depgraph.schema import Node
    n = Node(id="config:X", type=NodeType.CONFIG, name="X", layer=Layer.CONFIG,
             discovered_by=DiscoveredBy.STATIC_SCAN, tier=3)
    assert n.tier == 3


def test_to_dict_includes_tier():
    cfg = make_node("config:X", NodeType.CONFIG, "X", Layer.CONFIG)
    assert cfg.to_dict()["tier"] == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_schema.py -k "tier" -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'tier'` / `AttributeError: tier`.

- [ ] **Step 3: Write minimal implementation**

In `schema.py`, add the tier map + helper *after* the `NodeType` enum:

```python
# Provider-stack tier per node type; goal types (Test/Project/Import) map to 0
# (they are the demand side, not a tier). See design §3.1/§3.2.
TYPE_TO_TIER: dict["NodeType", int] = {
    NodeType.PLATFORM: 1,
    NodeType.SYSTEM_LIB: 2,
    NodeType.TOOL: 2,
    NodeType.RUNTIME: 3,
    NodeType.PACKAGE: 4,
    NodeType.SERVICE: 5,
    NodeType.CONFIG: 6,
    NodeType.DATA_ASSET: 6,
}


def tier_for_type(node_type: "NodeType") -> int:
    """Provider tier for ``node_type``; 0 for goal nodes (Test/Project/Import)."""
    return TYPE_TO_TIER.get(node_type, 0)
```

In the `Node` dataclass, add the field (place it right after `discovered_by`):

```python
    tier: int = 0  # 0 = derive from type in __post_init__ (goal nodes stay 0)
```

Add a `__post_init__` to `Node` (frozen dataclass → use `object.__setattr__`, same idiom as `Edge`):

```python
    def __post_init__(self) -> None:
        # Derive tier from type when left at the 0 sentinel. Idempotent for goal
        # nodes (tier_for_type returns 0 for them). frozen=True blocks rebinding,
        # so set through object.__setattr__.
        if self.tier == 0:
            object.__setattr__(self, "tier", tier_for_type(self.type))
```

In `Node.to_dict`, add `"tier": self.tier,` (e.g. right after the `"layer"` line).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_schema.py -v`
Expected: PASS (all, including the existing schema tests — `tier` is default-safe).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema.py
git commit -m "feat(depgraph): add Node.tier attribute auto-derived from type"
```

---

### Task 3: Allow `requires` edges into the new tiers (`EDGE_RULES`)

**Files:**
- Modify: `src/python_deps/depgraph/schema.py` (`EDGE_RULES`)
- Test: `tests/depgraph/test_schema.py`

**Interfaces:**
- Consumes: Task 1 node types.
- Produces: `requires` edges whose destination is `Config`/`Service`/`DataAsset`/`Platform` validate; illegal destinations still raise.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_schema.py`:

```python
def test_requires_edge_into_config_is_allowed():
    from python_deps.depgraph.schema import DepGraph, Edge, EdgeType
    proj = make_node("project:app", NodeType.PROJECT, "app", Layer.PIP)
    cfg = make_node("config:SECRET_KEY", NodeType.CONFIG, "SECRET_KEY", Layer.CONFIG)
    g = DepGraph().with_node(proj).with_node(cfg)
    g = g.with_edge(Edge(src="project:app", dst="config:SECRET_KEY",
                         relation=EdgeType.REQUIRES, origin="project"))
    assert any(e.dst == "config:SECRET_KEY" for e in g.edges)


def test_requires_edge_from_config_is_rejected():
    from python_deps.depgraph.schema import DepGraph, Edge, EdgeType
    cfg = make_node("config:X", NodeType.CONFIG, "X", Layer.CONFIG)
    pkg = make_node("pkg:y", NodeType.PACKAGE, "y", Layer.PIP)
    g = DepGraph().with_node(cfg).with_node(pkg)
    with pytest.raises(ValueError):
        g.with_edge(Edge(src="config:X", dst="pkg:y", relation=EdgeType.REQUIRES))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_schema.py -k "requires_edge" -v`
Expected: `test_requires_edge_into_config_is_allowed` FAILS with `ValueError: illegal requires destination type 'Config'`.

- [ ] **Step 3: Write minimal implementation**

In `schema.py`, extend the `requires` destination set in `EDGE_RULES`:

```python
EDGE_RULES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "requires": (
        frozenset({"Test", "Project", "Import", "Package"}),
        frozenset({"Project", "Import", "Package", "SystemLib", "Tool", "Runtime",
                   "Platform", "Service", "Config", "DataAsset"}),
    ),
    "conflicts_with": (
        frozenset({"Package"}),
        frozenset({"Package"}),
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_schema.py -k "requires_edge" -v`
Expected: PASS (both — config dst allowed, config src still rejected).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/schema.py tests/depgraph/test_schema.py
git commit -m "feat(depgraph): allow requires edges into env-tier node types"
```

---

### Task 4: Certify `Config` nodes — extend `_LAYER_ORDER`

**Files:**
- Modify: `src/python_deps/depgraph/certify.py` (`_LAYER_ORDER`)
- Test: `tests/depgraph/test_certify.py`

**Interfaces:**
- Consumes: `Layer.CONFIG` (Task 1).
- Produces: `certify_all` runs `check_command` on `Layer.CONFIG` nodes (ordered after `PIP`/`NAMING`, before `TESTS`).

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_certify.py` (match the file's existing executor-stub pattern; a minimal fake shown here):

```python
def test_certify_all_certifies_config_nodes():
    from python_deps.depgraph.schema import DepGraph, Node, NodeType, Layer, DiscoveredBy, State
    from python_deps.depgraph.certify import certify_all

    class FakeResult:
        def __init__(self, ok): self.ok = ok; self.stdout = ""; self.stderr = ""
    class FakeExecutor:
        def run(self, cmd):
            # printenv of a set var returns rc 0; everything else rc 0 too here.
            return FakeResult(ok=cmd.startswith("printenv DJANGO_SETTINGS_MODULE"))

    cfg = Node(id="config:DJANGO_SETTINGS_MODULE", type=NodeType.CONFIG,
               name="DJANGO_SETTINGS_MODULE", layer=Layer.CONFIG,
               discovered_by=DiscoveredBy.STATIC_SCAN,
               check_command="printenv DJANGO_SETTINGS_MODULE")
    g = DepGraph().with_node(cfg)
    out = certify_all(g, FakeExecutor())
    assert out.get("config:DJANGO_SETTINGS_MODULE").state is State.SATISFIED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_certify.py::test_certify_all_certifies_config_nodes -v`
Expected: FAIL — the CONFIG node is left `UNKNOWN` (its layer is never visited), so `state is State.SATISFIED` is False.

- [ ] **Step 3: Write minimal implementation**

In `certify.py`, add `Layer.CONFIG` to `_LAYER_ORDER` (after `PIP`/`NAMING`, before `TESTS`):

```python
_LAYER_ORDER: tuple[Layer, ...] = (
    Layer.INTERPRETER,
    Layer.SYSTEM,
    Layer.TOOLCHAIN,
    Layer.PIP,
    Layer.NAMING,
    Layer.CONFIG,
    Layer.TESTS,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_certify.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/certify.py tests/depgraph/test_certify.py
git commit -m "feat(depgraph): certify Config-layer nodes in certify_all"
```

---

### Task 5: `package → config-obligation` table

**Files:**
- Create: `src/python_deps/depgraph/config_tables.py`
- Test: `tests/depgraph/test_config_tables.py`

**Interfaces:**
- Produces: `config_obligations_for_package(name: str) -> list[tuple[str, str | None]]` — for a PyPI distribution name, the env vars that package *induces* as `(var_name, default_value_or_None)`; name-normalized lookup; returns a FRESH list; `[]` if unknown.

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_config_tables.py`:

```python
from python_deps.depgraph.config_tables import config_obligations_for_package


def test_django_induces_settings_module():
    obligations = config_obligations_for_package("django")
    assert ("DJANGO_SETTINGS_MODULE", None) in obligations


def test_lookup_is_name_normalized():
    assert config_obligations_for_package("Django") == config_obligations_for_package("django")


def test_unknown_package_returns_empty_list():
    assert config_obligations_for_package("requests") == []


def test_returns_fresh_list():
    a = config_obligations_for_package("django")
    a.append(("X", None))
    assert config_obligations_for_package("django") != a  # caller mutation isolated
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_config_tables.py -v`
Expected: FAIL with `ModuleNotFoundError: config_tables`.

- [ ] **Step 3: Write minimal implementation**

Create `src/python_deps/depgraph/config_tables.py`:

```python
"""Curated `package -> config-obligation` table (tier-6 analogue of
``tables.PACKAGE_TO_SYSTEM_DEPS``).  A distribution that, once installed, reads
an env var to function induces a Config *need*: ``django`` reads
``DJANGO_SETTINGS_MODULE``, ``celery`` reads a broker URL, etc.  Keyed by PyPI
distribution name; lookups are normalized so case/separators don't matter.

Each obligation is ``(env_var_name, default_value_or_None)``.  A default is given
only when a universally-safe test-time value exists; otherwise ``None`` (the
agent must supply it — see design §7.4 placeholder fix).
"""

from __future__ import annotations

from python_deps.import_mapping import normalize_package_name

PACKAGE_TO_CONFIG: dict[str, list[tuple[str, str | None]]] = {
    "django": [("DJANGO_SETTINGS_MODULE", None)],
    "celery": [("CELERY_BROKER_URL", None)],
    "boto3": [("AWS_ACCESS_KEY_ID", None), ("AWS_SECRET_ACCESS_KEY", None),
              ("AWS_DEFAULT_REGION", "us-east-1")],
}

_NORMALIZED: dict[str, list[tuple[str, str | None]]] = {
    normalize_package_name(name): obligations
    for name, obligations in PACKAGE_TO_CONFIG.items()
}


def config_obligations_for_package(name: str) -> list[tuple[str, str | None]]:
    """Env vars a distribution induces, or ``[]`` if unknown (fresh list)."""
    return list(_NORMALIZED.get(normalize_package_name(name), ()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_config_tables.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/config_tables.py tests/depgraph/test_config_tables.py
git commit -m "feat(depgraph): add package->config-obligation table"
```

---

### Task 6: AST scanner for `os.environ` / `os.getenv` reads (project-induced)

**Files:**
- Create: `src/python_deps/depgraph/config_scan.py`
- Test: `tests/depgraph/test_config_scan.py`

**Interfaces:**
- Produces: `scan_env_reads(repo_path: str) -> dict[str, str]` — maps each env var name the repo's own code reads to a one-line evidence string (`"<relpath>:<lineno>  <snippet>"`). Recognizes `os.environ['X']`, `os.environ.get('X')`, `os.getenv('X')`, and the bare `environ['X']` / `getenv('X')` forms. Skips the same excluded dirs as `scan.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/depgraph/test_config_scan.py`:

```python
import os
import textwrap
from python_deps.depgraph.config_scan import scan_env_reads


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))
    return p


def test_scan_finds_os_environ_subscript(tmp_path):
    _write(tmp_path, "app/settings.py", """
        import os
        SECRET_KEY = os.environ['SECRET_KEY']
        DEBUG = os.getenv('DEBUG')
        DB = os.environ.get('DATABASE_URL')
    """)
    found = scan_env_reads(str(tmp_path))
    assert set(found) == {"SECRET_KEY", "DEBUG", "DATABASE_URL"}
    assert "settings.py" in found["SECRET_KEY"]


def test_scan_skips_excluded_dirs(tmp_path):
    _write(tmp_path, "examples/demo.py", "import os\nX = os.environ['SHOULD_BE_IGNORED']\n")
    found = scan_env_reads(str(tmp_path))
    assert "SHOULD_BE_IGNORED" not in found


def test_scan_handles_unparseable_file(tmp_path):
    _write(tmp_path, "app/ok.py", "import os\nA = os.getenv('A')\n")
    _write(tmp_path, "app/broken.py", "def (:\n")  # syntax error
    found = scan_env_reads(str(tmp_path))
    assert "A" in found  # broken file skipped, good file still scanned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_config_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: config_scan`.

- [ ] **Step 3: Write minimal implementation**

Create `src/python_deps/depgraph/config_scan.py`:

```python
"""Static Config-tier discovery: the env vars a repo reads (project-induced).

Pure (no Executor, no network): walks the repo on disk, AST-parses each in-scope
``.py`` file, and records every ``os.environ[...]`` / ``os.environ.get(...)`` /
``os.getenv(...)`` read (and the bare ``environ``/``getenv`` forms).  Mirrors the
directory-exclusion scope of ``scan.py`` so examples/docs/build don't leak.
"""

from __future__ import annotations

import ast
import os
import re

_EXCLUDED_SEGMENTS: frozenset[str] = frozenset(
    {
        "examples", "example", "docs", "doc", "build", "dist", "samples",
        "sample", "benchmarks", "benchmark", "bench", "scripts", "script",
        ".github", ".tox", "node_modules", "site-packages", ".venv", "venv",
        ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    }
)


def _is_excluded(rel: str) -> bool:
    segments = {seg.lower() for seg in re.split(r"[\\/]+", rel) if seg}
    return bool(segments & _EXCLUDED_SEGMENTS)


def _const_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _var_from_call(call: ast.Call) -> str | None:
    """``os.getenv('X')`` / ``os.environ.get('X')`` -> ``'X'`` (first str arg)."""
    func = call.func
    name = None
    if isinstance(func, ast.Attribute):
        name = func.attr
    if name not in ("getenv", "get"):
        return None
    # environ.get must be on an `environ` object to count.
    if name == "get":
        owner = func.value
        owner_ok = (isinstance(owner, ast.Name) and owner.id == "environ") or (
            isinstance(owner, ast.Attribute) and owner.attr == "environ"
        )
        if not owner_ok:
            return None
    return _const_str(call.args[0]) if call.args else None


def _var_from_subscript(sub: ast.Subscript) -> str | None:
    """``os.environ['X']`` / ``environ['X']`` -> ``'X'``."""
    value = sub.value
    is_environ = (isinstance(value, ast.Name) and value.id == "environ") or (
        isinstance(value, ast.Attribute) and value.attr == "environ"
    )
    if not is_environ:
        return None
    return _const_str(sub.slice)


def _scan_source(src: str, rel: str, out: dict[str, str]) -> None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        var = None
        if isinstance(node, ast.Call):
            var = _var_from_call(node)
        elif isinstance(node, ast.Subscript):
            var = _var_from_subscript(node)
        if var and var not in out:
            snippet = " ".join((src.splitlines()[node.lineno - 1] if node.lineno else "").split())
            out[var] = f"{rel}:{getattr(node, 'lineno', 0)}  {snippet}"[:200]


def scan_env_reads(repo_path: str) -> dict[str, str]:
    """Map each env var the repo reads -> one-line evidence (file:line snippet)."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_SEGMENTS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, repo_path)
            if _is_excluded(rel):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            _scan_source(src, rel, out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_config_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/config_scan.py tests/depgraph/test_config_scan.py
git commit -m "feat(depgraph): AST scan for os.environ/getenv reads (config discovery)"
```

---

### Task 7: Discover `pydantic-settings` / `python-decouple` env reads

**Files:**
- Modify: `src/python_deps/depgraph/config_scan.py` (add `scan_framework_config_reads`)
- Test: `tests/depgraph/test_config_scan.py`

**Interfaces:**
- Produces: `scan_framework_config_reads(repo_path: str) -> dict[str, str]` — env vars read via the dominant modern patterns: `decouple.config('X')` / `environs` `env.str('X')` (string-literal arg), and `pydantic-settings` `BaseSettings` subclass annotated fields (field name upper-cased as the env var candidate). Same exclusion scope. (Known limitation noted inline: `env_prefix`/aliases not yet resolved.)

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_config_scan.py`:

```python
from python_deps.depgraph.config_scan import scan_framework_config_reads


def test_scan_decouple_and_environs(tmp_path):
    _write(tmp_path, "app/conf.py", """
        from decouple import config
        from environs import Env
        env = Env()
        SECRET = config('SECRET_KEY')
        PORT = env.int('PORT')
    """)
    found = scan_framework_config_reads(str(tmp_path))
    assert "SECRET_KEY" in found
    assert "PORT" in found


def test_scan_pydantic_basesettings_fields(tmp_path):
    _write(tmp_path, "app/settings.py", """
        from pydantic_settings import BaseSettings
        class Settings(BaseSettings):
            database_url: str
            redis_url: str = "redis://localhost"
    """)
    found = scan_framework_config_reads(str(tmp_path))
    assert "DATABASE_URL" in found
    assert "REDIS_URL" in found
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_config_scan.py -k framework -v`
Expected: FAIL with `ImportError: cannot import name 'scan_framework_config_reads'`.

- [ ] **Step 3: Write minimal implementation**

Add to `config_scan.py`:

```python
_DECOUPLE_FUNCS = frozenset({"config"})           # decouple.config('X')
_ENVIRONS_METHODS = frozenset({"str", "int", "bool", "float", "list", "url"})  # env.str('X')
_SETTINGS_BASES = frozenset({"BaseSettings", "BaseConfig"})


def _framework_var_from_call(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id in _DECOUPLE_FUNCS:
        return _const_str(call.args[0]) if call.args else None
    if isinstance(func, ast.Attribute) and func.attr in _ENVIRONS_METHODS:
        return _const_str(call.args[0]) if call.args else None
    return None


def _settings_fields(tree: ast.AST) -> list[str]:
    """Annotated field names of any class subclassing a *Settings base -> UPPER."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {b.id for b in node.bases if isinstance(b, ast.Name)} | {
            b.attr for b in node.bases if isinstance(b, ast.Attribute)
        }
        if not (base_names & _SETTINGS_BASES):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                out.append(stmt.target.id.upper())
    return out


def scan_framework_config_reads(repo_path: str) -> dict[str, str]:
    """Env vars read via pydantic-settings / decouple / environs (string-literal
    args + BaseSettings fields). NOTE: env_prefix / field aliases not yet resolved.
    """
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_SEGMENTS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, repo_path)
            if _is_excluded(rel):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    src = fh.read()
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    var = _framework_var_from_call(node)
                    if var and var not in out:
                        out[var] = f"{rel}:{getattr(node, 'lineno', 0)}  (framework config)"[:200]
            for field in _settings_fields(tree):
                out.setdefault(field, f"{rel}  (BaseSettings field)")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_config_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/config_scan.py tests/depgraph/test_config_scan.py
git commit -m "feat(depgraph): discover pydantic-settings/decouple/environs env reads"
```

---

### Task 8: Parse `.env.example` value hints + detect already-configured vars

**Files:**
- Modify: `src/python_deps/depgraph/config_scan.py` (add `parse_env_example`, `configured_vars`)
- Test: `tests/depgraph/test_config_scan.py`

**Interfaces:**
- Produces:
  - `parse_env_example(repo_path: str) -> dict[str, str]` — `{VAR: example_value}` from `.env.example`/`.env.sample`/`.env.template` (used for fix-value hints; these vars still *need* setting).
  - `configured_vars(repo_path: str) -> set[str]` — vars already provided at test time (a real `.env` file, and `[tool:pytest] env =` / `[pytest] env =` in `pytest.ini`/`setup.cfg`/`tox.ini`). Used to SUPPRESS false-missing nodes.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_config_scan.py`:

```python
from python_deps.depgraph.config_scan import parse_env_example, configured_vars


def test_parse_env_example_values(tmp_path):
    _write(tmp_path, ".env.example", "DEBUG=True\nDATABASE_URL=postgres://localhost/db\n# comment\nEMPTY=\n")
    vals = parse_env_example(str(tmp_path))
    assert vals["DEBUG"] == "True"
    assert vals["DATABASE_URL"] == "postgres://localhost/db"
    assert vals.get("EMPTY", "") == ""


def test_configured_vars_from_real_dotenv_and_pytest_ini(tmp_path):
    _write(tmp_path, ".env", "ALREADY_SET=1\n")
    _write(tmp_path, "pytest.ini", "[pytest]\nenv =\n    DJANGO_SETTINGS_MODULE=app.settings\n")
    provided = configured_vars(str(tmp_path))
    assert "ALREADY_SET" in provided
    assert "DJANGO_SETTINGS_MODULE" in provided
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_config_scan.py -k "env_example or configured" -v`
Expected: FAIL with `ImportError: cannot import name 'parse_env_example'`.

- [ ] **Step 3: Write minimal implementation**

Add to `config_scan.py`:

```python
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_EXAMPLE_FILES = ("(.env.example)", "(.env.sample)", "(.env.template)")  # see _read_env_files


def _read_env_pairs(path: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.lstrip().startswith("#"):
                    continue
                m = _ENV_LINE.match(line)
                if m:
                    pairs[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        return {}
    return pairs


def parse_env_example(repo_path: str) -> dict[str, str]:
    """`{VAR: example_value}` from .env.example/.sample/.template (value hints)."""
    out: dict[str, str] = {}
    for fname in (".env.example", ".env.sample", ".env.template"):
        out.update(_read_env_pairs(os.path.join(repo_path, fname)))
    return out


def configured_vars(repo_path: str) -> set[str]:
    """Vars already provided at test time -> suppress as false-missing.

    Sources: a real ``.env`` file, and an ``env =`` block in pytest config
    (``pytest.ini`` / ``setup.cfg`` / ``tox.ini``; pytest-env style).
    """
    provided: set[str] = set(_read_env_pairs(os.path.join(repo_path, ".env")))
    for cfg in ("pytest.ini", "setup.cfg", "tox.ini"):
        path = os.path.join(repo_path, cfg)
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        in_env = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^env\s*=", stripped):
                in_env = True
                continue
            if in_env:
                m = _ENV_LINE.match(line)
                if m and (line.startswith(" ") or line.startswith("\t")):
                    provided.add(m.group(1))
                elif stripped and not line[:1].isspace():
                    in_env = False
    return provided
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_config_scan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/config_scan.py tests/depgraph/test_config_scan.py
git commit -m "feat(depgraph): parse .env.example value hints + already-configured vars"
```

---

### Task 9: `scan_config` orchestrator — build CONFIG nodes + edges + suppression

**Files:**
- Modify: `src/python_deps/depgraph/config_scan.py` (add `scan_config`)
- Test: `tests/depgraph/test_config_scan.py`

**Interfaces:**
- Consumes: `scan_env_reads`, `scan_framework_config_reads`, `parse_env_example`, `configured_vars` (Tasks 6–8); `config_obligations_for_package` (Task 5); `config_id` (Task 1); `Node`/`Edge`/`NodeType`/`Layer`/`DiscoveredBy`/`State`/`EdgeType` from `schema`.
- Produces: `scan_config(repo_path: str, graph: DepGraph) -> DepGraph` — appends `CONFIG` nodes (check `printenv VAR`, fix `env:VAR=<value-or-?>`) and `requires` edges (`Project → CONFIG` for project/framework reads; `Package → CONFIG` for package-induced). Suppresses any var in `configured_vars`. Returns a NEW graph; existing nodes untouched.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_config_scan.py`:

```python
from python_deps.depgraph.config_scan import scan_config
from python_deps.depgraph.schema import (
    DepGraph, Node, NodeType, Layer, DiscoveredBy, State, EdgeType,
)
from python_deps.depgraph.ids import project_id, package_id, config_id


def _graph_with_project_and_pkg(proj="app", pkg="django"):
    p = Node(id=project_id(proj), type=NodeType.PROJECT, name=proj, layer=Layer.PIP,
             discovered_by=DiscoveredBy.STATIC_SCAN)
    d = Node(id=package_id(pkg, "4.2"), type=NodeType.PACKAGE, name=pkg, layer=Layer.PIP,
             discovered_by=DiscoveredBy.RESOLVER, version="4.2")
    return DepGraph().with_node(p).with_node(d)


def test_project_induced_config_node_and_edge(tmp_path):
    _write(tmp_path, "app/settings.py", "import os\nSECRET_KEY = os.environ['SECRET_KEY']\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    node = g.get(config_id("SECRET_KEY"))
    assert node is not None and node.type is NodeType.CONFIG and node.tier == 6
    assert node.check_command == "printenv SECRET_KEY"
    assert node.fix_candidates == ("env:SECRET_KEY=?",)
    assert any(e.src == project_id("app") and e.dst == config_id("SECRET_KEY")
               for e in g.edges)


def test_package_induced_config_node_and_edge(tmp_path):
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg(pkg="django"))
    node = g.get(config_id("DJANGO_SETTINGS_MODULE"))
    assert node is not None
    assert any(e.src == package_id("django", "4.2") and e.dst == config_id("DJANGO_SETTINGS_MODULE")
               for e in g.edges)


def test_value_hint_from_env_example(tmp_path):
    _write(tmp_path, "app/s.py", "import os\nX = os.getenv('DEBUG')\n")
    _write(tmp_path, ".env.example", "DEBUG=False\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    assert g.get(config_id("DEBUG")).fix_candidates == ("env:DEBUG=False",)


def test_already_configured_var_is_suppressed(tmp_path):
    _write(tmp_path, "app/s.py", "import os\nX = os.environ['ALREADY']\n")
    _write(tmp_path, ".env", "ALREADY=1\n")
    g = scan_config(str(tmp_path), _graph_with_project_and_pkg())
    assert g.get(config_id("ALREADY")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_config_scan.py -k scan_config -v`
Expected: FAIL with `ImportError: cannot import name 'scan_config'`.

- [ ] **Step 3: Write minimal implementation**

Add to `config_scan.py` (imports at top of file: `from .ids import config_id`; `from .schema import (DepGraph, Node, NodeType, Layer, DiscoveredBy, State, Edge, EdgeType)`; `from .config_tables import config_obligations_for_package`):

```python
def _config_node(var: str, value: str | None, evidence: str | None,
                 discovered_by: DiscoveredBy) -> Node:
    fix = f"env:{var}={value}" if value else f"env:{var}=?"
    return Node(
        id=config_id(var),
        type=NodeType.CONFIG,
        name=var,
        layer=Layer.CONFIG,
        discovered_by=discovered_by,
        state=State.UNKNOWN,
        check_command=f"printenv {var}",
        fix_candidates=(fix,),
        chosen_fix=fix,
        evidence=evidence,
        provenance="config scan",
    )


def scan_config(repo_path: str, graph: DepGraph) -> DepGraph:
    """Append project- and package-induced CONFIG nodes + requires edges.

    Suppresses vars already provided at test time (``configured_vars``). Returns a
    NEW graph; no-op-safe when there is no Project/Package node to anchor to.
    """
    suppressed = configured_vars(repo_path)
    values = parse_env_example(repo_path)

    project = next((n for n in graph.nodes if n.type is NodeType.PROJECT), None)
    test = next((n for n in graph.nodes if n.type is NodeType.TEST), None)
    anchor = project or test  # project-induced reads hang off Project (or Test goal)

    new = graph

    def _add(var: str, evidence: str | None, src_id: str | None,
             discovered_by: DiscoveredBy) -> None:
        nonlocal new
        if var in suppressed:
            return
        if new.get(config_id(var)) is None:
            new = new.with_node(_config_node(var, values.get(var), evidence, discovered_by))
        if src_id is not None:
            new = new.with_edge(Edge(src=src_id, dst=config_id(var),
                                     relation=EdgeType.REQUIRES, origin="config"))

    # Project-induced: os.environ reads + framework config readers.
    project_reads = dict(scan_env_reads(repo_path))
    project_reads.update(scan_framework_config_reads(repo_path))
    for var, evidence in project_reads.items():
        _add(var, evidence, anchor.id if anchor else None, DiscoveredBy.STATIC_SCAN)

    # Package-induced: each resolved Package's curated config obligations.
    for pkg in [n for n in graph.nodes if n.type is NodeType.PACKAGE]:
        for var, default in config_obligations_for_package(pkg.name):
            ev = f"induced by package {pkg.name}"
            # a curated default becomes the value hint when .env.example has none.
            if default is not None and var not in values:
                values[var] = default
            _add(var, ev, pkg.id, DiscoveredBy.RESOLVER)

    return new
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_config_scan.py -v`
Expected: PASS (all config_scan tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/config_scan.py tests/depgraph/test_config_scan.py
git commit -m "feat(depgraph): scan_config orchestrator (CONFIG nodes + edges + suppression)"
```

---

### Task 10: Wire `scan_config` into the build pipeline

**Files:**
- Modify: `src/python_deps/depgraph/build.py` (import + new stage call near line 240)
- Test: `tests/depgraph/test_build.py`

**Interfaces:**
- Consumes: `scan_config` (Task 9).
- Produces: `build_dep_graph` graphs contain CONFIG nodes (discovered from the repo), stamped with the resolver cycle, certified by the existing Stage 5.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_build.py` (follow the file's existing fixture pattern for a fake/stub container executor + a temp repo; the assertion is the new behavior):

```python
def test_build_includes_config_nodes(tmp_path, fake_container_executor, fake_host_executor):
    # minimal repo that reads an env var its code needs
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0"\n')
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "settings.py").write_text("import os\nSECRET_KEY = os.environ['SECRET_KEY']\n")

    from python_deps.depgraph.build import build_dep_graph
    from python_deps.depgraph.schema import NodeType

    graph = build_dep_graph(str(tmp_path), fake_container_executor,
                            host_executor=fake_host_executor, target_python="3.11")
    config_names = {n.name for n in graph.nodes if n.type is NodeType.CONFIG}
    assert "SECRET_KEY" in config_names
```

> If `tests/depgraph/test_build.py` has no reusable executor fixtures, reuse the same stub-executor construction already used by the nearest existing test in that file (do not invent a new Docker dependency); the point of this test is only that a CONFIG node appears after the wiring.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_build.py::test_build_includes_config_nodes -v`
Expected: FAIL — no CONFIG node (scan_config not wired yet).

- [ ] **Step 3: Write minimal implementation**

In `build.py`, add the import (with the other depgraph imports near the top):

```python
from python_deps.depgraph.config_scan import scan_config
```

In `build_dep_graph`, insert the stage immediately after `seed_predicted_native` and **before** the `resolver_ids` computation, so CONFIG nodes get the resolver-cycle stamp. The existing lines are:

```python
    graph = seed_predicted_native(graph)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
```

Change to:

```python
    graph = seed_predicted_native(graph)
    # Stage 3c — Config tier (tier 6): project- and package-induced env-var needs
    # appended to the same graph (design 2026-06-25 six-tier model). Static; the
    # existing certify pass (Stage 5) certifies their `printenv` presence.
    graph = scan_config(repo_path, graph)
    resolver_ids = {n.id for n in graph.nodes} - pre_resolve_ids
    graph = _restamp(graph, resolver_ids, _RESOLVER_CYCLE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_build.py -v`
Expected: PASS (the new test, and no regression in existing build tests).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/build.py tests/depgraph/test_build.py
git commit -m "feat(depgraph): wire scan_config into the build pipeline (tier 6)"
```

---

### Task 11: Render CONFIG nodes in the advisory

**Files:**
- Modify: `src/python_deps/depgraph/advise.py` (`_LAYER_RANK`, and a `(value needed)` marker for placeholder fixes)
- Test: `tests/depgraph/test_advise.py`

**Interfaces:**
- Consumes: `Layer.CONFIG`; CONFIG nodes with `fix_candidates=("env:VAR=?",)`.
- Produces: `render_dep_graph_advisory` orders CONFIG nodes in the frontier (rank after PIP) and appends `  (value needed)` to a fix-candidate line whose fix ends in `=?`.

- [ ] **Step 1: Write the failing test**

Add to `tests/depgraph/test_advise.py`:

```python
def test_advisory_renders_missing_config_node_with_value_needed():
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.advise import render_dep_graph_advisory

    cfg = Node(id="config:SECRET_KEY", type=NodeType.CONFIG, name="SECRET_KEY",
               layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.MISSING, check_command="printenv SECRET_KEY",
               fix_candidates=("env:SECRET_KEY=?",))
    out = render_dep_graph_advisory(DepGraph().with_node(cfg))
    assert "SECRET_KEY" in out
    assert "CONFIG" in out
    assert "value needed" in out


def test_advisory_config_with_derived_value_has_no_marker():
    from python_deps.depgraph.schema import (
        DepGraph, Node, NodeType, Layer, DiscoveredBy, State,
    )
    from python_deps.depgraph.advise import render_dep_graph_advisory

    cfg = Node(id="config:DEBUG", type=NodeType.CONFIG, name="DEBUG",
               layer=Layer.CONFIG, discovered_by=DiscoveredBy.STATIC_SCAN,
               state=State.MISSING, check_command="printenv DEBUG",
               fix_candidates=("env:DEBUG=False",))
    out = render_dep_graph_advisory(DepGraph().with_node(cfg))
    assert "value needed" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/depgraph/test_advise.py -k value_needed -v`
Expected: FAIL — `value needed` not present (no marker logic yet).

- [ ] **Step 3: Write minimal implementation**

In `advise.py`, add `Layer.CONFIG` to `_LAYER_RANK` (rank after PIP; sharing a rank value is harmless — the sort key is `(rank, name)`):

```python
_LAYER_RANK: dict[Layer, int] = {
    Layer.INTERPRETER: 0,
    Layer.SYSTEM: 1,
    Layer.TOOLCHAIN: 2,
    Layer.PIP: 3,
    Layer.NAMING: 4,
    Layer.CONFIG: 5,
    Layer.RUNTIME: 5,
    Layer.TESTS: 6,
}
```

In `render_dep_graph_advisory`, change the fix-candidate frontier line to add the marker when a fix is a `=?` placeholder. The existing line is:

```python
            if n.fix_candidates:
                lines.append(f"            fix-candidate: {', '.join(n.fix_candidates)}")
```

Change to:

```python
            if n.fix_candidates:
                marker = "  (value needed)" if any(
                    f.endswith("=?") for f in n.fix_candidates
                ) else ""
                lines.append(
                    f"            fix-candidate: {', '.join(n.fix_candidates)}{marker}"
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/depgraph/test_advise.py -v`
Expected: PASS (new tests + no regression).

- [ ] **Step 5: Commit**

```bash
git add src/python_deps/depgraph/advise.py tests/depgraph/test_advise.py
git commit -m "feat(depgraph): render CONFIG tier in advisory with value-needed marker"
```

---

### Task 12: Full-suite regression + off-state invariant check

**Files:**
- Test: the whole `tests/depgraph/` suite (no new production code)

**Interfaces:** none (verification task).

- [ ] **Step 1: Run the full depgraph suite**

Run: `pytest tests/depgraph/ -q`
Expected: PASS — all existing tests green (default-safe additions), plus the new tests from Tasks 1–11.

- [ ] **Step 2: Confirm the off-state invariant**

The new code only runs inside `build_dep_graph`; the feature is gated upstream by `--enable-dep-graph` / arm `v1gd`. Confirm no module imported at process start unconditionally calls `scan_config`:

Run: `grep -rn "scan_config" src/ | grep -v "def scan_config" | grep -v config_scan.py`
Expected: exactly one hit — the call site in `build.py`. (No top-level/import-time invocation.)

- [ ] **Step 3: Commit (if any lint/format fixes were needed)**

```bash
git add -p   # stage only intended changes
git commit -m "test(depgraph): green full suite for config-tier slice"
```

> If Step 1 and the grep are clean with nothing to stage, skip the commit — the slice is already committed task-by-task.

---

## Self-Review

**Spec coverage (against `2026-06-25-six-tier-environment-world-model-design.md`):**
- §4 schema (NodeTypes, `tier`, `EDGE_RULES`, `Layer.CONFIG`) → Tasks 1–3. ✅
- §5 certification (`Layer.CONFIG` in `_LAYER_ORDER`) → Task 4. ✅ (Rebuild-and-recertify + `PRESENT_UNVERIFIED` are explicitly **out of scope** — fix-application slice.)
- §7.2 discovery sources (`os.environ`, framework readers, `.env.example`, pre-cert filter, package→config table) → Tasks 5–9. ✅
- §7.3 connection (Project-induced / Package-induced edges) → Task 9. ✅
- §7.4 node shape + placeholder fix → Task 9. ✅
- §7.5 phase 1 wiring + certification + advisory → Tasks 10–11. ✅
- §8 advisory render → Task 11. ✅ (The A/B *measurement* is a separate validation effort, not code in this plan.)
- **Deferred (noted, not gaps):** Services/Platform/Data tiers, failure-driven discovery, fix-application/env-bake, rebuild-and-recertify, `PRESENT_UNVERIFIED` — all spec §10 Phase 2/3+.

**Placeholder scan:** none — every code/test step contains the literal code.

**Type consistency:** `config_id` returns `config:<VAR>` (Task 1) and is used identically in Tasks 9/test. `scan_config(repo_path, graph) -> DepGraph` signature consistent across Tasks 9–10. `config_obligations_for_package -> list[tuple[str, str|None]]` consumed exactly that way in Task 9. `Node.tier` (int, Task 2) read in Task 9 test (`== 6`). Fix string format `env:VAR=value` / `env:VAR=?` consistent across Tasks 9 and 11.

**Open question carried from spec §13:** `scan_config` placed after `seed_predicted_native` (Task 10) — both consume the resolved Package layer; order is functionally independent (config vs native nodes), fixed deterministically here.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-six-tier-config-tier-slice.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session via executing-plans, batched with checkpoints.

Which approach?
