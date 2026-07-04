# Role-Aware Declaration Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the construction-only PACKAGE closure cover the requirements a repo needs to *run its tests* (testability scope) by teaching the declaration reader three roles — runtime, feature-extra, and dev/test group — instead of the current two.

**Architecture:** Three edits to the existing declared-only reader, no new pipeline stage. (1) A new PEP 735 `[dependency-groups]` collector in `evidence.py`. (2) `requirements*.txt` reading becomes role-aware (filename → runtime vs dev/test/docs) and follows `-r`/`-c` includes and captures `-e .[extras]` signals. (3) `roots.select_roots` gains a fixed testability-scope policy `_in_test_scope` that default-includes dev/test groups (minus a docs/release denylist) while keeping feature-extras conflict-gated. Resolution (`resolve_closure`/`uv lock`) is unchanged — only the set of declared root names fed to it changes.

**Tech Stack:** Python 3.10+, `packaging` (Requirement/Marker parsing), `tomllib`, pytest. Pure/deterministic reader code; the eval harness uses `uv lock` (network) and is run only in the final validation task.

## Global Constraints

- **One clean path, no flags.** This is the v3-core reference impl (interpretability priority). Do NOT add an arm/flag/config toggle to switch scopes — the new testability scope is *the* behavior. `select_roots` keeps its signature; the default (`needed_extras=frozenset()`) now yields testability scope.
- **Declared-only invariant holds.** Imports NEVER generate roots. Every root still carries `import_id=None`. Nothing in this plan reads `evidence.imports` to add a root. Extras the tests import but no declaration signals stay for the Phase-A repair loop, not for root selection.
- **Immutability / purity.** Collectors are pure appends into the mutable `PythonDependencyEvidence` during collection (existing pattern). `select_roots` stays pure and returns a new list. `PythonRequirement` stays `frozen`.
- **New `kind` value is exactly `"dev_group"`.** Existing kinds unchanged: `"dependency"` (runtime), `"optional_dependency"` (feature extra), `"constraint"`. The group/role sub-label stays embedded in `source` (as extras already do) — no new field on `PythonRequirement`.
- **Docs/release denylist (verbatim):** `{"docs", "doc", "documentation", "release", "publish", "deploy", "benchmark", "benchmarks", "profiling", "examples", "demo"}`. Matched case-insensitively against the normalized group name.
- **Open decision RESOLVED — default-include catch-all dev groups (`dev`, `all`).** Per the spec's recall-first recommendation and the "cover MOST requirements to run the tests" goal, catch-all groups are included (they are dev_group, not on the denylist). Do NOT add a stricter allowlist. If the final eval shows material `dev`-group over-pull, that is surfaced to the human as a follow-up, not silently changed here.
- **pkg_layer coupling.** `src/python_deps/pkg_layer/contract.py` also calls `collect_python_dependency_evidence`, but its own `select_roots`/`in_scope_deps` only accept `kind in {"dependency","optional_dependency"}`, so new `dev_group` rows are ignored there (its closure is unchanged, except dev/test requirements files correctly stop being read as runtime `dependency` rows). pkg_layer parity is a NON-goal. Task 4 runs the pkg_layer suite to confirm no unexpected shift; the A/B eval is regenerated in Task 5.
- **Commit-local only. NEVER push.** Standing constraint for this branch.
- **Temp/scratch files** go under `/Users/john/.claude/jobs/366037cb/tmp` (the job tmp), never `/tmp`.

## File Structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/python_deps/models.py` | Add `used_extras: set[str]` to `PythonDependencyEvidence` + serialize it | 1 |
| `src/python_deps/depgraph/roots.py` | Extend `_requirement_group` regex (parse `dependency-groups.*` / `requirements-file.*`), add `_DEV_GROUP_DENYLIST`, add `_in_test_scope`, wire `in_scope_extras` | 1, 4 |
| `src/python_deps/evidence.py` | New `_collect_dependency_groups` (PEP 735); role-aware `_collect_requirements_files` (filename role, nested discovery, `-e`/`-r`/`-c` directives, `used_extras`) | 2, 3 |
| `tests/depgraph/test_evidence.py` | Unit tests for the new collectors | 2, 3 |
| `tests/depgraph/test_roots.py` | Unit tests for `_requirement_group`, `_in_test_scope`, scope wiring | 1, 4 |
| `tests/depgraph/test_build.py`, `tests/pkg_layer/*` | Update any fixture whose closure legitimately changes under testability scope | 4 |

---

## Task 1: Shared role vocabulary (`used_extras` field + `_requirement_group` extension + denylist)

Lay the data foundation both new collectors and the scope policy depend on: the evidence field that carries `-e .[…]` signals, the regex that parses the two new source shapes, and the docs/release denylist constant. All inert until later tasks produce/consume them, so each is unit-tested in isolation here.

**Files:**
- Modify: `src/python_deps/models.py` (add field to `PythonDependencyEvidence`, extend `to_dict`)
- Modify: `src/python_deps/depgraph/roots.py:137-143` (extend `_OPTIONAL_GROUP_RE` + add `_DEV_GROUP_DENYLIST`)
- Test: `tests/depgraph/test_evidence.py`, `tests/depgraph/test_roots.py`

