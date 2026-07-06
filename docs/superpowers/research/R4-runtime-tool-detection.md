# R4 — Runtime-tool detection (library-wraps-a-binary) is unreliable

Status: research/design only. No source files modified.

## 1. The finding, precisely re-verified

Three repos, three DIFFERENT verified root causes — not one bug with three symptoms.
All evidence below was reproduced from the actual cloned repos at
`/Users/john/john-v3-multi-lang/outputs/build_script_eval/_smoke/{cryptography,pyzmq,python-semantic-release}`
(same checkouts `sweep.py` used) plus a clean-venv repro of the GitPython failure.

### 1a. `python-semantic-release` — MISS (predicted `[]`, needs `git`)

- `pyproject.toml:27` declares `"gitpython ~= 3.0"` as a real runtime dependency.
- `semantic_release/gitproject.py` does `import git` (GitPython's `git.Repo` API), never a literal
  `subprocess.run(["git", ...])`. GitPython itself shells out to the `git` binary **inside its own
  package source** (`site-packages/git/cmd.py`), using `self._git_exec_name` — a **variable**, not a
  string literal, and it lives in a **third-party dependency's** source tree, not the target repo's.
- `subprocess_scan.py` (`scan_subprocess_tools`, `src/python_deps/depgraph/subprocess_scan.py:95-117`)
  only walks `repo_path` — the repo under test. It structurally cannot see a subprocess call buried in
  an installed dependency, and even if it could, `_program_from_call` (line 45) only resolves **string
  literal** first-args (`_const_str`), so a variable argv would still yield `None`.
- I reproduced GitPython's actual failure mode in a clean venv with `git` stripped from `PATH`:
  ```
  ImportError: Bad git executable.
  The git executable must be specified in one of the following ways:
      - be included in your $PATH
      - be set via $GIT_PYTHON_GIT_EXECUTABLE
  ...
  This initial message can be silenced ... by setting the $GIT_PYTHON_REFRESH environment variable.
  ```
  This is raised **at `import git` time**, not lazily — so if `import_probe`
  (`src/python_deps/depgraph/probe.py:219-275`) ever ran `python -c "import git"` against this repo, it
  WOULD fail. But `extract_needs`'s `BINARY_RES` table
  (`src/python_deps/depgraph/failure_signatures.py:29-39`) has no pattern matching "Bad git
  executable." — none of the 11 regexes fire on this text shape (they're all compiler/autoconf/meson/
  cmake/shell "not found" phrasings). Result: even the **reactive** probe path would currently
  degrade this to the honest-flag path (`flag_runtime_import_failure`), never mint a `git` Tool node.
- A fourth, independent gap: `import_mapping.CURATED_IMPORT_TO_PACKAGE`
  (`src/python_deps/import_mapping.py:7-23`) has no `"git": "gitpython"` entry, so even a static
  Import node named `git` would resolve **unresolved** via `map_import_to_package` (import name `git`
  normalizes to `git`, declared dependency `gitpython` normalizes to `gitpython` — no match). This
  path is also largely moot: `naming.py`'s own docstring says `package_roots` (the import→package
  generator) is "OFF the construction path" (P0.1), and `build.py:560` notes the "provisional Stage 3a
  Import->Package heuristic is retired" in favor of a post-install certified relink (`relink.py`,
  `build.py:611`) that only runs when a real container executor installs the closure — not in the
  cheap `build_graph_construction_only` path `sweep.py`/DIAGNOSTIC.md used.

  **Net: all three of the codebase's current mechanisms for surfacing this need (static subprocess
  scan, static import-name mapping, reactive probe pattern-matching) miss it, each for a distinct,
  verified reason.**

### 1b. `cryptography` — FALSE POSITIVE (predicted `git`, needs nothing at runtime)

