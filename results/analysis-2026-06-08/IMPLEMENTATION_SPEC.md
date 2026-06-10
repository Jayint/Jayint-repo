# Implementation Spec: `repo2run_repair_port.py`

**Date:** 2026-06-08  
**Authors:** Synthesis of research sections R1–R4 plus PORT_REPAIR_LOOP_PLAN.md (revised) and REPAIR_LOOP_PORT_FIDELITY_AUDIT.md  
**Purpose:** Drop-in reference for an implementer to produce the standalone module and its wiring changes top-to-bottom with zero additional research.

---

## (A) Standalone Module Manifest

### A.1 File location and constraint

```
repo_root/repo2run_repair_port.py
```

This module MUST NOT import from `src/recipe_repair.py` or `src/artifact_verify.py`.  
The only permitted `src/` imports are `src.synthesizer` and `src.verification_bundle` (see glue table below).

### A.2 Import block

```python
from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

# Permitted src imports (NOT recipe_repair, NOT artifact_verify)
from src.synthesizer import Synthesizer as _Synthesizer
from src.verification_bundle import derive_supported_verification_bundle
```

### A.3 Ordered symbol manifest

Copy symbols in this order (bottom-up dependency order prevents forward-reference errors).  
Symbols marked **VERBATIM** are copied byte-for-byte from the cited source lines.  
Symbols marked **GLUE** are new code described in section (B).