**Interfaces:**
- Produces: `PythonDependencyEvidence.used_extras: set[str]` (default empty, lowercased extra names); `to_dict()["used_extras"]` = sorted list.
- Produces: `roots._requirement_group(source)` now also returns the group for sources of the form `…dependency-groups.<name>` and `…requirements-file.<role>`.
- Produces: `roots._DEV_GROUP_DENYLIST: frozenset[str]` (verbatim from Global Constraints).

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_roots.py`:

```python
from python_deps.depgraph.roots import _requirement_group, _DEV_GROUP_DENYLIST


def test_requirement_group_parses_optional_dependencies_source():
    assert _requirement_group("pyproject.toml:project.optional-dependencies.test") == "test"


def test_requirement_group_parses_extras_require_source():
    assert _requirement_group("setup.cfg:options.extras_require.docs") == "docs"


def test_requirement_group_parses_dependency_groups_source():
    assert _requirement_group("pyproject.toml:dependency-groups.typing") == "typing"


def test_requirement_group_parses_requirements_file_source():
    assert _requirement_group("requirements-file.dev") == "dev"


def test_requirement_group_no_match_returns_empty():
    assert _requirement_group("pyproject.toml:project.dependencies") == ""


def test_dev_group_denylist_contents():
    assert _DEV_GROUP_DENYLIST == frozenset(
        {
            "docs", "doc", "documentation",
            "release", "publish", "deploy",
            "benchmark", "benchmarks", "profiling",
            "examples", "demo",
        }
    )
```

Append to `tests/depgraph/test_evidence.py`:

```python
def test_evidence_used_extras_defaults_to_empty_set():
    ev = PythonDependencyEvidence(repo_path="/x")
    assert ev.used_extras == set()


