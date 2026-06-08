"""**DEPRECATED (2026-06-08): Superseded by repo2run_repair_port.py / run_rat_benchmark.py:_repair_and_rescore.
Retained and toggled via enable_post_synthesis_repair / --repair-mode. Do not extend.**

Clean-room verification and repair of a synthesized build recipe.

This is the engine behind the agent's post-synthesis self-verify phase. It renders
a *self-contained* Dockerfile from a build recipe (the agent's own Dockerfile only
works where the repo is already present; here we insert a ``git clone`` so it builds
in an empty context), builds it, runs the agent's own verified test command inside
the fresh image, and classifies whether the tests actually executed.

If they did not (the "hollow success" case — image builds but
``ModuleNotFoundError`` on every test), it repairs the *recipe* via
``src.recipe_repair`` (deterministic missing-module install, then LLM) and retries,
up to ``max_rounds`` times.

The verification runs the project's **own** test command — no held-out grader — so
it only catches genuine environment incompleteness, which is the fair kind of
self-check. ``verify_and_repair_recipe`` never raises on Docker/infra failure; it
degrades to a status string so the caller can keep the best-effort artifact.
"""

from __future__ import annotations

import copy
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.recipe_repair import (
    extract_missing_modules,
    repair_recipe_for_missing_modules,
    repair_recipe_with_llm,
)

DOCKER_TIMEOUT_EXIT_CODE = 124

# Only the command buckets matter when deciding whether a repair actually changed the
# recipe — ignore default-key normalization noise (empty lists added by the LLM path).
_CHANGED_COMPARE_KEYS = (
    "build_commands",
    "post_test_patch_commands",
    "runtime_preparation_commands",
    "test_commands",
)


# ─────────────────────────────────────────────────────────────────────────────
# Test-execution shell script + signal classification (ported from repo2run runner)
# ─────────────────────────────────────────────────────────────────────────────

def build_test_execution_script(workdir: str, runtime_commands: List[str], test_command: str) -> str:
    lines = ["set -e", f"cd {shlex.quote(workdir)}"]
    lines.extend(runtime_commands or [])
    lines.extend([
        f"cd {shlex.quote(workdir)}",
        "set +e",
        test_command,
        "TEST_EXIT_CODE=$?",
        "set -e",
        'printf "\\n__SELFVERIFY_TEST_EXIT_CODE__=%s\\n" "$TEST_EXIT_CODE"',
        'exit "$TEST_EXIT_CODE"',
    ])
    return "\n".join(lines) + "\n"


def discover_internal_import_prefixes(workspace_root: Optional[Path]) -> set:
    prefixes = {"src", "tests"}
    if not workspace_root:
        return prefixes
    for candidate_root in (workspace_root, workspace_root / "src"):
        if not candidate_root.is_dir():
            continue
        for child in candidate_root.iterdir():
            if child.name.startswith(".") or not child.is_dir():
                continue
            if (child / "__init__.py").exists():
                prefixes.add(child.name)
    return prefixes


def _output_has_collection_error_signal(output: str) -> bool:
    patterns = [r"ERROR collecting", r"ImportError while importing test module"]
    return any(re.search(p, output, re.IGNORECASE | re.MULTILINE) for p in patterns)


def _output_has_invocation_error_signal(output: str) -> bool:
    patterns = [r"found no collectors for", r"pytest: error:", r"unrecognized arguments:", r"usage: pytest"]
    return any(re.search(p, output, re.IGNORECASE | re.MULTILINE) for p in patterns)


_REAL_TEST_RUN_RE = re.compile(
    r"\b\d+\s+(passed|failed|xpassed|xfailed)\b", re.IGNORECASE
)


def _output_has_real_test_execution_signal(output: str) -> bool:
    """True iff the output shows tests actually RAN (a pytest pass/fail summary line).

    Distinguishes a real run from a ``--collect-only`` invocation, which exits 0 after
    merely *collecting* tests (``collected 15 items``) without executing any. We require
    a ``N passed``/``N failed`` token — ``N errors`` alone is excluded because collection
    /import errors also report ``errors`` without any tests having run.
    """
    return bool(_REAL_TEST_RUN_RE.search(output or ""))


