# R4 — Toggle and Wiring Spec

**Date:** 2026-06-08  
**Scope:** Exact minimal diffs to wire `_repair_and_rescore` into `run_rat_benchmark.py` and toggle
the agent self-verify OFF via `multi_docker_eval_adapter.py`. Read-only — no source files are
modified by this document.

---

## 1. Argparse additions — `run_rat_benchmark.py`

**Source of truth for the existing block:** `run_rat_benchmark.py:667-712`

The existing argparse block ends with the `--model` argument at line 709-711 and `args = parser.parse_args()` at line 713. The two new arguments must be inserted immediately before `args = parser.parse_args()` at line 713.

```
# file: run_rat_benchmark.py
# BEFORE (lines 709-713):
    parser.add_argument("--model", choices=["dockeragent", "rat", "repo2run"],
                        default="dockeragent",
                        help="Which eval model to use (default: dockeragent).")

    args = parser.parse_args()

# AFTER:
    parser.add_argument("--model", choices=["dockeragent", "rat", "repo2run"],
                        default="dockeragent",
                        help="Which eval model to use (default: dockeragent).")

    # Repair-loop controls
    parser.add_argument("--repair-mode",
                        choices=["runner", "selfverify", "both", "off"],
                        default="selfverify",
                        help=(
                            "Repair strategy. "
                            "'runner': runner-side verbatim Repo2Run loop ON, agent self-verify OFF. "
                            "'selfverify': agent self-verify ON, runner loop OFF (current default). "
                            "'both': both ON (debug-compare only). "
                            "'off': both OFF (clean baseline). "
                            "Default: selfverify."
                        ))
    parser.add_argument("--repair-rounds", type=int, default=2,
                        help=(
                            "Maximum LLM Dockerfile repair rounds for the runner-side loop. "
                            "0 disables LLM repair (deterministic only). Default: 2."
                        ))

    args = parser.parse_args()
```

**Insertion point:** `run_rat_benchmark.py` line 712 (after the `--model` add_argument call closes,
before `args = parser.parse_args()`).

---

## 2. `_repair_and_rescore` gate inside `_run_one`

**Injection point** (per plan §1 and audit H1): after `model.predict()` returns at line 190 and before the scorer block at lines 207-209.

The function `_run_one` signature today (`run_rat_benchmark.py:146-151`):
```python
def _run_one(
    full_name: str,
    model: "DockerAgentModel",
    root_path: str,
    category: str,
) -> dict:
```

The signature must gain two new parameters with defaults so all existing callers (`worker_main:611`,
`sequential_main:630`) remain valid without change:

```
# BEFORE (run_rat_benchmark.py:146-151):
def _run_one(
    full_name: str,
    model: "DockerAgentModel",
    root_path: str,
    category: str,
) -> dict:

# AFTER:
def _run_one(
    full_name: str,
    model: "DockerAgentModel",
    root_path: str,
    category: str,
    repair_mode: str = "selfverify",
    repair_rounds: int = 2,
) -> dict:
```

The gate is inserted after the `print(f"[done  ] ...")` at line 199 and before `end_ts = time.time()` at line 201:

```
# BEFORE (run_rat_benchmark.py:199-201):
        print(f"[done  ] {full_name}  status={out.get('status')}", flush=True)

    end_ts = time.time()

# AFTER:
        print(f"[done  ] {full_name}  status={out.get('status')}", flush=True)

    # ── Runner-side repair loop ──────────────────────────────────────────────
    if repair_mode in ("runner", "both") and out.get("status") != "error":
        from repo2run_repair_port import _repair_and_rescore  # lazy import; module is optional
        try:
            out = _repair_and_rescore(
                out=out,
                root_path=root_path,
                full_name=full_name,
                llm=model.llm if hasattr(model, "llm") else "",
                max_rounds=repair_rounds,
            )
        except Exception as _repair_exc:
            print(f"[repair] {full_name} — runner repair failed (non-fatal): {_repair_exc}",
                  flush=True)

    end_ts = time.time()
```

**Rationale:** `out.get("status") != "error"` avoids trying to repair repos where `predict()` itself
raised — there is no Dockerfile to repair in that case. The lazy import means the import error is
non-fatal when `repo2run_repair_port.py` does not yet exist (useful during phased TDD).

---

## 3. `_child_cmd` and `worker_main` — threading repair flags to child processes

### 3a. `_child_cmd` (run_rat_benchmark.py:337-349)