| # | Symbol | Source | Status | Notes |
|---|--------|--------|--------|-------|
| 1 | `DOCKER_TIMEOUT_EXIT_CODE` | `run_repo2run_benchmark.py:37` | VERBATIM | constant = 124 |
| 2 | `OBSERVED_PIP_CONSTRAINTS_PATH` | `run_repo2run_benchmark.py:38` | VERBATIM | |
| 3 | `PYTORCH_CPU_INDEX_URL` | `run_repo2run_benchmark.py:52` | VERBATIM | |
| 4 | `TEST_EXECUTION_SHELL_WRAPPER` | `run_repo2run_benchmark.py:39-41` | VERBATIM | |
| 5 | `DOCKERFILE_REPAIR_LOG_LIMIT` | `run_repo2run_benchmark.py:53` | VERBATIM | = 12000 |
| 6 | `DOCKERFILE_REPAIR_SYSTEM_PROMPT` | `run_repo2run_benchmark.py:54-75` | VERBATIM | 13-rule LLM prompt |
| 7 | `DOCKERFILE_REPAIR_USER_PROMPT` | `run_repo2run_benchmark.py:77-83` | VERBATIM | |
| 8 | `_DOCKERFILE_VARIABLE_RE` | `run_repo2run_benchmark.py:832-834` | VERBATIM | |
| 9 | `_PIP_INSTALL_OPTION_VALUE_FLAGS` | `run_repo2run_benchmark.py:997-1019` | VERBATIM | |
| 10 | `_SUCCESSFULLY_INSTALLED_BLOCK_RE` | `run_repo2run_benchmark.py:1067-1070` | VERBATIM | |
| 11 | `_INSTALLED_PACKAGE_TOKEN_RE` | `run_repo2run_benchmark.py:1071-1073` | VERBATIM | |
| 12 | `_SHELL_CONTROL_TOKENS` | `run_repo2run_benchmark.py:1205` | VERBATIM | |
| 13 | `_CUDA_SKIPPED_LOCAL_SOURCE_INSTALL_RE` | `run_repo2run_benchmark.py:1585-1593` | VERBATIM | |
| 14 | `UNSAFE_COLLECT_COMMAND_SUBSTRINGS` | `run_repo2run_benchmark.py:2155-2165` | VERBATIM | |
| 15 | `DISALLOWED_COLLECT_TOKENS` | `run_repo2run_benchmark.py:2166` | VERBATIM | |
| 16 | `_MISSING_PYTHON_MODULE_RE` | `run_repo2run_benchmark.py:2563-2565` | VERBATIM | |
| 17 | `_KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS` | `run_repo2run_benchmark.py:2566-2569` | VERBATIM | |
| 18 | `TEST_SIGNAL_DETECTOR` | — | GLUE | `= _Synthesizer()` (module-level singleton) |
| 19 | `_decode_command_stream` | `run_repo2run_benchmark.py:114-119` | VERBATIM | |
| 20 | `normalize_command_list` | `run_repo2run_benchmark.py:140-148` | VERBATIM | |
| 21 | `write_text` | `run_repo2run_benchmark.py:109-111` | VERBATIM | |
| 22 | `_strip_requirement_line` | `run_repo2run_benchmark.py:2593-2599` | VERBATIM | |
| 23 | `_normalize_pip_constraint_name` | `run_repo2run_benchmark.py:1076-1077` | VERBATIM | |
| 24 | `_pip_requirement_name` | `run_repo2run_benchmark.py:1022-1027` | VERBATIM | |
| 25 | `_split_pip_install_command` | `run_repo2run_benchmark.py:1030-1064` | VERBATIM | |
| 26 | `_pip_installed_requirement_names` | `run_repo2run_benchmark.py:1170-1179` | VERBATIM | |
| 27 | `_normalize_dockerfile_path_value` | `run_repo2run_benchmark.py:946-957` | VERBATIM | |
| 28 | `_parse_dockerfile_env_instruction` | `run_repo2run_benchmark.py:837-863` | VERBATIM | |
| 29 | `_expand_dockerfile_variables` | `run_repo2run_benchmark.py:866-871` | VERBATIM | |
| 30 | `_normalize_dockerfile_workdir` | `run_repo2run_benchmark.py:874-881` | VERBATIM | |
| 31 | `infer_workdir_from_dockerfile` | `run_repo2run_benchmark.py:884-900` | VERBATIM | |
| 32 | `_pip_install_command_has_constraint` | `run_repo2run_benchmark.py:1134-1139` | VERBATIM | |
| 33 | `_pip_install_command_needs_observed_constraints` | `run_repo2run_benchmark.py:1142-1161` | VERBATIM | |
| 34 | `_add_observed_constraints_to_pip_command` | `run_repo2run_benchmark.py:1164-1167` | VERBATIM | |
| 35 | `_is_bare_pip_install_command` | `run_repo2run_benchmark.py:960-971` | VERBATIM | |
| 36 | `_is_bare_uv_pip_install_command` | `run_repo2run_benchmark.py:974-994` | VERBATIM | |
| 37 | `_is_uv_shell_installer_command` | `run_repo2run_benchmark.py:1677-1679` | VERBATIM | |
| 38 | `_is_generated_uv_pip_retry_command` | `run_repo2run_benchmark.py:1682-1686` | VERBATIM | |
| 39 | `_is_apt_install_replay_command` | `run_repo2run_benchmark.py:1689-1698` | VERBATIM | |
| 40 | `_extract_generated_pip_retry_inner_command` | `run_repo2run_benchmark.py:1649-1660` | VERBATIM | |
| 41 | `_extract_generated_apt_retry_inner_command` | `run_repo2run_benchmark.py:1663-1674` | VERBATIM | |
| 42 | `_extract_generated_retry_inner_shell_command` | `run_repo2run_benchmark.py:1501-1512` | VERBATIM | |
| 43 | `_iter_pip_install_segments` | `run_repo2run_benchmark.py:1208-1238` | VERBATIM | |
| 44 | `_local_pip_install_project_names` | `run_repo2run_benchmark.py:1241-1255` | VERBATIM | |
| 45 | `_drop_reinstalled_local_projects` | `run_repo2run_benchmark.py:1258-1277` | VERBATIM | |
| 46 | `_is_exact_torch_requirement` | `run_repo2run_benchmark.py:1280-1283` | VERBATIM | |
| 47 | `_is_broad_torch_requirement` | `run_repo2run_benchmark.py:1286-1289` | VERBATIM | |
| 48 | `_is_torch_cpu_split_candidate` | `run_repo2run_benchmark.py:1292-1293` | VERBATIM | |
| 49 | `_exact_torch_requirements` | `run_repo2run_benchmark.py:1296-1308` | VERBATIM | |
| 50 | `_compatible_torchvision_requirement` | `run_repo2run_benchmark.py:1311-1325` | VERBATIM | |
| 51 | `_pip_command_installs_torch_replacement` | `run_repo2run_benchmark.py:1328-1340` | VERBATIM | |
| 52 | `_pip_command_installs_mosaicml_stack` | `run_repo2run_benchmark.py:1343-1351` | VERBATIM | |
| 53 | `_dockerfile_exact_torch_replacement_requirement` | `run_repo2run_benchmark.py:1354-1368` | VERBATIM | |
| 54 | `_dockerfile_contains_torch_replacement` | `run_repo2run_benchmark.py:1371-1382` | VERBATIM | |
| 55 | `_dockerfile_contains_mosaicml_stack` | `run_repo2run_benchmark.py:1385-1396` | VERBATIM | |
| 56 | `_drop_redundant_broad_torch_bootstrap` | `run_repo2run_benchmark.py:1399-1433` | VERBATIM | |
| 57 | `_add_compatible_torchvision_constraint` | `run_repo2run_benchmark.py:1436-1460` | VERBATIM | |
| 58 | `_is_redundant_exact_torch_reinstall` | `run_repo2run_benchmark.py:1463-1476` | VERBATIM | |
| 59 | `_is_cuda_local_installer_scaffolding_command` | `run_repo2run_benchmark.py:1479-1487` | VERBATIM | |
| 60 | `_rewrite_absolute_tests_redirect_to_workdir` | `run_repo2run_benchmark.py:1490-1498` | VERBATIM | |
| 61 | `_harden_cuda_skipped_local_source_install` | `run_repo2run_benchmark.py:1596-1613` | VERBATIM | |
| 62 | `_drop_replay_poetry_lock_command` | `run_repo2run_benchmark.py:1616-1629` | VERBATIM | |
| 63 | `_dockerfile_may_include_poetry_lock` | `run_repo2run_benchmark.py:1632-1646` | VERBATIM | |
| 64 | `_repair_generated_apt_retry_status_variables` | `run_repo2run_benchmark.py:1701-1709` | VERBATIM | |
| 65 | `_add_no_deps_to_known_force_reinstall` | `run_repo2run_benchmark.py:1182-1202` | VERBATIM | |
| 66 | `_shell_single_quote` | `run_repo2run_benchmark.py:1742-1743` | VERBATIM | also called `_quote_shell_single` in synthesizer; same body |
| 67 | `_has_unclosed_shell_quote` | `run_repo2run_benchmark.py:1746-1763` | VERBATIM | |
| 68 | `_format_multiline_run_as_script` | `run_repo2run_benchmark.py:1766-1774` | VERBATIM | |
| 69 | `_is_top_level_dockerfile_instruction` | `run_repo2run_benchmark.py:1872-1882` | VERBATIM | |
| 70 | `_join_dockerfile_continued_lines` | `run_repo2run_benchmark.py:1796-1805` | VERBATIM | |
| 71 | `_collect_continued_dockerfile_instruction` | `run_repo2run_benchmark.py:1777-1793` | VERBATIM | |
| 72 | `_collect_raw_multiline_run` | `run_repo2run_benchmark.py:1808-1832` | VERBATIM | |
| 73 | `_collect_generated_apt_retry_with_orphan_continuations` | `run_repo2run_benchmark.py:1835-1869` | VERBATIM | |
| 74 | `_render_observed_pip_constraints_instruction` | `run_repo2run_benchmark.py:1124-1131` | VERBATIM | |
| 75 | `build_resilient_pip_install_run_instruction` | `src/synthesizer.py:265-362` (copy body, inline `_quote_shell_single`) | GLUE-COPY | copy verbatim from synthesizer; not from recipe_repair |
| 76 | `build_resilient_apt_install_run_instruction` | `src/synthesizer.py:265-362` (copy body, inline `_quote_shell_single`) | GLUE-COPY | same |
| 77 | `build_resilient_uv_install_run_instruction` | `run_repo2run_benchmark.py:1712-1739` | VERBATIM | pure Python, no src import |
| 78 | `split_heavy_pip_install_replay_commands` | `run_repo2run_benchmark.py:1515-1582` | VERBATIM | |
| 79 | `extract_observed_pip_install_constraints_from_text` | `run_repo2run_benchmark.py:1080-1094` | VERBATIM | |
| 80 | `collect_observed_pip_install_constraints` | `run_repo2run_benchmark.py:1097-1121` | VERBATIM | |
| 81 | `normalize_eval_dockerfile_for_replay` | `run_repo2run_benchmark.py:1885-2109` | VERBATIM | large rewrite pass; ~225 lines |
| 82 | `build_test_execution_script` | `run_repo2run_benchmark.py:2434-2451` | VERBATIM | |
| 83 | `discover_internal_import_prefixes` | `run_repo2run_benchmark.py:2454-2464` | VERBATIM | |
| 84 | `output_has_collection_error_signal` | `run_repo2run_benchmark.py:2467-2473` | VERBATIM | |
| 85 | `output_has_invocation_error_signal` | `run_repo2run_benchmark.py:2476-2484` | VERBATIM | |
| 86 | `output_has_internal_repo_import_error_signal` | `run_repo2run_benchmark.py:2487-2509` | VERBATIM | |
| 87 | `classify_test_execution` | `run_repo2run_benchmark.py:2512-2560` | VERBATIM | calls `TEST_SIGNAL_DETECTOR` |
| 88 | `extract_missing_python_modules_from_test_execution` | `run_repo2run_benchmark.py:2572-2590` | VERBATIM | |
| 89 | `_find_declared_requirement_in_workspace` | `run_repo2run_benchmark.py:2602-2641` | VERBATIM | |
| 90 | `_requirement_for_missing_module` | `run_repo2run_benchmark.py:2644-2663` | VERBATIM | |
| 91 | `_preferred_pip_invocation_for_dockerfile` | `run_repo2run_benchmark.py:2666-2672` | VERBATIM | |
| 92 | `_dockerfile_already_installs_requirement` | `run_repo2run_benchmark.py:2675-2705` | VERBATIM | |
| 93 | `_insert_run_instruction_before_final_command` | `run_repo2run_benchmark.py:2708-2722` | VERBATIM | |
| 94 | `repair_dockerfile_for_missing_python_modules` | `run_repo2run_benchmark.py:2725-2752` | VERBATIM | |
| 95 | `should_add_postgres_host_alias` | `run_repo2run_benchmark.py:2793-2820` | VERBATIM | |
| 96 | `evaluate_built_image` | `run_repo2run_benchmark.py:2823-2889` | VERBATIM | |
| 97 | `truncate_for_repair_prompt` | `run_repo2run_benchmark.py:2892-2902` | VERBATIM | |
| 98 | `extract_json_object_candidates` | `run_repo2run_benchmark.py:2905-2949` | VERBATIM | includes in-string/escape tracking |
| 99 | `extract_dockerfile_repair_json` | `run_repo2run_benchmark.py:2952-2970` | VERBATIM | |
| 100 | `build_dockerfile_repair_input` | `run_repo2run_benchmark.py:2973-3041` | VERBATIM | trajectory-aware; reads `successful_actions` |
| 101 | `repair_dockerfile_with_llm` | `run_repo2run_benchmark.py:3044-3114` | VERBATIM | |
| 102 | `docker_build_failed_due_to_unavailable_daemon` | `run_repo2run_benchmark.py:245-257` | VERBATIM | infra short-circuit |
| 103 | `run_command` | `run_repo2run_benchmark.py:201-242` | VERBATIM | |
| 104 | `create_openai_client_from_env` | `src/workplace_replay.py:71-79` | GLUE-COPY | copy 9-line body verbatim |
| 105 | `derive_verification_commands` | `run_repo2run_benchmark.py:2421-2431` | VERBATIM | calls `derive_supported_verification_bundle` via import |
| 106 | `junit_to_pytest_results` | NEW | GLUE | see section (B) |
| 107 | `real_test_command` | NEW | GLUE | see section (B) |
| 108 | `_repair_and_rescore` | NEW | GLUE | see section (C) |