def _output_has_internal_repo_import_error_signal(output: str, prefixes: set) -> bool:
    match = re.search(
        r"ModuleNotFoundError:\s+No module named ['\"]([^'\"]+)['\"]",
        output, re.IGNORECASE | re.MULTILINE,
    )
    if match and match.group(1).split(".", 1)[0] in prefixes:
        return True
    return bool(re.search(
        r"ImportError:\s+cannot import name .* from ['\"][^'\"]+['\"]",
        output, re.IGNORECASE | re.MULTILINE,
    ))


def classify_test_execution(
    command_result: Dict[str, Any],
    detector: Any,
    internal_import_prefixes: Optional[set] = None,
) -> Dict[str, Any]:
    """Decide whether the test command actually executed (``effective``).

    ``detector`` is any object exposing the Synthesizer signal methods
    (``observation_has_effective_test_signal`` etc.).
    """
    output = f"{command_result.get('stdout') or ''}\n{command_result.get('stderr') or ''}".strip()
    prefixes = set(internal_import_prefixes or {"src", "tests"})
    effective_signal = detector.observation_has_effective_test_signal(output)
    empty_signal = detector.observation_has_empty_test_run_signal(output)
    help_signal = detector.observation_looks_like_help_text(output)
    failure_signal = detector.observation_has_test_failure_signal(output)
    invocation_error = _output_has_invocation_error_signal(output)
    collection_error = _output_has_collection_error_signal(output)
    internal_import_error = _output_has_internal_repo_import_error_signal(output, prefixes)

    real_execution = effective_signal or _output_has_real_test_execution_signal(output)

    effective = False
    reason = "tests_did_not_execute"
    if command_result.get("timed_out"):
        reason = "timed_out"
    elif command_result.get("returncode") == 0:
        # rc 0 alone is NOT proof of execution: `pytest --collect-only` and "no tests ran"
        # both exit 0. Require evidence that tests actually ran before crediting a pass —
        # otherwise a collect-only verified command makes self-verify declare a hollow win.
        if real_execution:
            effective, reason = True, "tests_passed"
        else:
            reason = "ran_without_test_execution"
    elif command_result.get("returncode") == 5:
        # pytest exit 5 = zero tests collected/ran. Collecting nothing is not execution.
        reason = "no_tests_collected"
    elif help_signal:
        reason = "help_output"
    elif invocation_error:
        reason = "invocation_error"
    elif collection_error:
        reason = "collection_or_env_error"
    elif internal_import_error:
        reason = "internal_repo_import_error"
    elif empty_signal:
        reason = "empty_test_run"
    elif real_execution:
        # rc != 0 but a pytest summary is present → tests ran and some failed; the
        # environment is correct and the failures are the repo's own code defects.
        effective, reason = True, "tests_ran_with_failures"

    return {
        "effective": effective,
        "reason": reason,
        "effective_signal": effective_signal,
        "failure_signal": failure_signal,
        "empty_signal": empty_signal,
        "help_signal": help_signal,
        "invocation_error_signal": invocation_error,
        "collection_error_signal": collection_error,
        "internal_repo_import_error_signal": internal_import_error,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-contained Dockerfile rendering
# ─────────────────────────────────────────────────────────────────────────────

_CLONE_INSTRUCTION_TEMPLATE = (
    "RUN (command -v git >/dev/null 2>&1 || "
    "(apt-get update && apt-get install -y --no-install-recommends git ca-certificates)) && "
    "git clone {repo_url} {workdir}{checkout}"
)


def make_self_contained_dockerfile(
    dockerfile_text: str,
    repo_url: str,
    base_commit: Optional[str],
    workdir: str,
) -> str:
    """Insert a ``git clone`` of the repo right after the WORKDIR line.

    The agent's rendered Dockerfile assumes the repo already exists at ``workdir``;
    for a clean-room build we clone it in (after WORKDIR creates the dir, before the
    build commands that need it).
    """
    checkout = (
        f" && cd {shlex.quote(workdir)} && git checkout {shlex.quote(base_commit)}"
        if base_commit else ""
    )
    clone = _CLONE_INSTRUCTION_TEMPLATE.format(
        repo_url=shlex.quote(repo_url), workdir=shlex.quote(workdir), checkout=checkout,
    )
    lines = dockerfile_text.splitlines()
    insert_at = None
    for index, line in enumerate(lines):
        if line.strip().upper().startswith("WORKDIR "):
            insert_at = index + 1
            break
    if insert_at is None:
        # No WORKDIR — fall back to after the first FROM.
        for index, line in enumerate(lines):
            if line.strip().upper().startswith("FROM "):
                insert_at = index + 1
                break
    if insert_at is None:
        insert_at = 0
    lines.insert(insert_at, clone)
    return "\n".join(lines) + "\n"


def render_verification_dockerfile(
    synthesizer: Any,
    recipe: Dict[str, Any],
    repo_url: str,
    base_commit: Optional[str],
    workdir: str,
) -> str:
    """Render a buildable, self-contained Dockerfile from a recipe.

    Reuses the synthesizer's own instruction formatting (multiline base64,
    bootstrap blocks) so the verification build matches what the agent emits, then
    makes it self-contained. Saves/restores the synthesizer's instruction list.
    """
    saved_instructions = list(getattr(synthesizer, "instructions", []))
    try:
        synthesizer.instructions = []
        for command in recipe.get("build_commands") or []:
            synthesizer._record_setup_instruction(command)
        with tempfile.TemporaryDirectory() as tmp:
            base_text = synthesizer.generate_dockerfile(file_path=str(Path(tmp) / "Dockerfile"))
    finally:
        synthesizer.instructions = saved_instructions

    if "pytest" not in base_text:
        base_text = base_text.rstrip() + "\nRUN pip install --no-cache-dir pytest\n"
    return make_self_contained_dockerfile(base_text, repo_url, base_commit, workdir)


# ─────────────────────────────────────────────────────────────────────────────
# Docker build / run wrappers (never raise; return structured results)
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: List[str], timeout: int) -> Dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"returncode": proc.returncode, "stdout": proc.stdout,
                "stderr": proc.stderr, "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": DOCKER_TIMEOUT_EXIT_CODE,
                "stdout": exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                "stderr": exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
                "timed_out": True}
    except (OSError, ValueError) as exc:
        return {"returncode": 1, "stdout": "", "stderr": str(exc), "timed_out": False}