`release.py` (repo root, not under any excluded directory):
```python
def run(*args: str) -> None:            # release.py:24 — a LOCAL wrapper, name collides with subprocess.run
    print(f"[running] {list(args)}")
    subprocess.check_call(list(args))

...
run("git", "tag", "-s", version, "-m", f"{version} release")     # release.py:49
run("git", "push", "--tags", "git@github.com:pyca/cryptography.git")  # release.py:50
```
`_program_from_call` (`subprocess_scan.py:45-75`) matches purely on **function name** (`func.attr` or
`func.id`) against `_ARGV_FUNCS = {"run","Popen","call","check_call","check_output"}` — it never
checks that the callable actually resolves to `subprocess.*`. A locally-defined `def run(*args)` name-
collides with `subprocess.run`, and its call site `run("git", "tag", ...)` has a literal string first
arg (`"git"`), so it matches the allowlist and mints a false `tool:git` node.

Note this is a maintainer-only release-automation script (`release.py`, invoked manually via `click`,
declared as `# /// script` PEP 723 inline deps, and never imported by `cryptography`'s package source
or its test suite) — it is never reached at install or test time regardless of the name-collision bug.

### 1c. `pyzmq` — FALSE POSITIVE (predicted `git`, needs nothing at runtime)

`tools/test_sdist.py:33`:
```python
from subprocess import run
...
p = run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)   # a REAL, correctly-resolved subprocess.run call
```
This one is a **correct** AST match (real `subprocess.run`, real literal `"git"` argv[0]) — the bug is
**scope**, not name resolution. `pyzmq/pytest.ini` sets:
```
testpaths = tests
```
so `tools/test_sdist.py` (a maintainer sdist-content verification script, requires a pre-built
`dist/*.tar.gz` and a git checkout) is **never collected** by a normal `pytest` run. Yet
`subprocess_scan.py` walks the whole repo tree using `_is_excluded` from
`config_scan.py:15-22`:
```python
_EXCLUDED_SEGMENTS = {"examples","example","docs","doc","build","dist","samples","sample",
                       "benchmarks","benchmark","bench","scripts","script",".github",".tox",
                       "node_modules","site-packages",".venv","venv",".git",".hg",".svn",
                       "__pycache__",".mypy_cache",".pytest_cache"}
```
**`"tools"` is missing from this list.** Compare `scan.py:44-49` (the *import* scanner, Stage 1),
which has its own, independently-maintained exclusion set:
```python
_EXCLUDED_SEGMENTS = {"examples","example","docs","doc","build","dist","samples","sample",
                       "benchmarks","benchmark","bench","scripts","script",".github",".tox",
                       "node_modules","site-packages",".venv","venv","tools"}
```
`scan.py` DOES exclude `"tools"`. Two scanners maintain two independently-drifted exclusion tables —
this is exactly the kind of bug that recurs every time a new static scanner is added by copy-paste.

## 2. Design: three complementary tiers + two precision guards

### Tier 1 (primary fix) — curated library→required-binary table, keyed on the RESOLVED package identity

Add a new curated table, same shape and same file as the two tables `tables.py` already owns
(`CLI_TOOL_TO_APT`, `NATIVE_RISK_PACKAGES`):

```python
# tables.py — new table, same curation discipline as CLI_TOOL_TO_APT
LIBRARY_REQUIRES_BINARY: dict[str, str] = {
    "gitpython": "git",
    "pydot": "dot",          # graphviz
    "pypandoc": "pandoc",
    "ffmpeg-python": "ffmpeg",
    "pydub": "ffmpeg",
    # deliberately NOT here: pygit2 (bundles libgit2 as a *.so*, not a binary — SystemLib
    # class, not Tool), sh/plumbum/pexpect (wrap an ARBITRARY/dynamic program, not one
    # fixed binary — out of scope by the same "unambiguous only" rule CLI_TOOL_TO_APT uses)
}
```
keys normalized via the existing `import_mapping.normalize_package_name`. **Resolve the binary through
the existing `CLI_TOOL_TO_APT` / `apt_for_cli_tool`** (`tables.py:27-64`) rather than duplicating an
apt name — `LIBRARY_REQUIRES_BINARY["gitpython"] == "git"` and `apt_for_cli_tool("git") == "git"` are
two separate, composable facts (library→tool, tool→package), so `git`'s apt mapping is defined exactly
once in the whole codebase.