**Infra dependency flag:** `subprocess` is used by `run_command` (symbol #103). The source imports it at module level (`run_repo2run_benchmark.py:96`). Add `import subprocess` to the import block.

---

## (B) Glue Contracts

### B.1 `junit_to_pytest_results`

**Purpose:** Convert a live pytest run (inside the repaired Docker container) into the exact JSON schema that `scorers.py` reads. This is the only translation layer between the repair loop's docker run and the existing scorers.

**Signature:**
```python
def junit_to_pytest_results(
    execution: dict[str, Any],         # the run_command() return dict from the test step
    image_tag: str,                    # docker image to docker cp junit from
    junit_container_path: str = "/tmp/repair_junit.xml",
) -> dict[str, Any]:
```

**Target schema** (exact keys; only `summary` + `error_breakdown` are load-bearing for scorers — `scorers.py:99-106`):

```json
{
  "summary": {
    "total_tests": <int>,
    "passed":      <int>,
    "failed":      <int>,
    "errors":      <int>,
    "skipped":     <int>,
    "xfailed":     0,
    "xpassed":     0
  },
  "error_breakdown": {
    "<ExceptionClassName>": <int>
  },
  "failed_tests":  [...],
  "error_tests":   [...],
  "raw_output":    "<str>",
  "returncode":    <int>,
  "parse_method":  "junit_xml" | "regex_fallback"
}
```

**`error_breakdown` derivation rule** (must match `run_pytest.py:110-150` exactly):

Apply first-match regex scan on the combined `<failure message> + "\n" + <failure text>` for each failed/errored test. The 21-bucket vocabulary in first-match order:

```
ModuleNotFoundError, ImportError, AttributeError, AssertionError (no colon),
TypeError, ValueError, KeyError, IndexError, NameError, FileNotFoundError,
RuntimeError, OSError, IOError, ZeroDivisionError, SyntaxError, IndentationError,
MemoryError, RecursionError, TimeoutError, ConnectionError, PermissionError
```

Catch-all: `OtherError`.  
Special: if `execution["timed_out"]` is True, emit `{"summary": {"total_tests": 0, ...}, "error_breakdown": {"TimeoutError": 1}}` directly without XML parsing (matches `run_pytest.py:517`).

**JUnit acquisition strategy:** The test command passed to `evaluate_built_image` must append `--junitxml=<junit_container_path>`. After `docker run` completes (regardless of returncode), run `docker cp <container_id>:<junit_container_path> <local_tmppath>` to retrieve the XML. Parse with `xml.etree.ElementTree`. If XML is absent or unparseable, fall back to regex parsing of stdout/stderr and set `parse_method = "regex_fallback"`.

**Fully-passing repair minimum viable emit:**
```json
{
  "summary": {"total_tests": N, "passed": N, "failed": 0, "errors": 0,
              "skipped": 0, "xfailed": 0, "xpassed": 0},
  "error_breakdown": {},
  "failed_tests": [], "error_tests": [],
  "raw_output": "...", "returncode": 0, "parse_method": "junit_xml"
}
```

### B.2 `real_test_command`

**Purpose:** Derive a runnable test command from the recipe. The framework uses `--collect-only` for pre-run validation; those must be stripped before the repair loop fires actual tests.

**Signature:**
```python
def real_test_command(recipe: dict[str, Any]) -> tuple[str, str]:
    """Return (test_command, junit_container_path)."""
```

**Logic (in order):**
1. Read `recipe.get("logs", {}).get("verified_test_commands") or []`.
2. Strip each command of `--collect-only` and any `-q`/`--quiet` flags (keep other flags).
3. If the stripped command is non-empty and starts with `pytest`, `python -m pytest`, `poetry run pytest`, `uv run pytest` — accept it.
4. If nothing passes, fall back to `"pytest -q --disable-warnings"`.
5. Append `--junitxml=/tmp/repair_junit.xml` to the accepted command.
6. Return `(command_with_junitxml, "/tmp/repair_junit.xml")`.

**Edge cases:**
- Commands with shell operators (`&&`, `|`, `;`) should be accepted as-is (do not strip operators; just append `--junitxml=...` only if the last token is not a redirect).
- Preserve existing `--junitxml=` if already present; do not double-add.

---

## (C) `_repair_and_rescore` — Signature, Loop, and Annotations

### C.1 Signature

```python
def _repair_and_rescore(
    out: dict[str, Any],          # predict() return dict (7 keys; see R3 §1)
    root_path: str,               # DockerAgentModel.root_path
    full_name: str,               # "owner/repo"
    llm: str,                     # model.llm (the --llm CLI value)
    max_rounds: int = 2,          # --repair-rounds CLI value
) -> dict[str, Any]:              # returns out (possibly mutated in-place)
```

### C.2 Input shapes that are NOT in `out`

**Critical (R3 §1, confirmed):** `predict()` returns only `{status, failure_reason, root_path, full_name, requested_model, base_image, head_sha}`. The following fields are **absent** from `out` and must be read from disk:

- `successful_actions` — not in `out`, not in recipe JSON at `out_dir/{slug}.json`. It is in the workplace `agent_run_summary.json` at path `{root_path}/output/{full_name}/workplace/agent_run_summary.json` (adapter `workplace_replay.py` / adapter line 2680 writes it). Load it separately.
- `build_recipe` — present at `recipe_json["logs"]["build_recipe"]` (adapter line 913).
- `verified_test_commands` — present at `recipe_json["logs"]["verified_test_commands"]` (adapter lines 864-872).

**On-disk path derivations (from `root_path` and `full_name`):**
```python
out_dir          = os.path.join(root_path, "output", full_name)
slug             = full_name.replace("/", "__")
recipe_path      = os.path.join(out_dir, f"{slug}.json")
dockerfile_path  = os.path.join(out_dir, "eval_build", "Dockerfile")
pytest_json_path = os.path.join(out_dir, "run_pytest_results.json")
repair_dir       = os.path.join(out_dir, "repair_artifacts")
agent_summary_path = os.path.join(out_dir, "workplace", "agent_run_summary.json")
```

`eval_build/Dockerfile` is fully self-contained (git clone baked in — R3 §3); `docker build eval_build/` needs no host context.

### C.3 Loop body — annotated line-by-line

```python
def _repair_and_rescore(
    out: dict[str, Any],
    root_path: str,
    full_name: str,
    llm: str,
    max_rounds: int = 2,
) -> dict[str, Any]:
    # --- GLUE: path derivation ---
    out_dir          = os.path.join(root_path, "output", full_name)
    slug             = full_name.replace("/", "__")
    recipe_path      = os.path.join(out_dir, f"{slug}.json")
    dockerfile_path  = os.path.join(out_dir, "eval_build", "Dockerfile")
    pytest_json_path = os.path.join(out_dir, "run_pytest_results.json")
    repair_dir       = os.path.join(out_dir, "repair_artifacts")
    os.makedirs(repair_dir, exist_ok=True)

    # --- GLUE: early-exit if already effective ---
    try:
        existing = json.loads(Path(pytest_json_path).read_text())
        summary = existing.get("summary", {})
        effective_total = summary.get("total_tests", 0) - summary.get("skipped", 0)
        passed = summary.get("passed", 0)
        if effective_total > 0 and passed == effective_total:
            return out    # already passing — do not touch
    except Exception:
        pass  # missing or malformed JSON → proceed with repair

    # --- GLUE: load recipe and agent summary ---
    try:
        recipe = json.loads(Path(recipe_path).read_text())
    except Exception:
        return out  # no recipe → cannot repair

    try:
        agent_summary_path = os.path.join(out_dir, "workplace", "agent_run_summary.json")
        run_summary = json.loads(Path(agent_summary_path).read_text())
    except Exception:
        run_summary = recipe.get("logs") or {}

    # --- GLUE: load current Dockerfile ---
    try:
        current_eval_dockerfile_text = Path(dockerfile_path).read_text(encoding="utf-8")
    except Exception:
        return out  # no Dockerfile → cannot repair

    # --- GLUE: derive run commands via verbatim helper ---
    runtime_commands, test_commands_base, _source = derive_verification_commands(run_summary)
    # GLUE: override test_commands with real (non-collect-only) command + junitxml
    real_cmd, junit_path = real_test_command(recipe)
    test_commands = [real_cmd]

    # --- GLUE: collect pip constraints from run_summary (pre-loop, verbatim helper) ---
    pip_constraints = collect_observed_pip_install_constraints(None, run_summary)

    # --- GLUE: workdir-relative eval_build_context_path for docker build ---
    eval_build_context_path = Path(out_dir) / "eval_build"
    repo_root = Path(out_dir)

    # --- GLUE: synthetic instance dict for build_dockerfile_repair_input ---
    instance = {
        "instance_id": slug,
        "full_name": full_name,
        "sha": out.get("head_sha", ""),
        "repo_url": (run_summary or {}).get("repo_url", ""),
    }

    # --- GLUE: unique image tag per repo+pid to avoid concurrency collisions ---
    safe_slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
    image_tag = f"dockeragent-repair-{safe_slug}-{os.getpid()}"

    # --- GLUE: shared accumulator lists and lazy client ---
    dockerfile_validation_attempts: list[dict] = []
    dockerfile_repair_rounds: list[dict] = []
    repair_client = None
    eval_dockerfile_path = eval_build_context_path / "Dockerfile"

    # ===== VERBATIM loop body from run_repo2run_benchmark.py:3398-3530 =====
    # (only I/O bindings are new; the repair decision logic is byte-for-byte)
    try:
        for attempt_index in range(max_rounds + 1):                 # VERBATIM: range(max_repair_rounds + 1)
            workdir = infer_workdir_from_dockerfile(current_eval_dockerfile_text)  # VERBATIM
            eval_dockerfile_path.write_text(current_eval_dockerfile_text, encoding="utf-8")  # VERBATIM

            docker_build_command = ["docker", "build"]              # VERBATIM
            # GLUE: no --platform (derive from recipe if needed; omit for now)
            docker_build_command.extend([                           # VERBATIM pattern
                "-f", str(eval_dockerfile_path),
                "-t", image_tag,
                str(eval_build_context_path),
            ])
            docker_build = run_command(                             # VERBATIM
                docker_build_command,
                cwd=repo_root,
                env=os.environ.copy(),
                timeout_seconds=1800,                               # GLUE: default; wire to --timeout later
            )

            test_execution = None
            if docker_build["returncode"] == 0 and not docker_build.get("timed_out"):  # VERBATIM
                test_execution = evaluate_built_image(              # VERBATIM
                    image_tag=image_tag,
                    workdir=workdir,
                    runtime_commands=runtime_commands,
                    test_commands=test_commands,
                    cwd=repo_root,
                    timeout_seconds=600,                            # GLUE: default; wire to --timeout later
                    workspace_root=eval_build_context_path,
                    docker_platform=None,                           # GLUE: no platform for now
                )

            attempt_success = bool(                                 # VERBATIM
                docker_build
                and docker_build["returncode"] == 0
                and not docker_build.get("timed_out")
                and test_execution
                and test_execution["all_test_commands_effective"]
            )
            dockerfile_validation_attempts.append({                 # VERBATIM
                "attempt": attempt_index,
                "dockerfile_path": str(eval_dockerfile_path),
                "docker_build": docker_build,
                "test_execution": test_execution,
                "success": attempt_success,
            })

            # GLUE: unconditional write of results after each attempt
            # (mirrors Repo2Run's "score the last attempt regardless")
            if test_execution is not None:
                pr = junit_to_pytest_results(                       # GLUE
                    execution=test_execution["results"][0]["execution"] if test_execution["results"] else {},
                    image_tag=image_tag,
                    junit_container_path=junit_path,
                )
                Path(pytest_json_path).write_text(
                    json.dumps(pr, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                # GLUE: write sidecar for audit trail
                Path(pytest_json_path.replace(".json", f"_repair_attempt{attempt_index}.json")).write_text(
                    json.dumps(pr, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            if attempt_success:                                     # VERBATIM
                break
            if attempt_index < max_rounds and test_execution:      # VERBATIM
                repaired_text, installed_requirements = repair_dockerfile_for_missing_python_modules(  # VERBATIM
                    current_eval_dockerfile_text,
                    test_execution,
                    eval_build_context_path,
                )
                if repaired_text != current_eval_dockerfile_text:   # VERBATIM
                    dockerfile_repair_rounds.append({               # VERBATIM
                        "round": attempt_index + 1,
                        "source": "deterministic_missing_python_modules",
                        "error": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        "raw_content": "",
                        "dockerfile_text": repaired_text,
                        "rationale": (
                            "Installed missing Python modules reported by pytest collection: "
                            + ", ".join(installed_requirements)
                        ),
                        "confidence": "high",
                        "log_path": None,
                    })
                    current_eval_dockerfile_text = normalize_eval_dockerfile_for_replay(  # VERBATIM
                        repaired_text,
                        pip_constraints=pip_constraints,
                    )
                    continue                                         # VERBATIM
            if attempt_index >= max_rounds:                         # VERBATIM
                break
            if docker_build_failed_due_to_unavailable_daemon(docker_build):  # VERBATIM
                break

            repair_input = build_dockerfile_repair_input(          # VERBATIM
                instance=instance,
                workdir=workdir,
                dockerfile_text=current_eval_dockerfile_text,
                run_summary=run_summary,
                runtime_commands=runtime_commands,
                test_commands=test_commands,
                docker_build=docker_build,
                test_execution=test_execution,
            )
            try:
                if repair_client is None:                           # VERBATIM
                    repair_client = create_openai_client_from_env()  # VERBATIM
                repair_result = repair_dockerfile_with_llm(        # VERBATIM
                    client=repair_client,
                    model=llm,                                      # GLUE: model.llm → passed as param
                    repair_input=repair_input,
                    artifact_dir=Path(repair_dir),
                    round_index=attempt_index + 1,
                )
            except Exception as exc:                               # VERBATIM
                repair_result = {
                    "round": attempt_index + 1,
                    "source": "llm_error",
                    "error": str(exc),
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "raw_content": "",
                    "dockerfile_text": None,
                    "rationale": "",
                    "confidence": "low",
                    "log_path": None,
                }
            dockerfile_repair_rounds.append(repair_result)         # VERBATIM
            repaired_text = repair_result.get("dockerfile_text")   # VERBATIM
            if not repaired_text:                                   # VERBATIM
                break
            current_eval_dockerfile_text = normalize_eval_dockerfile_for_replay(  # VERBATIM
                repaired_text,
                pip_constraints=pip_constraints,
            )
        # ===== END VERBATIM LOOP =====

    except Exception as _exc:
        # GLUE: never-raise contract; degrade to original results
        print(f"[repair] {full_name} — repair loop exception (non-fatal): {_exc}", flush=True)

    finally:
        # GLUE: always remove the repair image to free disk
        try:
            subprocess.run(["docker", "rmi", "-f", image_tag],
                           capture_output=True, timeout=30)
        except Exception:
            pass

    # GLUE: write repair metadata sidecar
    try:
        sidecar = {
            "full_name": full_name,
            "repair_rounds": len(dockerfile_repair_rounds),
            "validation_attempts": len(dockerfile_validation_attempts),
            "repair_history": dockerfile_repair_rounds,
        }
        Path(os.path.join(repair_dir, "repair_meta.json")).write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    return out
```

### C.3 Annotation key

- Lines marked **VERBATIM** must match `run_repo2run_benchmark.py:3398-3530` character-for-character (modulo renamed local variables).
- Lines marked **GLUE** are new; they are the only non-verbatim logic. Keep them minimal.
- `run_summary` is loaded from `workplace/agent_run_summary.json` if available, otherwise from `recipe["logs"]`. This is the bridge for `successful_actions` / `failed_actions` reaching `build_dockerfile_repair_input` (which reads `run_summary.get("successful_actions") or []` at `run_repo2run_benchmark.py:3041`).

---

## (D) Wiring Diffs

All diffs are in `run_rat_benchmark.py` and `multi_docker_eval_adapter.py`. No changes to `agent.py`.

### D.1 Argparse additions — `run_rat_benchmark.py`

**Insertion point:** after `--model` argument closes and before `args = parser.parse_args()` at line 713.

```python
    # Repair-loop controls — insert after run_repo2run_benchmark.py:712
    parser.add_argument(
        "--repair-mode",
        choices=["runner", "selfverify", "both", "off"],
        default="selfverify",
        help=(
            "Repair strategy. "
            "'runner': runner-side verbatim Repo2Run loop ON, agent self-verify OFF. "
            "'selfverify': agent self-verify ON, runner loop OFF (current default). "
            "'both': both ON (debug-compare only). "
            "'off': both OFF (clean baseline). "
            "Default: selfverify."
        ),
    )
    parser.add_argument(
        "--repair-rounds",
        type=int,
        default=2,
        help=(
            "Maximum LLM Dockerfile repair rounds for the runner-side loop. "
            "0 disables LLM repair (deterministic only). Default: 2."
        ),
    )
```

**After `args = parser.parse_args()` at line 713, add immediately:**

```python
    os.environ["DOCKERAGENT_REPAIR_MODE"] = args.repair_mode
```

This sets the env var before any subprocess inherits it and before the adapter reads it.

### D.2 `_run_one` signature and gate — `run_rat_benchmark.py`

**Signature change** (`run_rat_benchmark.py:146-151`):

```python
# BEFORE
def _run_one(full_name, model, root_path, category) -> dict:

# AFTER
def _run_one(
    full_name: str,
    model: "DockerAgentModel",
    root_path: str,
    category: str,
    repair_mode: str = "selfverify",
    repair_rounds: int = 2,
) -> dict:
```

**Gate injection** (after `print("[done  ]...")` at line 199, before `end_ts = time.time()` at line 201):

```python
        print(f"[done  ] {full_name}  status={out.get('status')}", flush=True)

    # Runner-side repair loop ────────────────────────────────────────────────
    if repair_mode in ("runner", "both") and out.get("status") != "error":
        from repo2run_repair_port import _repair_and_rescore  # lazy; safe if module absent
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

### D.3 Param threading — all call sites

Add `repair_mode: str = "selfverify", repair_rounds: int = 2` to the signatures of (in order):

1. `_run_child` (`run_rat_benchmark.py:401`) — forward to `_child_cmd`.
2. `_child_cmd` (`run_rat_benchmark.py:337`) — append `"--repair-mode", repair_mode, "--repair-rounds", str(repair_rounds)` to the returned argv list.
3. `scheduler` (`run_rat_benchmark.py:474`) — forward to `_run_child` via `pool.submit`.
4. `sequential_main` (`run_rat_benchmark.py:614`) — forward to `_run_one`.
5. `worker_main` (`run_rat_benchmark.py:595`) — forward to `_run_one`.

In `__main__` dispatch (lines 726-756), add `repair_mode=args.repair_mode, repair_rounds=args.repair_rounds` to the three calls: `worker_main(...)`, `parallel_main(...)`, `sequential_main(...)`.

### D.4 Adapter toggle — `multi_docker_eval_adapter.py`

**Change site:** `multi_docker_eval_adapter.py:764-778` (`DockerAgent(...)` constructor call).

**Before (line 764):**
```python
            agent = DockerAgent(
                repo_url=repo_url,
                ...
            )
```

**After:**
```python
            # Honour DOCKERAGENT_REPAIR_MODE set by run_rat_benchmark.py.
            # selfverify / both → enable agent's own post-synthesis repair.
            # runner / off      → disable it (runner loop is authoritative or baseline mode).
            _repair_mode_env = os.environ.get("DOCKERAGENT_REPAIR_MODE", "selfverify")
            _enable_agent_repair = _repair_mode_env in ("selfverify", "both")

            agent = DockerAgent(
                repo_url=repo_url,
                ...
                enable_post_synthesis_repair=_enable_agent_repair,
            )
```

Confirmed: `agent.py:131` accepts `enable_post_synthesis_repair` with default `True`; `agent.py:1175` guards on it; `agent.py:2034` already has a CLI flag. No changes to `agent.py` are needed.

### D.5 Deprecation banners (documentation only; no functional change)

**`src/artifact_verify.py` line 1** — prepend to existing module docstring:

```
**DEPRECATED (2026-06-08): Superseded by repo2run_repair_port.py / run_rat_benchmark.py:_repair_and_rescore.
Retained and toggled via enable_post_synthesis_repair / --repair-mode. Do not extend.**
```

**`agent.py:1167`** — prepend to `_self_verify_and_repair` docstring:

```
DEPRECATED (2026-06-08): superseded by the runner-side repair loop
(run_rat_benchmark.py:_repair_and_rescore via repo2run_repair_port.py).
Retained and toggleable via enable_post_synthesis_repair / --repair-mode.
Do not extend — port improvements to the runner loop instead.
```

---

## (E) TDD Task Checklist (maps to PLAN §6)

Step numbers match PLAN §6. Step 3 (editable-install deterministic recovery) is **DEFERRED** and absent from this checklist.

- [ ] **Step 0 — Module skeleton + import-isolation test**
  - Create `repo2run_repair_port.py` with the import block from A.2.
  - RED: `test_no_suspect_imports()` — assert that `import repo2run_repair_port` does not transitively import `src.recipe_repair` or `src.artifact_verify`. Use `sys.modules` inspection.
  - GREEN: copy all VERBATIM symbols from A.3 in order. Module imports cleanly.

- [ ] **Step 1 — `junit_to_pytest_results` + fixtures**
  - RED: feed `run_pytest_results.json` from `rat_run/output/resend/resend-python/` as expected output; feed the corresponding stdout as input; assert exact `summary` + `error_breakdown` match.
  - GREEN: implement JUnit XML parse path + `categorize_error` matching `run_pytest.py:110-150`.
  - Add fixture for `timed_out=True` path → `{"error_breakdown": {"TimeoutError": 1}}`.
  - Validate against at least 2 real on-disk files from `rat_run/output/`.

- [ ] **Step 2 — `real_test_command`**
  - RED: `--collect-only` command → stripped real command with `--junitxml=...` appended.
  - RED: `poetry run pytest --collect-only` → `poetry run pytest --junitxml=...`.
  - RED: no test commands in recipe → `"pytest -q --disable-warnings --junitxml=..."`.
  - GREEN: implement.

- [ ] **Step 3 — DEFERRED (editable-install recovery).** Do not implement for the A/B.

- [ ] **Step 4 — `_repair_and_rescore` loop (mocked docker/test)**
  - Mock `run_command` to simulate: (a) build succeeds, test hollow → deterministic repair → resolved; (b) build succeeds, test hollow → LLM repair with mocked `repair_dockerfile_with_llm` → resolved; (c) unconditional write of last attempt even when unresolved; (d) infra short-circuit when `docker_build_failed_due_to_unavailable_daemon` returns True; (e) never raises even if all internals throw.
  - Use `unittest.mock.patch` to avoid actual docker calls.

- [ ] **Step 5 — LLM repair input trajectory test**
  - Assert that `build_dockerfile_repair_input(run_summary=run_summary_with_successful_actions, ...)` produces a payload where `payload["agent_run_summary"]["successful_actions"]` is non-empty (audit H3/H5 — the key regression vector).
  - Confirm `DOCKERFILE_REPAIR_SYSTEM_PROMPT` contains Rule 5 text: "restore omitted successful setup commands from agent_run_summary in the original trajectory order."

- [ ] **Step 6 — Wiring + integration**
  - Apply diffs from D.1–D.5.
  - RED: `test_repair_mode_flag()` — parse `["--repair-mode", "runner", "--repair-rounds", "3"]`; assert `args.repair_mode == "runner"` and `args.repair_rounds == 3`.
  - RED: `test_adapter_toggle_off()` — mock `os.environ["DOCKERAGENT_REPAIR_MODE"] = "runner"` and assert `DockerAgent(..., enable_post_synthesis_repair=False)` is called.
  - GREEN: apply wiring.
  - Confirm `tests/test_artifact_verify.py` + `tests/test_recipe_repair.py` still green (those use the legacy path which is untouched functionally).

- [ ] **Step 7 — `compute_essr.py` repaired column** (PLAN §6 step 7)
  - Add detection of `repair_artifacts/repair_meta.json` sidecar.
  - Surface `repaired_count` and `repaired_pass_rate` in the output table.

---

## (F) Open Risks

Each risk below was not fully confirmable from static analysis. The stated resolution tells the implementer how to resolve it at implementation time.

### RISK-1 (HIGH): `successful_actions` may not be available at repair time

**What is uncertain:** `successful_actions` is written to `workplace/agent_run_summary.json` by the adapter (`multi_docker_eval_adapter.py:2680`), but the workplace directory is not guaranteed to persist after `predict()` returns. If it is cleaned up (or never written in a failed run), `run_summary["successful_actions"]` will be empty, defeating the primary benefit of trajectory-aware LLM repair (audit H3).

**How to resolve:** At implementation time, (a) confirm `workplace/agent_run_summary.json` exists after a normal run by checking `rat_run/output/resend/resend-python/workplace/`. (b) If absent, fall back to `recipe["logs"]` which has `verified_test_commands` and `build_recipe.build_commands` — these are sufficient for Rules 11/12 even without `successful_actions`. (c) Add a warning log when `successful_actions` is empty so the gap is visible in run logs.

### RISK-2 (HIGH): `run_pytest_results.json` schema mismatch if JUnit XML is unavailable

**What is uncertain:** The repair loop appends `--junitxml` to the test command, but some test suites use custom test runners that ignore pytest flags, or pytest is invoked via a wrapper script that drops unknown flags. In those cases the XML is never written inside the container and `docker cp` fails.

**How to resolve:** Implement the regex-fallback path in `junit_to_pytest_results` (see B.1). Validate by testing against at least one repo that already has `run_pytest_results.json` on disk: run the repair loop's test command manually and check whether `repair_junit.xml` appears in the container.

### RISK-3 (MEDIUM): `docker_platform` omitted — ARM/AMD64 mismatch on Apple Silicon

**What is uncertain:** The verbatim loop supports `--platform` forwarding (R1 §11, loop line `docker_build_command.extend(["--platform", docker_platform])`). The RAT runner omits platform from the glue code above. On an Apple Silicon host, images may build for wrong arch.

**How to resolve:** Read `docker_platform` from the recipe JSON (`recipe.get("base_image")` → infer from FROM line, or expose it from `DockerAgentModel.base_image`). Add `docker_platform = recipe.get("docker_platform")` to the glue section and pass it through.

### RISK-4 (MEDIUM): `evaluate_built_image` uses `TEST_SIGNAL_DETECTOR` which requires `src.synthesizer` to initialize correctly

**What is uncertain:** `_Synthesizer()` construction may fail or produce different behaviour in isolation from the full agent run context (e.g., if `Synthesizer.__init__` reads config files or environment state).

**How to resolve:** Run `python -c "from repo2run_repair_port import TEST_SIGNAL_DETECTOR; print(TEST_SIGNAL_DETECTOR)"` as part of Step 0 smoke test. If it fails, fall back to stubbing the four methods with literal regex patterns from `src/synthesizer.py:2927-2941`.

### RISK-5 (MEDIUM): `error_breakdown` bucket names in `junit_to_pytest_results` may diverge from scorer expectations

**What is uncertain:** The scorer reads `error_breakdown["ModuleNotFoundError"]` and `error_breakdown["ImportError"]` directly (`scorers.py:126-127`). If `junit_to_pytest_results` uses different bucket names (e.g., `"module_not_found"`), the `pass_rate_exclude_code_issues` score will be wrong.

**How to resolve:** Step 1 of the TDD checklist explicitly validates against real on-disk `run_pytest_results.json` files. Use those as golden fixtures. Additionally, unit-test `categorize_error` in isolation against the 21-pattern table from R2.

### RISK-6 (LOW): Disk OOM during repair builds

**What is uncertain:** The repair loop calls `docker build` up to `max_rounds + 1` times per repo. With 12 concurrent workers this could be 36 simultaneous builds. The existing scheduler already checks disk (`disk_low_gb` parameter at `run_rat_benchmark.py:479`), but the repair loop does not check before each attempt.

**How to resolve:** Add a disk-check guard before each `docker build` call in `_repair_and_rescore`. If free disk < 10 GB, log and break. Use `shutil.disk_usage("/")` (stdlib).

### RISK-7 (LOW): `normalize_eval_dockerfile_for_replay` pip-constraints path

**What is uncertain:** `normalize_eval_dockerfile_for_replay` writes `OBSERVED_PIP_CONSTRAINTS_PATH` (`/tmp/jayint-pip-constraints.txt`) to disk and references it in generated `RUN` instructions. This path is expected to exist in the container but may not exist on the host at evaluation time.

**How to resolve:** The function only writes this path to the Dockerfile as a `COPY` or mount hint; the actual write happens inside the docker context. Verify by diffing the Dockerfile before/after a `normalize_eval_dockerfile_for_replay` call and checking whether the path appears in a `COPY` instruction.