def build_image(
    dockerfile_text: str,
    image_tag: str,
    platform: Optional[str] = None,
    timeout: int = 3600,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as ctx:
        (Path(ctx) / "Dockerfile").write_text(dockerfile_text, encoding="utf-8")
        cmd = ["docker", "build"]
        if platform:
            cmd += ["--platform", platform]
        cmd += ["-t", image_tag, ctx]
        return _run(cmd, timeout)


def run_test_command_in_image(
    image_tag: str,
    workdir: str,
    runtime_commands: List[str],
    test_command: str,
    platform: Optional[str] = None,
    timeout: int = 600,
) -> Dict[str, Any]:
    script = build_test_execution_script(workdir, runtime_commands, test_command)
    cmd = ["docker", "run", "--rm"]
    if platform:
        cmd += ["--platform", platform]
    cmd += [image_tag, "bash", "-lc", script]
    return _run(cmd, timeout)


def remove_image(image_tag: str) -> None:
    subprocess.run(["docker", "rmi", "-f", image_tag],
                   capture_output=True, text=True, check=False)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator: verify → repair → re-verify
# ─────────────────────────────────────────────────────────────────────────────

def _apply_repair(
    recipe: Dict[str, Any],
    failure: Dict[str, Any],
    workspace_root: Optional[Path],
    client: Any,
    model: str,
    repo_url: str,
    test_commands: List[str],
) -> tuple:
    """Try deterministic missing-module repair first, then LLM. Returns (recipe, applied|None)."""
    missing = failure.get("missing_modules") or []
    if missing:
        repaired, installed = repair_recipe_for_missing_modules(recipe, missing, workspace_root)
        if installed:
            return repaired, {"type": "deterministic", "installed": installed}
    if client and model:
        repaired, record = repair_recipe_with_llm(
            client, model, recipe, failure, workspace_root,
            repo_url=repo_url, verified_test_commands=test_commands,
        )
        if repaired is not None and repaired != recipe:
            return repaired, {"type": "llm", "rationale": record.get("rationale"),
                              "confidence": record.get("confidence"), "usage": record.get("usage"),
                              "error": record.get("error")}
    return recipe, None


def verify_and_repair_recipe(
    *,
    recipe: Dict[str, Any],
    synthesizer: Any,
    repo_url: str,
    base_commit: Optional[str],
    workdir: str,
    verified_test_command: Optional[str],
    runtime_preparation_commands: Optional[List[str]],
    workspace_root: Optional[str],
    client: Any,
    model: str,
    image_tag: str,
    platform: Optional[str] = None,
    max_rounds: int = 2,
    build_timeout: int = 3600,
    test_timeout: int = 600,
    logger: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """Build the recipe in a clean room, run its test command, repair if hollow.

    Returns ``{status, recipe, changed, rounds}``. ``status`` is one of:
    ``resolved`` | ``unresolved`` | ``build_failed`` | ``render_failed`` |
    ``skipped_no_test_command``. Never raises.
    """
    if not verified_test_command:
        return {"status": "skipped_no_test_command", "recipe": recipe, "changed": False, "rounds": []}

    ws = Path(workspace_root) if workspace_root else None
    prefixes = discover_internal_import_prefixes(ws)
    runtime_commands = runtime_preparation_commands or []
    current = copy.deepcopy(recipe or {})
    original = copy.deepcopy(recipe or {})
    rounds: List[Dict[str, Any]] = []
    status = "unresolved"

    for round_index in range(max_rounds + 1):
        logger(f"[Self-Verify] Round {round_index}: building clean-room image…")
        try:
            dockerfile_text = render_verification_dockerfile(
                synthesizer, current, repo_url, base_commit, workdir,
            )
        except Exception as exc:  # noqa: BLE001
            logger(f"[Self-Verify] Render failed: {exc}; keeping current artifact.")
            status = "render_failed"
            break

        build = build_image(dockerfile_text, image_tag, platform, build_timeout)
        if build.get("timed_out") or build.get("returncode") != 0:
            failure = {"stage": "build", "returncode": build.get("returncode"),
                       "reason": "build_failed", "stdout": build.get("stdout", ""),
                       "stderr": build.get("stderr", ""), "missing_modules": []}
            rounds.append({"round": round_index, "stage": "build", "resolved": False,
                           "returncode": build.get("returncode")})
            logger(f"[Self-Verify] Build failed (rc={build.get('returncode')}).")
            remove_image(image_tag)
            if round_index >= max_rounds:
                status = "build_failed"
                break
            current, applied = _apply_repair(current, failure, ws, client, model, repo_url,
                                              [verified_test_command])
            rounds[-1]["repair"] = applied
            if not applied:
                status = "build_failed"
                break
            continue

        try:
            test = run_test_command_in_image(image_tag, workdir, runtime_commands,
                                             verified_test_command, platform, test_timeout)
            classification = classify_test_execution(test, synthesizer, prefixes)
        except Exception as exc:  # noqa: BLE001 — honor the never-raises contract
            logger(f"[Self-Verify] Test run/classify failed: {exc}; treating as unresolved.")
            remove_image(image_tag)
            status = "unresolved"
            break
        remove_image(image_tag)

        round_record = {"round": round_index, "stage": "test", "resolved": classification["effective"],
                        "reason": classification["reason"], "returncode": test.get("returncode")}
        if classification["effective"]:
            rounds.append(round_record)
            status = "resolved"
            logger(f"[Self-Verify] Round {round_index}: tests executed ({classification['reason']}). Done.")
            break

        missing = extract_missing_modules(f"{test.get('stdout','')}\n{test.get('stderr','')}")
        round_record["missing_modules"] = missing
        logger(f"[Self-Verify] Round {round_index}: tests did not execute "
               f"({classification['reason']}); missing={missing}.")
        if round_index >= max_rounds:
            rounds.append(round_record)
            status = "unresolved"
            break

        failure = {"stage": "test", "returncode": test.get("returncode"),
                   "reason": classification["reason"], "stdout": test.get("stdout", ""),
                   "stderr": test.get("stderr", ""), "missing_modules": missing}
        current, applied = _apply_repair(current, failure, ws, client, model, repo_url,
                                         [verified_test_command])
        round_record["repair"] = applied
        rounds.append(round_record)
        if not applied:
            status = "unresolved"
            logger(f"[Self-Verify] Round {round_index}: no repair available; stopping.")
            break
        logger(f"[Self-Verify] Round {round_index}: applied {applied['type']} repair; retrying.")

    changed = any(current.get(k) != original.get(k) for k in _CHANGED_COMPARE_KEYS)
    return {"status": status, "recipe": current, "changed": changed, "rounds": rounds}