New pure stage, symmetric to `subprocess_scan.py` (no Executor, walks already-resolved graph state,
returns a new `DepGraph`):
```python
def add_library_tool_nodes(graph: DepGraph) -> DepGraph:
    """For every resolved PACKAGE node whose normalized name is a known
    tool-wrapping library (LIBRARY_REQUIRES_BINARY), mint a Tool node + requires
    edge from that Package — mirrors add_subprocess_tool_nodes's node shape."""
```
Wire it in `build.py` right next to the existing call (`build.py:568`,
`graph = add_subprocess_tool_nodes(graph, repo_path)`) — at that point in the pipeline Phase-A's
resolve/install fixpoint has already converged, so every declared+transitive Package node (including
`gitpython`, from `pyproject.toml:27`) already exists in `graph.nodes`. **Keying on `Package.name`
rather than on the Import graph is the load-bearing design choice**: it sidesteps the entire
import→package linking machinery I found to be fragile/partially-retired for this exact case
(`CURATED_IMPORT_TO_PACKAGE` missing `git`→`gitpython`; `package_roots` off the construction path;
certified relink needing a real container install) and instead uses the single most trustworthy
signal already in the graph — a manifest-declared, resolver-confirmed dependency edge.

**Why this generalizes across DIAGNOSTIC.md's repo-type space:** any repo-type stratum (pure-Python,
C-ext, sdist-only, monorepo, any build backend) that declares one of these libraries as a real
dependency gets the Tool node; a repo that doesn't, never does. It is orthogonal to build-mode
(`build_from_source`), to native-risk classification, and to resolution completeness (R2) — it only
needs the Package node to exist, which is the *first*, most stable thing Phase-A produces.

**Precision/recall:** Recall is bounded by curation coverage (a library not yet in the table is still
a miss — same shape/limitation as `CLI_TOOL_TO_APT` today, an accepted tradeoff already made in this
codebase). Precision is structurally ~100% for the false-positive class in this diagnostic: cryptography
and pyzmq never resolve `gitpython` as a Package (they don't declare it), so keying on Package identity
cannot produce the git false positive no matter what their source trees mention.

### Tier 2 — import-reachability as a secondary/complementary signal (not primary)

The prompt's suggested "a REACHED import of a known tool-wrapping lib ⇒ its binary" is a real
alternative axis, useful for the case Tier 1 can't cover: a library that shells to a binary only when
one of its **optional** submodules is actually used (dependency present, tool not always needed) — e.g.
a plotting library that only calls `dot` when its optional graphviz-backend submodule is imported. I
verified this is NOT semantic-release's situation (gitpython is unconditionally imported by core
`gitproject.py`), so it is not required for the three repos in scope, but it's worth flagging as a
future refinement: gate Tier 1 further by requiring the specific submodule import (`git.repo.base`,
not just any `gitpython`-family import) to actually appear as a reached `Import` node from `scan.py`
(which already has the correct `"tools"`-exclusion scope, unlike `config_scan.py`). This makes Tier 1
strictly conditional rather than unconditional-on-dependency, at the cost of depending on the same
import→package linkage that is currently fragile — recommend deferring this refinement until a repo
in the corpus actually demonstrates the "declared but binary not always needed" case (none does today).

### Tier 3 — reactive runtime-probe confirmation (recall backstop, not primary)