Child processes are spawned by the scheduler and re-enter `worker_main`, which calls `_run_one`.
The repair flags must be forwarded on the command line so child processes inherit the parent's mode.

```
# BEFORE (run_rat_benchmark.py:337-349):
def _child_cmd(full_name: str, root_path: str, llm: str, timeout: int, num_turn: int,
               repos_json: str, model: str = "dockeragent") -> list:
    """Build the argv for a worker child process."""
    return [
        PY, __file__,
        "--only", full_name,
        "--root-path", root_path,
        "--llm", llm,
        "--timeout", str(timeout),
        "--num-turn", str(num_turn),
        "--repos-json", repos_json,
        "--model", model,
    ]

# AFTER:
def _child_cmd(full_name: str, root_path: str, llm: str, timeout: int, num_turn: int,
               repos_json: str, model: str = "dockeragent",
               repair_mode: str = "selfverify", repair_rounds: int = 2) -> list:
    """Build the argv for a worker child process."""
    return [
        PY, __file__,
        "--only", full_name,
        "--root-path", root_path,
        "--llm", llm,
        "--timeout", str(timeout),
        "--num-turn", str(num_turn),
        "--repos-json", repos_json,
        "--model", model,
        "--repair-mode", repair_mode,
        "--repair-rounds", str(repair_rounds),
    ]
```

### 3b. `worker_main` (run_rat_benchmark.py:595-611)

```
# BEFORE (run_rat_benchmark.py:595-611):
def worker_main(full_name: str, root_path: str, llm: str, timeout: int, num_turn: int,
                repos_json: str, model_name: str = "dockeragent") -> None:
    """--only <full_name>: run exactly one repo and exit.  Does NOT write rat_results.json."""
    os.makedirs(root_path, exist_ok=True)

    # Resolve category from the repos JSON (best-effort; "?" if not found).
    category = "?"
    try:
        for r in load_repos(repos_json):
            if r.get("full_name") == full_name:
                category = r.get("_category", "?")
                break
    except Exception:
        pass

    model = _make_model(model_name, root_path, timeout, llm, num_turn)
    _run_one(full_name, model, root_path, category)

# AFTER:
def worker_main(full_name: str, root_path: str, llm: str, timeout: int, num_turn: int,
                repos_json: str, model_name: str = "dockeragent",
                repair_mode: str = "selfverify", repair_rounds: int = 2) -> None:
    """--only <full_name>: run exactly one repo and exit.  Does NOT write rat_results.json."""
    os.makedirs(root_path, exist_ok=True)

    # Resolve category from the repos JSON (best-effort; "?" if not found).
    category = "?"
    try:
        for r in load_repos(repos_json):
            if r.get("full_name") == full_name:
                category = r.get("_category", "?")
                break
    except Exception:
        pass

    model = _make_model(model_name, root_path, timeout, llm, num_turn)
    _run_one(full_name, model, root_path, category,
             repair_mode=repair_mode, repair_rounds=repair_rounds)
```

### 3c. `DOCKERAGENT_REPAIR_MODE` env-var set — where in the parent process

The env var must be set **before** `subprocess.Popen` in `_run_child` (line 436) so child processes
inherit it. The correct place is in the `--only` (worker-mode) dispatch block in `__main__`
(currently `run_rat_benchmark.py:726-736`) and in `sequential_main` before the loop, and in
`parallel_main` before calling `scheduler`. However, because `_child_cmd` now forwards `--repair-mode`
explicitly on the CLI, the env-var is **not** the primary inheritance mechanism for child processes;
it only needs to be set so the **adapter** (`multi_docker_eval_adapter.py`) which runs in the same
process (under `--only` worker mode) reads the correct value.

The minimal addition is a single `os.environ` assignment in the `__main__` dispatch, inserted once
immediately after `args = parser.parse_args()`:

```
# BEFORE (run_rat_benchmark.py:713):
    args = parser.parse_args()

# AFTER:
    args = parser.parse_args()

    # Set DOCKERAGENT_REPAIR_MODE so the adapter reads the correct value in this process
    # and in any subprocess that inherits the environment (belt-and-suspenders with --repair-mode CLI).
    os.environ["DOCKERAGENT_REPAIR_MODE"] = args.repair_mode
```

For `worker_main` called under `--only`, `args.repair_mode` is already set from the CLI; the env-var
is then in the environment before `DockerAgentModel` (which calls the adapter) runs.

