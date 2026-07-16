# A2/B4 + C1 — Implementation Plan

> Follows `docs/superpowers/handoffs/2026-07-13-graph-construction-fixes.md` §3 (A2/B4) and §4 (C1).
> Codex (`gpt-5.6-terra`) review after each task. Tests: `python3 -m pytest tests/depgraph/ tests/test_evidence.py -q` (never `pytest tests/` — conftest shadowing, see handoff §8.6).

**Goal:** Archipelago (0 pip packages today) and posthog (0 packages, 77,642 gold tests) both produce a working environment, without letting newly-discovered files poison the resolve closure.

---

## Part 1 — A2/B4: split *discovery* from *role*

Landed code makes every discovered requirements file a hard root in one global `uv lock`. Gold succeeded on Archipelago with `for f in worlds/*/requirements.txt; do pip install -r "$f" || true; done` — **independent, best-effort, non-blocking**. One unified lock over 84 worlds' pins is a harder problem than the one gold solved, and it's the wrong problem.

### Task 1 — classify discovered files HARD vs SOFT at discovery time

**Files:** `src/python_deps/evidence.py` (~:404-486), `src/python_deps/models.py:85`

**The invariant that makes this safe:**
> **HARD = exactly the set the pre-B4 allowlist would have found.** (root-level `*.txt` matching A2's substring rule, plus files under a top-level `requirements/`, `tests/`, `test/`, `docs/`.) **SOFT = everything else the new walk discovers.**
>
> So B4 **cannot change any existing root**. A2's widened filename rule still promotes `ci-requirements.txt` to a hard root (it's root-level). Everything the recursive walk newly finds is soft, by construction.

- Add `_is_hard_requirements_file(root, path) -> bool` encoding the above.
- `PythonDependencyEvidence`: new field `soft_requirements_files: list[str] = field(default_factory=list)` (repo-relative paths, sorted).
- `_collect_requirements_files`: HARD → `_ingest_requirements_file` as today. SOFT → append relpath, **do not ingest into `declared_dependencies`**.

**Tests** (`tests/test_evidence.py`):
- `ci-requirements.txt` at root → in `declared_dependencies` (A2 preserved).
- `worlds/aquaria/requirements.txt` → in `soft_requirements_files`, **not** in `declared_dependencies`.
- `tests/fixtures/upstream/requirements.txt` pinning `requests==2.0.0` → soft; `requests` is **not** a declared root. *(This is the poisoning case; today it silently downgrades the closure with no conflict and no ladder.)*
- `docs/requirements.txt` → still hard (no regression on the existing tested target).

### Task 2 — cap becomes order-independent

**Files:** `src/python_deps/evidence.py:401,444-486`

Today `_discover_requirements_files` early-`return`s mid-walk on hitting 500, so a lexically-early vendored tree can eat the budget before the walk reaches `worlds/`. Fix: walk fully (dir walk, no file reads — cheap), then if over cap sort by `(depth, path)` and keep the **shallowest** N; record truncation in `collection_errors` as today.

**Do NOT extend `_REQUIREMENTS_SKIP_DIRS`.** With the hard/soft split, junk under `vendor/`/`fixtures/`/`examples/` can only ever be a *soft, constrained, non-fatal* install — harmless. Adding names is a blocklist arms race that never terminates. Skip set stays as-is.

**Test:** 600 qualifying files across a deep tree → the shallow ones survive, truncation recorded.

### Task 3 — renderer emits the soft-install block

**Files:** `src/python_deps/depgraph/build_script.py`, caller of `render_build_script`

Emitted **after** the pinned closure install, **before** the `pip install -e .` capstone:

```sh
# closure constraints — a soft file may ADD packages, never MOVE a pinned one
cat > /tmp/closure.txt <<'EOF'
<name>==<version>   # every pinned Package node in the graph
EOF
pip install -r worlds/aquaria/requirements.txt -c /tmp/closure.txt || true
...
```

The `-c` is what makes this sound: a soft file can introduce **new** packages but can never downgrade one the closure already pinned. Reuse B5's `_non_fatal_block()` for the `|| true` semantics.