Independent of Tier 1/2, extend `BINARY_RES` in `failure_signatures.py:29-39` with library-specific
ImportError shapes as a safety net for tool-wrapping libraries NOT YET in `LIBRARY_REQUIRES_BINARY`:
```python
re.compile(r"ImportError: Bad ([A-Za-z0-9_][\w.+-]*) executable\."),   # GitPython's own phrasing
```
This only helps when `import_probe` (`probe.py:219`) actually runs `python -c "import git"` against a
**real container** — i.e. only in the full (non-construction-only) pipeline, after `install_closure`
has already installed `gitpython` (Phase-A, `build.py:383`). It is a correct, cheap, low-risk addition
(one more anchored regex, same discrimination discipline the module documents: "an ANCHORED failure
signature ... never scanned from a table") but it is fundamentally reactive — it only fires AFTER a
build attempt has already run without `git` installed, i.e. it cannot fix the **predicted apt = []**
problem DIAGNOSTIC.md measures at construction time (no replay). Recommend it as a defense-in-depth
addition alongside Tier 1, not a replacement: Tier 1 is what makes `setup.sh` correct on the FIRST
pass; Tier 3 only catches a library not yet curated, and only in a run that has an Executor at all.
One more caveat if Tier 3 is added: it would also need a bare-`FileNotFoundError`/`OSError` pattern
for tool-wrapping libraries with lazier, non-import-time binary checks (unlike GitPython, most wrappers
fail only when the wrapped call actually happens) — this generalizes far worse than Tier 1 because
every wrapper library invents its own error phrasing; that asymmetry is itself the strongest argument
for curation (Tier 1) over pattern-matching as the primary mechanism for this need-class.

### Precision guards on `subprocess_scan.py` itself (orthogonal, but same root class — fixes 1b/1c directly)

1. **Unify the exclusion-segment table.** `config_scan.py:15-22` (used by `subprocess_scan.py` via
   `_is_excluded`) and `scan.py:44-49` maintain two independently-drifted copies; `scan.py`'s has
   `"tools"`, `config_scan.py`'s doesn't — this is exactly the pyzmq false positive. Promote ONE
   shared `_EXCLUDED_SEGMENTS` constant (superset-merge the two: add `"tools"` to the config_scan/
   subprocess_scan set, or extract both into a single shared module e.g. `scan_scope.py` that all
   three scanners import). This is a one-line, zero-risk fix that also prevents the same class of bug
   recurring in any FUTURE static scanner added by copying `subprocess_scan.py`'s pattern.
2. **Verify the call target actually resolves to `subprocess`/`os`/`shutil`, not just a matching
   name.** In `_program_from_call` (`subprocess_scan.py:45-75`), before matching `fname` against
   `_ARGV_FUNCS`/`_STRING_FUNCS`, track per-file import bindings (`import subprocess`,
   `import subprocess as sp`, `from subprocess import run`, `import os`, `from shutil import which`)
   in a lightweight pre-pass over the module's `Import`/`ImportFrom` nodes, and require the call's
   `ast.Attribute.value` (for `subprocess.run(...)`) or the call's `ast.Name` binding (for
   `from subprocess import run; run(...)`) to trace back to one of those bindings. A locally-defined
   `def run(*args)` (cryptography's `release.py:24`) is bound to nothing subprocess-related and would
   no longer match. I confirmed this fix has no unintended side effect on `release.py`'s *actual*
   `subprocess.check_call(list(args))` call (line 28): its first arg is `list(args)` — a dynamic
   `ast.Call`, not a literal — so `_const_str` already declines to resolve it; the false positive was
   purely the name-collision at the `run("git", ...)` call sites, never the real subprocess call.

Both guards are precision-only (never remove a currently-TRUE positive elsewhere in the 16-repo
diagnostic, since neither typer/httpx/click/etc. relies on a bare-name-collision or a `tools/`-scoped
subprocess call for its `apt_names_in_graph` result — verified: none of the other 13 repos' verdicts
in DIAGNOSTIC.md mention a Tool/subprocess-scan-attributed apt package at all).

## 3. Ecosystem-agnostic seam vs Python-specific

The **mechanism** in all three tiers is ecosystem-agnostic and belongs at the shared/seam layer:
"look up a resolved dependency's canonical name in a curated table; if found, mint a Tool node with a
`requires` edge" is identical machinery to what `CLI_TOOL_TO_APT`/`add_subprocess_tool_nodes` already
do, and to what an `EcosystemProvider` for Node/Go would need too (`simple-git`/`nodegit` on npm →
`git`; Go's `go-git` → **nothing**, since it's a pure-Go re-implementation with no external binary —
itself a useful contrast: the table is NOT "any git wrapper", it's "wraps the actual git BINARY").
The **data** (`LIBRARY_REQUIRES_BINARY`'s contents) is inescapably per-ecosystem (PyPI names vs npm
names vs Go module paths), exactly like `CURATED_IMPORT_TO_PACKAGE` and `CLI_TOOL_TO_APT` already are.
Recommend: keep the table Python-specific in `tables.py` for now (consistent with every other curated
table in this file), but name the new stage function and its call-site comment generically enough
(`add_library_tool_nodes`, "library-wraps-a-binary" in the docstring, not "git-specific") that porting
it to a Node/Go provider later is a data-table swap, not a redesign — same seam discipline R5 asks for.

## 4. Eval verification

`sweep.py`'s existing harness (`_emitted_apt`, `apt_names_in_graph` from
`src/eval/language_package_eval/coverage.py:108`) already gives the exact before/after signal needed,
with no new eval machinery required:

- **semantic-release**: today `predicted_apt == []`. After Tier 1, `apt_names_in_graph(g)` must include
  `"git"`, and `_emitted_apt` (which asserts `emit == pred`) must also include it — i.e. `setup.sh`
  actually runs `apt-get install git`. The stronger check is a **full replay** (`replay.py`'s
  install → env_works → tests_ran ladder,`src/eval/build_script_eval/replay.py`): DIAGNOSTIC.md's
  earlier 5-repo replay already recorded semantic-release failing at `env_works` (git missing breaks
  collection, `GIT_PYTHON_REFRESH`/`Bad git executable`); after the fix, `env_works` must flip to
  `True` for this repo specifically (not just apt-list equality — this is the eval's actual guardrail
  against "graph looks right but container still breaks").
- **cryptography / pyzmq**: today `predicted_apt == ["git"]` (false). After the precision guards,
  `apt_names_in_graph(g)` must be `[]` for both (no `LIBRARY_REQUIRES_BINARY` entry matches either
  closure, and the two `subprocess_scan.py` guards remove `release.py`'s and `tools/test_sdist.py`'s
  false triggers respectively). Since both already installed cleanly as wheels before (DIAGNOSTIC.md:
  "all wheels, no build"), no replay regression is possible — the fix is apt-list-only.
- **Regression-lock**: `cryptography` and `pyzmq` are not currently in the committed
  `src/eval/build_script_eval/corpus.py` (only `typer`/`semantic-release`/`psycopg2`/`pygraphviz`/
  `lxml` are) — they exist only in the ad-hoc `sweep.py` manifest used for this diagnostic. Add all
  three (`semantic-release` is already present) as permanent `RepoSpec` rows so this exact bug class
  (false-positive-via-name-collision, false-positive-via-scan-scope, miss-via-transitive-binary) is a
  standing regression gate, not a one-off diagnostic — directly serving R5's "eval as continuous
  guardrail" goal.

## 5. Summary of what fixes what

| Fix | Repos fixed | Class |
|---|---|---|
| Tier 1: `LIBRARY_REQUIRES_BINARY` + `add_library_tool_nodes` | python-semantic-release (miss → correct) | transitive-binary-via-declared-dependency |
| Guard 1: unify exclusion-segment table (add `"tools"`) | pyzmq (false positive → clean) | scan-scope inconsistency across scanners |
| Guard 2: call-target resolution (not name-only match) | cryptography (false positive → clean) | name-collision with a local wrapper function |
| Tier 3: extend `BINARY_RES` (optional, defense-in-depth) | any *future* uncurated tool-wrapping library, reactively, only when an Executor runs | recall safety net, not a construction-time fix |