For `sequential_main` and `parallel_main`, the env-var is set once before the loop/scheduler, so
every child inherits it via `os.environ.copy()` passed to `subprocess.Popen` at `_run_child:436`
(`env=os.environ.copy()` — verified at `run_repo2run_benchmark.py:3339`; same pattern used in
`run_rat_benchmark.py`'s agent-run invocation in multi_docker_eval_adapter.py line 3339 pattern).

The `scheduler` function and `_run_child` need `repair_mode` and `repair_rounds` forwarded:

```
# BEFORE (run_rat_benchmark.py:474-485):
def scheduler(
    repos: list,
    root_path: str,
    llm: str,
    timeout: int,
    num_turn: int,
    repos_json: str,
    concurrency: int,
    disk_low_gb: float = 15.0,
    poll_interval: float = 30.0,
    model_name: str = "dockeragent",
) -> None:

# AFTER:
def scheduler(
    repos: list,
    root_path: str,
    llm: str,
    timeout: int,
    num_turn: int,
    repos_json: str,
    concurrency: int,
    disk_low_gb: float = 15.0,
    poll_interval: float = 30.0,
    model_name: str = "dockeragent",
    repair_mode: str = "selfverify",
    repair_rounds: int = 2,
) -> None:
```

And in `scheduler`'s `_submit_next` inner closure (line ~515), the `pool.submit` call must forward:
```python
fut = pool.submit(
    _run_child,
    fn, cat, root_path, llm, timeout, num_turn, repos_json, hard_wall, model_name,
    repair_mode, repair_rounds,
)
```

`_run_child` (line 401-471) similarly gains `repair_mode: str = "selfverify", repair_rounds: int = 2`
parameters and passes them to `_child_cmd`:
```python
cmd = _child_cmd(full_name, root_path, llm, timeout, num_turn, repos_json, model_name,
                 repair_mode, repair_rounds)
```

`parallel_main` (line 635-660) gains the same two parameters and passes them to `scheduler`.

`sequential_main` (line 614-632) gains the same two parameters and passes them to `_run_one`.

In `__main__`, the three mode-dispatch blocks update their calls:
- `worker_main(...)` call at line 727: add `repair_mode=args.repair_mode, repair_rounds=args.repair_rounds`
- `parallel_main(...)` call at line 740: add the same
- `sequential_main(...)` call at line 756: add the same

---

## 4. `DockerAgent` construction in `multi_docker_eval_adapter.py` — threading `enable_post_synthesis_repair`

**Source file:** `multi_docker_eval_adapter.py:764-778`

**Exact current construction site (lines 764-778):**
```python
            agent = DockerAgent(
                repo_url=repo_url,
                base_image=base_image or "auto",
                model=model,
                workplace=workplace,
                base_commit=base_commit,
                problem_statement=problem_statement,
                test_patch=test_patch,
                benchmark_evaluation_target=benchmark_evaluation_target,
                language=language,
                enable_observation_compression=enable_observation_compression,
                enable_long_term_memory=enable_long_term_memory,
                memory_path=memory_path,
                memory_embedding_model=memory_embedding_model,
            )
```

**Exact replacement:**
```python
            # Honour DOCKERAGENT_REPAIR_MODE set by run_rat_benchmark.py.
            # selfverify / both → enable the agent's own post-synthesis repair.
            # runner / off      → disable it (runner loop is authoritative, or baseline mode).
            _repair_mode_env = os.environ.get("DOCKERAGENT_REPAIR_MODE", "selfverify")
            _enable_agent_repair = _repair_mode_env in ("selfverify", "both")

            agent = DockerAgent(
                repo_url=repo_url,
                base_image=base_image or "auto",
                model=model,
                workplace=workplace,
                base_commit=base_commit,
                problem_statement=problem_statement,
                test_patch=test_patch,
                benchmark_evaluation_target=benchmark_evaluation_target,
                language=language,
                enable_observation_compression=enable_observation_compression,
                enable_long_term_memory=enable_long_term_memory,
                memory_path=memory_path,
                memory_embedding_model=memory_embedding_model,
                enable_post_synthesis_repair=_enable_agent_repair,
            )
```

**Confirmation that `agent.py` already accepts the parameter:**

- `agent.py:131` (DockerAgent `__init__` parameter list): `enable_post_synthesis_repair=True` — present as a keyword argument with default `True`.
- `agent.py:136`: `self.enable_post_synthesis_repair = enable_post_synthesis_repair` — stored on the instance.
- `agent.py:1175`: `if not getattr(self, "enable_post_synthesis_repair", False): return` — the guard that skips `_self_verify_and_repair` when the flag is `False`.
- `agent.py:2034-2038` (CLI): `--disable-post-synthesis-repair` already exists as an `action="store_true"` flag that sets `enable_post_synthesis_repair=False` when passed to the standalone CLI.

No changes to `agent.py` are needed to support the toggle; the adapter change above is sufficient.

---

## 5. Deprecation banners — exact text and locations

### 5a. `src/artifact_verify.py` — module docstring (line 1)

**Current docstring** (`src/artifact_verify.py:1-18`):
```
"""Clean-room verification and repair of a synthesized build recipe.

This is the engine behind the agent's post-synthesis self-verify phase. It renders
a *self-contained* Dockerfile from a build recipe (the agent's own Dockerfile only
works where the repo is already present; here we insert a ``git clone`` so it builds
in an empty context), builds it, runs the agent's own verified test command inside
the fresh image, and classifies whether the tests actually executed.
...
"""
```

**AFTER (insert after the opening `"""` on line 1, before the existing text):**
```python
"""**DEPRECATED (2026-06-08): This module is superseded by the runner-side repair loop
in ``repo2run_repair_port.py`` (called from ``run_rat_benchmark.py:_repair_and_rescore``).
It is retained and still used when ``--repair-mode selfverify`` or ``both`` is in effect.
Do not extend this module — port improvements to the runner loop instead.**

Clean-room verification and repair of a synthesized build recipe.
...
```

Concretely, the first line of the file changes from:
```
"""Clean-room verification and repair of a synthesized build recipe.
```
to:
```
"""**DEPRECATED (2026-06-08): Superseded by repo2run_repair_port.py / run_rat_benchmark.py:_repair_and_rescore.
Retained and toggled via enable_post_synthesis_repair / --repair-mode. Do not extend.**

Clean-room verification and repair of a synthesized build recipe.
```

### 5b. `agent.py` — `_self_verify_and_repair` method (line 1167)

**Current first line of method** (`agent.py:1167`):
```python
    def _self_verify_and_repair(self, dockerfile_path):
        """Build the synthesized recipe in a clean room, run the verified test command,
        and repair the recipe (deterministic + LLM) if the environment is incomplete.
```

**AFTER (insert deprecation sentence at the start of the docstring):**
```python
    def _self_verify_and_repair(self, dockerfile_path):
        """DEPRECATED (2026-06-08): superseded by the runner-side repair loop
        (run_rat_benchmark.py:_repair_and_rescore via repo2run_repair_port.py).
        Retained and toggleable via enable_post_synthesis_repair / --repair-mode.
        Do not extend — port improvements to the runner loop instead.

        Build the synthesized recipe in a clean room, run the verified test command,
        and repair the recipe (deterministic + LLM) if the environment is incomplete.
```

---

## 6. Summary (6 lines)

1. **Argparse (`run_rat_benchmark.py:712`):** add `--repair-mode {runner,selfverify,both,off}` (default `selfverify`) and `--repair-rounds INT` (default `2`) immediately before `args = parser.parse_args()`.
2. **Gate in `_run_one` (`run_rat_benchmark.py:199-201`):** insert `if repair_mode in ("runner", "both"): out = _repair_and_rescore(...)` after `predict()` returns and before `end_ts = time.time()`; add `repair_mode`/`repair_rounds` params to `_run_one`, `worker_main`, `_run_child`, `_child_cmd`, `scheduler`, `sequential_main`, `parallel_main` with defaults so callers need no change.
3. **Env-var bridge (`run_rat_benchmark.py:713+`):** `os.environ["DOCKERAGENT_REPAIR_MODE"] = args.repair_mode` immediately after `parse_args()` so the adapter reads the correct value in the same process; child CLIs get the value via forwarded `--repair-mode` in `_child_cmd`.
4. **Adapter (`multi_docker_eval_adapter.py:764`):** read `DOCKERAGENT_REPAIR_MODE`; set `enable_post_synthesis_repair=True` when mode is `selfverify` or `both`, `False` when mode is `runner` or `off`; pass to the `DockerAgent(...)` constructor which already accepts the parameter at `agent.py:131`.
5. **`agent.py` confirmation:** `enable_post_synthesis_repair` accepted at line 131, guarded at line 1175, CLI flag `--disable-post-synthesis-repair` at line 2034 — no changes needed; adapter change alone is sufficient.
6. **Deprecation banners:** prepend a one-sentence `DEPRECATED (2026-06-08)` banner to `src/artifact_verify.py` module docstring (line 1) and to the `_self_verify_and_repair` docstring at `agent.py:1167`; no functional code changes.