def test_evidence_to_dict_includes_sorted_used_extras():
    ev = PythonDependencyEvidence(repo_path="/x")
    ev.used_extras.update({"socks", "http2"})
    assert ev.to_dict()["used_extras"] == ["http2", "socks"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/depgraph/test_roots.py::test_requirement_group_parses_dependency_groups_source tests/depgraph/test_evidence.py::test_evidence_used_extras_defaults_to_empty_set -q`
Expected: FAIL — `ImportError: cannot import name '_DEV_GROUP_DENYLIST'` and `AttributeError: ... has no attribute 'used_extras'`.

- [ ] **Step 3: Implement the model field**

In `src/python_deps/models.py`, add `used_extras` to `PythonDependencyEvidence` (after `constraint_dependencies`):

```python
    constraint_dependencies: list[PythonRequirement] = field(default_factory=list)
    used_extras: set[str] = field(default_factory=set)
    imports: list[ImportFinding] = field(default_factory=list)
```

And in its `to_dict`, add the key (place it right after `constraint_dependencies`):

```python
            "constraint_dependencies": [item.to_dict() for item in self.constraint_dependencies],
            "used_extras": sorted(self.used_extras),
```

- [ ] **Step 4: Implement the regex + denylist**

In `src/python_deps/depgraph/roots.py`, replace the `_OPTIONAL_GROUP_RE` block (lines ~133-143) with:

```python
# Group/role sub-label embedded at the tail of a requirement's ``source`` string
# by evidence.py:
#   pyproject.toml:project.optional-dependencies.test  -> test    (feature extra)
#   setup.cfg:options.extras_require.docs              -> docs    (feature extra)
#   pyproject.toml:dependency-groups.typing            -> typing  (PEP 735 dev group)
#   requirements-file.dev                              -> dev     (dev/test reqs file)
_OPTIONAL_GROUP_RE = re.compile(
    r"(?:optional-dependencies|extras_require|dependency-groups|requirements-file)\.(.+)$"
)


def _requirement_group(source: str) -> str:
    """Group/role sub-label a non-runtime requirement belongs to (``""`` if none)."""
    match = _OPTIONAL_GROUP_RE.search(source or "")
    return match.group(1) if match else ""


# Dev/test groups NOT needed to run the test suite: docs builders and
# release/packaging tooling. Every OTHER dev_group (test, lint, typing, dev, ...)
# is default-included (recall-first; the testability gate + Phase-A repair back it
# up). Matched case-insensitively against the normalized group name.
_DEV_GROUP_DENYLIST: frozenset[str] = frozenset(
    {
        "docs", "doc", "documentation",
        "release", "publish", "deploy",
        "benchmark", "benchmarks", "profiling",
        "examples", "demo",
    }
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/depgraph/test_roots.py tests/depgraph/test_evidence.py -q`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/models.py src/python_deps/depgraph/roots.py tests/depgraph/test_roots.py tests/depgraph/test_evidence.py
git commit -m "feat(evidence): role vocabulary — used_extras field, dev_group source parsing, docs/release denylist"
```

---

## Task 2: PEP 735 `_collect_dependency_groups` collector

Read `[dependency-groups]` from `pyproject.toml`, resolving `include-group` references transitively with cycle detection, and emit each concrete requirement as `kind="dev_group"`. This is the dominant recall fix (62% of misses are test-runner tooling declared here).

**Files:**
- Modify: `src/python_deps/evidence.py` (add collector + helper, register in the `collectors` tuple)
- Test: `tests/depgraph/test_evidence.py`

**Interfaces:**
- Consumes: `_add_requirement_line(target, line, source, *, kind, trust)` (existing), `evidence.declared_dependencies`, `evidence.collection_errors`.
- Produces: for each member of `[dependency-groups].<group>`, a `PythonRequirement(kind="dev_group", source=f"pyproject.toml:dependency-groups.{group}", trust="medium")`. `include-group` members are flattened into the *including* top-level group's rows.

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_evidence.py`:

```python
def _canon_deps(evidence, kind):
    return {(r.name, r.kind, r.source) for r in evidence.declared_dependencies if r.kind == kind}


def test_collect_dependency_groups_basic(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"
            dependencies = ["flask"]

            [dependency-groups]
            test = ["pytest", "pytest-cov"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    dev = _canon_deps(ev, "dev_group")
    assert ("pytest", "dev_group", "pyproject.toml:dependency-groups.test") in dev
    assert ("pytest-cov", "dev_group", "pyproject.toml:dependency-groups.test") in dev
    # runtime dep still classified as dependency
    assert any(r.name == "flask" and r.kind == "dependency" for r in ev.declared_dependencies)


def test_collect_dependency_groups_include_group_flattens_transitively(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"

            [dependency-groups]
            test = ["pytest"]
            typing = [{include-group = "test"}, "mypy"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    typing = {r.name for r in ev.declared_dependencies
              if r.source == "pyproject.toml:dependency-groups.typing"}
    assert typing == {"pytest", "mypy"}  # test's member flattened under typing


def test_collect_dependency_groups_cycle_terminates_and_records_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"

            [dependency-groups]
            a = [{include-group = "b"}, "pkg-a"]
            b = [{include-group = "a"}, "pkg-b"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))  # must not hang
    names = {r.name for r in ev.declared_dependencies if r.kind == "dev_group"}
    assert "pkg-a" in names and "pkg-b" in names
    assert any("cycle" in e.lower() for e in ev.collection_errors)


def test_collect_dependency_groups_absent_is_noop(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "proj"
            version = "0.1.0"
            dependencies = ["flask"]
            """
        ),
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert not [r for r in ev.declared_dependencies if r.kind == "dev_group"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/depgraph/test_evidence.py -k dependency_groups -q`
Expected: FAIL — no `dev_group` rows produced (collector not registered).

- [ ] **Step 3: Implement the collector**

In `src/python_deps/evidence.py`, register it in the `collectors` tuple (add right after `_collect_pyproject_metadata`):

```python
    collectors = (
        _collect_pyproject_metadata,
        _collect_dependency_groups,
        _collect_setup_cfg_metadata,
        _collect_setup_py_metadata,
        _collect_requirements_files,
        _collect_constraints_files,
    )
```

Add the collector + helper (place after `_collect_pyproject_metadata`):

```python
def _collect_dependency_groups(root: Path, evidence: PythonDependencyEvidence) -> None:
    """PEP 735 ``[dependency-groups]`` reader.

    Each group maps to a list whose members are requirement strings and/or
    ``{include-group = "<name>"}`` reference objects. include-group references are
    resolved transitively (a group may include another group) with cycle
    detection; the flattened requirements are attributed to the TOP-LEVEL group
    being expanded and tagged ``kind="dev_group"``.
    """
    path = root / "pyproject.toml"
    if not path.is_file() or tomllib is None:
        return
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    groups = data.get("dependency-groups", {})
    if not isinstance(groups, dict):
        return
    for group_name in groups:
        if not isinstance(group_name, str):
            continue
        requirements, cycle = _resolve_dependency_group(group_name, groups, ())
        if cycle:
            evidence.collection_errors.append(
                f"_collect_dependency_groups: include-group cycle involving '{group_name}'"
            )
        for requirement in requirements:
            _add_requirement_line(
                evidence.declared_dependencies,
                requirement,
                f"pyproject.toml:dependency-groups.{group_name}",
                kind="dev_group",
                trust="medium",
            )


def _resolve_dependency_group(
    name: str, groups: dict, seen: tuple[str, ...]
) -> tuple[list[str], bool]:
    """Flatten a dependency-group's members to concrete requirement strings.

    Returns ``(requirement_strings, cycle_detected)``. ``include-group`` refs are
    expanded depth-first; a group already on the current ``seen`` path is a cycle:
    its expansion is truncated (skipped) and ``cycle_detected`` is set True.
    """
    if name in seen:
        return [], True
    members = groups.get(name)
    if not isinstance(members, list):
        return [], False
    out: list[str] = []
    cycle = False
    for member in members:
        if isinstance(member, str):
            out.append(member)
        elif isinstance(member, dict) and isinstance(member.get("include-group"), str):
            sub, sub_cycle = _resolve_dependency_group(member["include-group"], groups, seen + (name,))
            out.extend(sub)
            cycle = cycle or sub_cycle
    return out, cycle
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/depgraph/test_evidence.py -k dependency_groups -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full evidence + roots suite (no regressions)**

Run: `python -m pytest tests/depgraph/test_evidence.py tests/depgraph/test_roots.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/evidence.py tests/depgraph/test_evidence.py
git commit -m "feat(evidence): PEP 735 [dependency-groups] collector with transitive include-group + cycle detection"
```

---

## Task 3: Role-aware `_collect_requirements_files` (filename role, nested discovery, `-e`/`-r`/`-c`)

Replace the "every requirements line is runtime" behavior. Classify each requirements file by its name/dir into runtime vs dev/test/docs; discover files in allowlisted nested dirs; follow `-r`/`-c` includes (cycle- and depth-guarded); and capture `-e .[extras]` self-install signals into `evidence.used_extras`.

**Files:**
- Modify: `src/python_deps/evidence.py` (rewrite `_collect_requirements_files`; add `_requirements_role`, `_role_kind_source`, `_discover_requirements_files`, `_ingest_requirements_file`, `_iter_raw_requirement_lines`, directive regexes)
- Test: `tests/depgraph/test_evidence.py`

**Interfaces:**
- Consumes: `_add_requirement_line`, `_strip_inline_comment`, `_relative_source`, `_glob_metadata_files`, `evidence.declared_dependencies`, `evidence.constraint_dependencies`, `evidence.used_extras`.
- Produces: requirements-file rows with `kind ∈ {"dependency","dev_group"}` and `source` = the relative file path (runtime) or `f"requirements-file.{role}"` (dev/test/docs); `-e .[a,b]` → `evidence.used_extras ⊇ {"a","b"}` (lowercased); `-r`/`-c` includes followed with role re-inferred on the referenced file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_evidence.py`:

```python
def _by_name(evidence):
    return {r.name: r for r in evidence.declared_dependencies}


def test_requirements_txt_is_runtime(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["flask"].kind == "dependency"


def test_requirements_dev_is_dev_group(tmp_path):
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["pytest"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.dev"


def test_nested_docs_requirements_is_dev_group_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.txt").write_text("sphinx\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["sphinx"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.docs"


def test_nested_requirements_dir_test_file_is_dev_group_test(tmp_path):
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "test.txt").write_text("pytest-xdist\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    req = _by_name(ev)["pytest-xdist"]
    assert req.kind == "dev_group"
    assert req.source == "requirements-file.test"


def test_nested_requirements_dir_base_file_is_runtime(tmp_path):
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "base.txt").write_text("flask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert _by_name(ev)["flask"].kind == "dependency"


def test_editable_self_extras_captured_into_used_extras(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "-e .[http2,socks]\npytest\n", encoding="utf-8"
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert {"http2", "socks"} <= ev.used_extras
    # the -e line is NOT added as a distribution named "."/project
    assert "." not in _by_name(ev)


def test_bare_editable_self_is_ignored(tmp_path):
    (tmp_path / "requirements.txt").write_text("-e .\nflask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert ev.used_extras == set()
    assert "flask" in _by_name(ev)


def test_dash_r_include_is_followed_with_referenced_file_role(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("-r requirements.txt\npytest\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))
    by = _by_name(ev)
    assert by["flask"].kind == "dependency"       # base file's role
    assert by["pytest"].kind == "dev_group"        # dev file's role
    assert by["pytest"].source == "requirements-file.dev"


def test_dash_r_self_cycle_terminates(tmp_path):
    (tmp_path / "requirements.txt").write_text("-r requirements.txt\nflask\n", encoding="utf-8")
    ev = collect_python_dependency_evidence(str(tmp_path))  # must not hang
    assert "flask" in _by_name(ev)


def test_option_lines_are_ignored(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "--index-url https://example.com/simple\n-i https://example.com/simple\nflask\n",
        encoding="utf-8",
    )
    ev = collect_python_dependency_evidence(str(tmp_path))
    assert set(_by_name(ev)) == {"flask"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/depgraph/test_evidence.py -k "requirements or editable or dash_r or option_lines or nested" -q`
Expected: FAIL — dev files read as runtime, `-e`/`-r` dropped, nested files not discovered.

- [ ] **Step 3: Rewrite `_collect_requirements_files` + helpers**

In `src/python_deps/evidence.py`, replace `_collect_requirements_files` (lines ~185-192) with the following, and add the new helpers below it. Leave `_collect_constraints_files`, `_read_requirement_lines`, `_glob_metadata_files` in place (still used elsewhere).

```python
# Editable self-install with extras: ``-e .[http2,socks]`` / ``--editable .[...]``.
_EDITABLE_SELF_EXTRAS_RE = re.compile(r"^(?:-e|--editable)\s+\.\s*\[([^\]]*)\]\s*$")
# Include directives: ``-r other.txt`` / ``--requirement other.txt`` (deps) and
# ``-c other.txt`` / ``--constraint other.txt`` (constraints). Optional ``=``.
_INCLUDE_RE = re.compile(r"^(-r|--requirement|-c|--constraint)\s*=?\s*(\S+)")
_MAX_INCLUDE_DEPTH = 5


def _collect_requirements_files(root: Path, evidence: PythonDependencyEvidence) -> None:
    visited: set[Path] = set()
    for path in _discover_requirements_files(root):
        _ingest_requirements_file(root, path, evidence, visited, depth=0)


def _discover_requirements_files(root: Path) -> list[Path]:
    """Root-level ``requirements*.txt`` plus files in allowlisted nested dirs.

    Bounded: only ``requirements/`` (all ``*.txt``) and ``tests/``, ``test/``,
    ``docs/`` (only ``*requirements*.txt``) — never a full-tree walk.
    """
    found: set[Path] = set(_glob_metadata_files(root, "requirements*.txt"))
    for sub in ("requirements", "tests", "test", "docs"):
        directory = root / sub
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.txt")):
            if not candidate.is_file():
                continue
            if sub == "requirements" or "requirement" in candidate.name.lower():
                found.add(candidate)
    return sorted(found)


def _requirements_role(root: Path, path: Path) -> str:
    """Role for a requirements file from its dir/basename tokens.

    Returns one of ``"docs"``, ``"test"``, ``"dev"``, ``"runtime"`` (checked in
    that precedence). Token/segment matching (not raw substring) keeps false
    positives low.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    dir_segments = {segment.lower() for segment in relative.parts[:-1]}
    stem_tokens = {tok for tok in re.split(r"[-_.]", path.stem.lower()) if tok}
    docs_markers = {"docs", "doc", "documentation"}
    test_markers = {"test", "tests", "testing"}
    if stem_tokens & docs_markers or dir_segments & docs_markers:
        return "docs"
    if stem_tokens & test_markers or dir_segments & test_markers:
        return "test"
    if "dev" in stem_tokens or "dev" in dir_segments:
        return "dev"
    return "runtime"


def _role_kind_source(role: str, root: Path, path: Path) -> tuple[str, str]:
    """Map a requirements-file role to ``(kind, source)`` for its rows."""
    if role == "runtime":
        return "dependency", _relative_source(root, path)
    return "dev_group", f"requirements-file.{role}"


def _ingest_requirements_file(
    root: Path,
    path: Path,
    evidence: PythonDependencyEvidence,
    visited: set[Path],
    depth: int,
) -> None:
    resolved = path.resolve()
    if depth > _MAX_INCLUDE_DEPTH or resolved in visited or not resolved.is_file():
        return
    visited.add(resolved)
    role = _requirements_role(root, resolved)
    kind, source = _role_kind_source(role, root, resolved)
    for raw_line in _iter_raw_requirement_lines(resolved):
        line = _strip_inline_comment(raw_line).strip()
        if not line:
            continue
        editable = _EDITABLE_SELF_EXTRAS_RE.match(line)
        if editable:
            for extra in editable.group(1).split(","):
                normalized = extra.strip().lower()
                if normalized:
                    evidence.used_extras.add(normalized)
            continue
        include = _INCLUDE_RE.match(line)
        if include:
            target = (resolved.parent / include.group(2)).resolve()
            if include.group(1) in ("-c", "--constraint"):
                for constraint_line in _read_requirement_lines(target) if target.is_file() else ():
                    _add_requirement_line(
                        evidence.constraint_dependencies,
                        constraint_line,
                        _relative_source(root, target),
                        kind="constraint",
                    )
            else:
                _ingest_requirements_file(root, target, evidence, visited, depth + 1)
            continue
        if line.startswith("-"):
            # any other option / editable form (``-i``, ``--hash``, bare ``-e .``,
            # ``-e <url>``) — ignored, matching prior behavior.
            continue
        _add_requirement_line(evidence.declared_dependencies, line, source, kind=kind)


def _iter_raw_requirement_lines(path: Path) -> Iterable[str]:
    """Yield every non-empty line (INCLUDING ``-``-prefixed directives)."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="latin-1")
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if stripped:
            yield raw_line
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/depgraph/test_evidence.py -k "requirements or editable or dash_r or option_lines or nested" -q`
Expected: PASS.

- [ ] **Step 5: Run the full evidence + roots + pkg_layer suites (no regressions)**

Run: `python -m pytest tests/depgraph/test_evidence.py tests/depgraph/test_roots.py tests/pkg_layer -q`
Expected: PASS. (If a pre-existing evidence/pkg_layer test asserted requirements-dev/nested behavior that legitimately changed, update it to the new role model — do NOT weaken assertions. None are expected from the current fixtures, which use inline pyproject only.)

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/evidence.py tests/depgraph/test_evidence.py
git commit -m "feat(evidence): role-aware requirements reading — filename role, nested discovery, -e/.[extras] + -r/-c includes"
```

---

## Task 4: Fixed testability-scope policy in `select_roots`

Replace the `needed_extras`-only gate with `_in_test_scope`: runtime always in; feature-extras gated by `in_scope_extras = needed_extras ∪ evidence.used_extras`; dev_groups default-in minus the docs/release denylist. Default construction (`needed_extras=frozenset()`) now yields the testability-scoped closure. Update any construction test whose closure legitimately changes.

**Files:**
- Modify: `src/python_deps/depgraph/roots.py` (add `_in_test_scope`; rewrite the loop body in `select_roots`)
- Test: `tests/depgraph/test_roots.py`
- Modify if needed: `tests/depgraph/test_build.py`, `tests/pkg_layer/*` (only fixtures whose closure legitimately changes)

**Interfaces:**
- Consumes: `_requirement_group`, `_DEV_GROUP_DENYLIST` (Task 1), `evidence.used_extras` (Task 3).
- Produces: `_in_test_scope(req, in_scope_extras: frozenset[str]) -> bool`; `select_roots` unchanged signature, new scope behavior.

- [ ] **Step 1: Write the failing tests**

Append to `tests/depgraph/test_roots.py` (reuse the module's existing `_write`/`scan_to_nodes` helpers):

```python
from python_deps.depgraph.roots import _in_test_scope


def _req(kind, source, name="x"):
    return SimpleNamespace(name=name, specifier="", marker="", extras=(), source=source, kind=kind)


def test_in_test_scope_runtime_always_in():
    assert _in_test_scope(_req("dependency", "pyproject.toml:project.dependencies"), frozenset())


def test_in_test_scope_feature_extra_gated_by_in_scope_extras():
    req = _req("optional_dependency", "pyproject.toml:project.optional-dependencies.http2")
    assert not _in_test_scope(req, frozenset())
    assert _in_test_scope(req, frozenset({"http2"}))


def test_in_test_scope_dev_group_default_in():
    for group in ("test", "tests", "lint", "typing", "dev"):
        req = _req("dev_group", f"pyproject.toml:dependency-groups.{group}")
        assert _in_test_scope(req, frozenset()), group


def test_in_test_scope_dev_group_docs_release_excluded():
    for group in ("docs", "documentation", "release", "publish", "benchmark"):
        req = _req("dev_group", f"pyproject.toml:dependency-groups.{group}")
        assert not _in_test_scope(req, frozenset()), group


def test_in_test_scope_denylist_is_case_insensitive():
    req = _req("dev_group", "requirements-file.DOCS")
    assert not _in_test_scope(req, frozenset())


def test_dependency_groups_test_becomes_root(tmp_path):
    _write(
        tmp_path / "proj",
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["flask"]

        [dependency-groups]
        test = ["pytest"]
        docs = ["sphinx"]
        """,
    )
    repo = tmp_path / "proj"
    graph = scan_to_nodes(str(repo))
    dists = {dist for _imp, dist in select_roots(str(repo), graph)}
    assert "flask" in dists          # runtime
    assert "pytest" in dists         # dev_group test -> in
    assert "sphinx" not in dists     # dev_group docs -> excluded


def test_used_extras_from_editable_puts_extra_in_scope(tmp_path):
    repo = tmp_path / "proj"
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["httpx"]

        [project.optional-dependencies]
        http2 = ["h2"]
        """,
    )
    _write(repo, "requirements.txt", "-e .[http2]\npytest\n")
    graph = scan_to_nodes(str(repo))
    dists = {dist for _imp, dist in select_roots(str(repo), graph)}
    assert "h2" in dists       # optional extra activated by -e .[http2]
    assert "pytest" in dists   # runtime line in requirements.txt


def test_optional_extra_not_signalled_stays_out(tmp_path):
    repo = tmp_path / "proj"
    _write(
        repo,
        "pyproject.toml",
        """
        [project]
        name = "proj"
        version = "0.1.0"
        dependencies = ["httpx"]

        [project.optional-dependencies]
        http2 = ["h2"]
        """,
    )
    graph = scan_to_nodes(str(repo))
    dists = {dist for _imp, dist in select_roots(str(repo), graph)}
    assert "h2" not in dists   # no signal -> feature extra stays gated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/depgraph/test_roots.py -k "in_test_scope or dependency_groups_test or used_extras or optional_extra_not" -q`
Expected: FAIL — `ImportError: cannot import name '_in_test_scope'`; dev groups not yet included.

- [ ] **Step 3: Add `_in_test_scope` and rewrite the `select_roots` loop**

In `src/python_deps/depgraph/roots.py`, add after `_requirement_group`/`_DEV_GROUP_DENYLIST`:

```python
def _in_test_scope(req, in_scope_extras: frozenset[str]) -> bool:
    """Testability-scope membership for a declared requirement (fixed policy).

    Single goal = run the tests, so the closure targets runtime ∪ dev/test groups
    ∪ import-signalled feature extras:

    * ``kind=="dependency"`` (runtime) -> always in.
    * ``kind=="optional_dependency"`` (feature extra) -> in IFF its group is a
      member of ``in_scope_extras`` (``needed_extras`` ∪ the repo's own
      ``-e .[...]`` extras). Extras stay gated so mutually-exclusive groups
      (cpu/gpu, conflicting DB drivers) can't collide the resolve — the bug
      ``needed_extras`` was built to prevent. Extras the tests import but no
      signal names are left to the Phase-A repair loop.
    * ``kind=="dev_group"`` (PEP 735 group / dev|test requirements file) -> in
      UNLESS its group is a docs/release group (``_DEV_GROUP_DENYLIST``):
      default-include dev/test/lint/typing (recall-first, gate-backstopped),
      exclude only docs/packaging bloat.
    * anything else (``constraint`` / unknown) -> not a root.
    """
    kind = getattr(req, "kind", "dependency")
    if kind == "dependency":
        return True
    group = _requirement_group(getattr(req, "source", "")).strip().lower()
    if kind == "optional_dependency":
        return group in in_scope_extras
    if kind == "dev_group":
        return group not in _DEV_GROUP_DENYLIST
    return False
```

Then rewrite the loop in `select_roots` (lines ~274-295). Replace:

```python
    evidence = collect_python_dependency_evidence(repo_path)

    roots: list[tuple[str | None, str]] = []
    seen: set[str] = set()

    # Manifest-declared dependencies are the ONLY roots (highest trust). Imports
    # never generate roots; they are audited post-install, not consulted here.
    for req in evidence.declared_dependencies:
        if getattr(req, "kind", "dependency") == "optional_dependency":
            if _requirement_group(req.source) not in needed_extras:
                continue
        if _env_marker_excludes(req, target_env):
            continue
        normalized = normalize_package_name(req.name)
        if normalized in seen:
            continue
        if _is_non_distribution(req.name):
            continue
        seen.add(normalized)
        roots.append((None, _manifest_root_token(req)))

    return roots
```

with:

```python
    evidence = collect_python_dependency_evidence(repo_path)

    # Feature-extras in scope = caller/CI override ∪ the repo's own ``-e .[...]``
    # self-install signals (evidence.used_extras). Normalized to lowercase to
    # match declared group names (PEP 685 extras compare normalized).
    in_scope_extras = frozenset(
        extra.strip().lower() for extra in needed_extras
    ) | frozenset(extra.strip().lower() for extra in evidence.used_extras)

    roots: list[tuple[str | None, str]] = []
    seen: set[str] = set()

    # Manifest-declared dependencies are the ONLY roots (highest trust). Imports
    # never generate roots; they are audited post-install, not consulted here.
    # Testability scope: runtime + dev/test groups (minus docs/release) + signalled
    # feature extras — see _in_test_scope.
    for req in evidence.declared_dependencies:
        if not _in_test_scope(req, in_scope_extras):
            continue
        if _env_marker_excludes(req, target_env):
            continue
        normalized = normalize_package_name(req.name)
        if normalized in seen:
            continue
        if _is_non_distribution(req.name):
            continue
        seen.add(normalized)
        roots.append((None, _manifest_root_token(req)))

    return roots
```

Update the `select_roots` docstring paragraph about `needed_extras` to note the new fixed testability scope (dev-groups default-included; `in_scope_extras = needed_extras ∪ evidence.used_extras`); keep it factual, no flags.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/depgraph/test_roots.py -q`
Expected: PASS (new + all pre-existing, including `test_only_needed_extra_group_becomes_a_root` and `test_no_needed_extras_default_excludes_all_optional_groups`, which are unaffected — feature-extras stay gated).

- [ ] **Step 5: Run the construction + pkg_layer suites; update legitimately-changed fixtures**

Run: `python -m pytest tests/depgraph tests/pkg_layer -q`
Expected: PASS. If a construction fixture that declares a `[dependency-groups]` table or a dev/test requirements file now yields a larger PACKAGE closure (intended), update that test's expected set to include the dev/test deps — do NOT weaken the assertion or add a flag to suppress the scope. Enumerate each changed test in the task report with the reason. `tests/depgraph/test_build.py::test_build_dep_graph_default_needed_extras_is_runtime_only` spies on the `needed_extras` value passed to `select_roots` (still `frozenset()`), so it keeps passing; if its NAME/docstring now misleads (scope is no longer runtime-only), adjust the wording without changing what it asserts.

- [ ] **Step 6: Commit**

```bash
git add src/python_deps/depgraph/roots.py tests/depgraph/test_roots.py
# add any fixture files that were legitimately updated
git commit -m "feat(roots): fixed testability-scope root selection — dev_groups default-in, extras gated by needed_extras ∪ used_extras"
```

---

## Task 5: Validation — re-run the package-layer eval + A/B (controller-run)

> **Not a TDD subagent task.** The controller runs this after Tasks 1-4 pass review. It measures the recall/precision lift against the persisted agent-configured oracle and confirms the A/B verdict is unaffected. No production code changes here (only regenerated eval artifacts, which are gitignored).

**Inputs (already on disk):**
- Ground-truth oracle (unchanged): `outputs/graph_fidelity/pkg_lock_ab/oracle/*.json` (15 repos).
- Pre-fix baseline (our side at HEAD 8d7fbbf): `outputs/graph_fidelity/pkg_lock_ab/ours/*.json`.
- Smoke corpus clones: `outputs/graph_fidelity/_smoke/<repo>/`.
- Runners: `/Users/john/.claude/jobs/366037cb/tmp/run_ours_pkg.py`, `outputs/graph_fidelity/pkg_lock_ab/compare_pkg.py`.

- [ ] **Step 1: Re-run OUR side with the new reader** (network `uv lock`; construction-only, no build-phase agent)

```bash
cd /Users/john/john-planner-v3-core-autoresearch
python /Users/john/.claude/jobs/366037cb/tmp/run_ours_pkg.py \
  "flask,requests,rich,httpx,pytest,sqlalchemy,anyio,marshmallow,scrapy,typer,slither,mvt,python-semantic-release,postgres-mcp,vizro" \
  /Users/john/.claude/jobs/366037cb/tmp/ours_v2
```

- [ ] **Step 2: Compare new-ours vs the persisted oracle**

```bash
python outputs/graph_fidelity/pkg_lock_ab/compare_pkg.py \
  /Users/john/.claude/jobs/366037cb/tmp/ours_v2 \
  outputs/graph_fidelity/pkg_lock_ab/oracle \
  | tee /Users/john/.claude/jobs/366037cb/tmp/compare_v2.txt
```

- [ ] **Step 3: Also compute the pre-fix baseline the same way (apples-to-apples), then diff**

```bash
python outputs/graph_fidelity/pkg_lock_ab/compare_pkg.py \
  outputs/graph_fidelity/pkg_lock_ab/ours \
  outputs/graph_fidelity/pkg_lock_ab/oracle \
  | tee /Users/john/.claude/jobs/366037cb/tmp/compare_baseline.txt
```

Record per-repo pooled recall/precision before vs after (exclude vizro from the headline — monorepo scope mismatch). **Expected direction:**
- **Recall ↑ (the primary, high-confidence win):** PEP 735 test groups now captured for flask/slither/typer/anyio/sqlalchemy/mvt; `-e .[http2,socks]` recovers httpx's `h2`/`hpack`/`hyperframe`/`socksio` MISS; `requirements-test.txt` / `requirements/test.txt` now read.
- **Precision — honest expectation, may be mixed:** separately-named docs files (`docs/requirements*.txt`, `requirements-docs*.txt`) and PEP 735 docs/release groups now excluded (win); BUT testability scope deliberately default-includes dev/lint/typing groups (mypy, ruff, tox) that the oracle's *minimal* collect-only env omits (recall-first tradeoff → possible precision dip). Docs tooling co-located inside a single mixed dev requirements file (httpx's root `requirements.txt` sections mkdocs+pytest by comment, not by group) is a KNOWN residual this design does not separate. Report the actual number; do not force a precision-up narrative.

- [ ] **Step 4: Confirm the A/B verdict is unaffected** (closure sizes shift; verdict must not)

```bash
python -m pytest tests/eval -q
# and re-run the two A/B mains if present, confirming 30/0/30/0 verdict=verifier
```

If A/B baselines are size-sensitive and legitimately shift, regenerate them and confirm the verifier-vs-generator verdict is unchanged (the dev-groups change is orthogonal to the imports-as-roots divergence the A/B measures). Record the outcome.

- [ ] **Step 5: Persist artifacts + record the lift**

Copy `ours_v2/*.json` and `compare_v2.txt` into `outputs/graph_fidelity/pkg_lock_ab/ours_v2/` (gitignored — nothing to stage). Append a short before/after table (pooled recall/precision + the per-repo deltas for the PEP 735 repos and httpx) to `CHANGELOG-planner-v3-e2e-loop.md` and update the `pkg-layer-vs-agent-freeze-eval` memory with the post-fix numbers. Do NOT claim any Docker/e2e/build-phase result — this is construction-only package-layer fidelity.

---

## Self-Review

**Spec coverage:**
- Role model / `dev_group` kind + `_requirement_group` extension → Task 1. ✅
- Change 1 (PEP 735 `_collect_dependency_groups` + include-group + cycle) → Task 2. ✅
- Change 2 (requirements role by filename + nested discovery + `-e .[extras]`→`used_extras` + `-r`/`-c` includes) → Task 3. ✅
- Change 3 (`_in_test_scope` fixed policy + `in_scope_extras = needed_extras ∪ used_extras`) → Task 4. ✅
- Open decision (dev/all) → resolved default-include in Global Constraints. ✅
- Test plan (RED-first per change) → each task Step 1-2. ✅
- Validation (re-run eval, measure lift, A/B unaffected) → Task 5. ✅
- Risks (default-scope behavior change, A/B regeneration, resolve-conflict safety, immutability) → Global Constraints + Task 4 Step 5 + Task 5 Step 4. ✅

**Type consistency:** `_in_test_scope(req, in_scope_extras: frozenset[str]) -> bool`, `_requirement_group(source) -> str`, `_resolve_dependency_group(name, groups, seen) -> tuple[list[str], bool]`, `_requirements_role(root, path) -> str`, `_role_kind_source(role, root, path) -> tuple[str, str]`, `used_extras: set[str]` — all consistent across tasks. `source` sub-label shapes (`dependency-groups.<g>`, `requirements-file.<role>`) match the regex in Task 1 and are produced in Tasks 2-3 and consumed in Task 4.

**Placeholder scan:** none — every code step carries complete code.

**Honest caveat baked in:** Task 5 Step 3 states the precision expectation truthfully (mixed; recall-first tradeoff; httpx mixed-file residual) rather than asserting a guaranteed precision jump.