**Plumbing — one open decision, resolve at step 0:** `render_build_script(graph, manual_blocks, *, include_services)` is pure over the graph, so soft files must reach it via `manual_blocks` (caller already has `repo_path`/evidence) or via graph carriage. **Check the caller first** — if it has evidence in hand, `manual_blocks` is the smaller diff and needs no new node kind.

---

## Part 2 — C1: carry the source table through

**Decision taken:** carry `[tool.uv.sources]` into the synthetic pyproject. **`uv.lock`-as-pin-oracle is deferred** — revisit once sources-through is measured on posthog. (Rationale: the Packages tier stays *derived*, not copied.)

### Task 4 — capture the source spec, stop dropping

**Files:** `src/python_deps/evidence.py:180-291`, `tests/test_evidence.py:171`

- Replace `_uv_non_pypi_source_names() -> frozenset[str]` with `uv_source_config(root)` returning the **full specs**: `{canon_name: spec}` from `[tool.uv.sources]`, plus `[tool.uv.workspace].members` and `[[tool.uv.index]]`.
- **Fix the `index =` hole.** `foo = {index = "internal"}` is *not* PyPI either. Today it falls through the filter and `uv lock` resolves the **public** `foo` — wrong package, no error. It must be carried (with its `[[tool.uv.index]]` block) or degraded (Task 6). **`test_evidence.py:171` asserts the wrong behaviour — rewrite it.**
- Keep `UV_NON_PYPI_SOURCE_KIND` only for genuinely-unresolvable sources (Task 6). Stop dropping git/workspace/path/url.
- **Keep** `build.py:561`'s `kind != "uv_non_pypi_source"` filter into the repair ladder (handoff §4, codex HIGH — sentinels re-entering as bare PyPI roots).

### Task 5 — emit the source table into the synthetic pyproject

**Files:** `src/python_deps/depgraph/resolve.py:150` (`_write_pyproject`), `_project_dir`, `resolve_closure` signature; `build.py` threading

🔴 **The real wrinkle.** The synthetic project (`name = "depgraph-resolve-root"`) is written to a **temp dir**. `workspace = true` and relative `path = "../x"` resolve against the *workspace root* — from a temp dir they resolve to nothing.

**Fix: rewrite, don't relocate.** Keep temp-dir isolation and normalize the specs on the way out:
- `hogli = {workspace = true}` → `hogli = {path = "<abs-repo>/tools/hogli", editable = true}` (member dir found via `[tool.uv.workspace].members` globs).
- `foo = {path = "../foo"}` → absolute path.
- `git`/`url` → carried verbatim (self-contained already).
- `index` → carried, with its `[[tool.uv.index]]` block.

`resolve_closure` gains `uv_sources` / `uv_indexes` / `repo_path`; `build.py` passes them.

**Tests:** synthetic pyproject contains an absolute-path source for a workspace member; a relative `path` is absolutized; `git`/`url` verbatim; an `index` dep carries its index block.

### Task 6 — degrade, don't silently drop

**Files:** `resolve.py`, graph emission

If a source genuinely cannot be honored (private index, no credentials; git host unreachable), emit a **`missing` Package node with evidence** and mark the environment **degraded** — it must not score as a clean success.

> **The rule: a dropped dependency poisons the certificate, not the build.** Dropping `pytest-split` (a *git-sourced test dep*) today yields an env that locks, installs, goes green, and then cannot collect — `--no-deps` project install (`populate.py:60`) recovers nothing. That is the same failure class as the self-install false-green.

---

## Order & verification

1. Task 1 → 2 → 3 (A2/B4), codex after each.
2. Task 4 → 5 → 6 (C1), codex after each.
3. **Measure:** re-run the tier-2 harness (v3's setup script in the base image it chose) on **Archipelago** and **posthog**; count collected tests + collect errors against gold. Those two repos are the whole point.

**Non-goals:** `uv.lock` replay (deferred); extending the skip-dir blocklist (Task 2); B1/B3/B6 (separate, see handoff).
