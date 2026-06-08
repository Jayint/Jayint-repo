"""Recipe-level build-recipe repair.

Shared logic for repairing a synthesized *build recipe* (the structured source of
truth that renders the Dockerfile) after a clean-room build/test reveals the
environment is incomplete. Two tiers:

1. ``repair_recipe_for_missing_modules`` — deterministic, zero-LLM. Reads
   ``ModuleNotFoundError`` / ``ImportError`` signals, resolves each missing module
   to a pinned requirement (preferring the version declared in the repo's own
   requirements/lock files), and appends ``pip install`` commands to the recipe's
   ``build_commands``. Catches the dominant "hollow success" failure mode.
2. ``repair_recipe_with_llm`` — sends the failing recipe plus build/test logs and
   bounded project-config evidence to the LLM and returns a repaired recipe.

Both repair the recipe, never the rendered Dockerfile text, so every downstream
consumer (the agent's emitted Dockerfile, the RAT adapter, the repo2run runner)
inherits the same fix. All functions are pure: they return new recipe dicts and
never mutate their inputs.
"""

from __future__ import annotations

import copy
import json
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Recipe schema
# ─────────────────────────────────────────────────────────────────────────────

RECIPE_COMMAND_KEYS = (
    "build_commands",
    "post_test_patch_commands",
    "runtime_preparation_commands",
    "test_commands",
)

RECIPE_REPAIR_SYSTEM_PROMPT = """You are a build-artifact repair module for an environment setup agent.

The setup agent already found a working interactive container state. Your job is to repair the structured build recipe so a fresh Docker image can reproduce that state and run the project's own test suite.

Return JSON only. Do not write Markdown.

Rules:
1. Repair the recipe fields only. Do not invent benchmark test patches.
2. Keep commands reproducible from a clean Docker build after the repository is cloned and checked out at the working directory.
3. Put persistent setup in `build_commands`.
4. Put commands that must run after any test patch is applied in `post_test_patch_commands`.
5. Put daemon starts, database/service initialization, and commands that must run immediately before tests in `runtime_preparation_commands`.
6. Put final test-facing commands in `test_commands`.
7. Do not include read-only diagnostics, failed commands, or broad fallback test commands that are not needed once a target-covering command exists.
8. If the failure is caused by an incomplete recipe (e.g. a missing dependency or a dropped editable install), add the missing setup command rather than weakening the test.
9. If the failure is caused by stale/wrong test selection, adjust `test_commands` to a command that definitely runs the project's tests.
10. Preserve commands from the current recipe that are still necessary.
11. Use `project_config_context` as the highest-priority dependency and test-runner evidence. Prefer pinned versions and commands from tox/CI/lock/config files over broad latest-version installs.
12. If logs show framework/plugin/runtime incompatibility, repair dependency pins first. Do not diagnose a path mismatch unless the logs or config prove that different source trees are being used.
13. Keep all paths consistent with the working directory the agent used; do not rewrite paths unless a concrete path error is proven by the logs.
14. If tests must exercise the project's own code, install or load the local project (e.g. `pip install -e .`). Do not install the same project from PyPI in a way that shadows the local source.
15. Treat exact dependency pins from project config as higher-priority evidence than broad ranges or latest-version installs. Do not mix an old framework pin with unpinned companion packages that can upgrade it.

Required JSON keys:
`build_commands`, `post_test_patch_commands`, `runtime_preparation_commands`, `test_commands`, `excluded_commands`, `rationale`, `confidence`.

`confidence` must be one of: "high", "medium", "low".
"""

# ─────────────────────────────────────────────────────────────────────────────
# Missing-module detection
# ─────────────────────────────────────────────────────────────────────────────

_MISSING_PYTHON_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s+No module named ['\"](?P<module>[^'\"]+)['\"]"
)

# Modules whose import name does not match a trivially-installable distribution.
_KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS: Dict[str, Tuple[str, str]] = {
    "ppocr": ("paddleocr", "paddleocr==2.7.3"),
    "ppstructure": ("paddleocr", "paddleocr==2.7.3"),
}


def extract_missing_modules(text: str) -> List[str]:
    """Return de-duplicated top-level module names from ModuleNotFound/Import errors."""
    modules: List[str] = []
    seen: set = set()
    for match in _MISSING_PYTHON_MODULE_RE.finditer(str(text or "")):
        module = (match.group("module") or "").split(".", 1)[0].strip()
        if module and module not in seen:
            seen.add(module)
            modules.append(module)
    return modules


# ─────────────────────────────────────────────────────────────────────────────
# pip name normalization (kept consistent with run_repo2run_benchmark.py)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_pip_constraint_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


def _pip_requirement_name(requirement: str) -> str:
    token = str(requirement or "").strip()
    if not token or token.startswith(("-", ".", "/", "git+", "http://", "https://")):
        return ""
    name = re.split(r"\[|==|!=|~=|>=|<=|>|<|@", token, maxsplit=1)[0].strip()
    return name.lower().replace("_", "-")


def _strip_requirement_line(line: str) -> str:
    stripped = (line or "").strip()
    if not stripped or stripped.startswith("#"):
        return ""
    if " #" in stripped:
        stripped = stripped.split(" #", 1)[0].strip()
    return stripped


def _find_declared_requirement_in_workspace(
    workspace_root: Optional[Path],
    package_name: str,
) -> Optional[str]:
    """Return the exact requirement line declared for ``package_name`` in repo config."""
    normalized_name = _normalize_pip_constraint_name(package_name)
    if not workspace_root or not normalized_name or not workspace_root.exists():
        return None

    inspected = 0
    for path in sorted(workspace_root.rglob("*requirements*.txt")):
        inspected += 1
        if inspected > 100:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            requirement = _strip_requirement_line(line)
            if _normalize_pip_constraint_name(_pip_requirement_name(requirement)) == normalized_name:
                return requirement

    lock_inspected = 0
    package_pattern = re.compile(
        r"(?ms)^\[\[package\]\]\s*.*?^name\s*=\s*\""
        + re.escape(package_name)
        + r"\"\s*$.*?^version\s*=\s*\"(?P<version>[^\"]+)\"",
    )
    for path in sorted(workspace_root.rglob("poetry.lock")):
        lock_inspected += 1
        if lock_inspected > 20:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = package_pattern.search(text)
        if match:
            return f"{package_name}=={match.group('version')}"
    return None


def requirement_for_missing_module(
    module: str,
    workspace_root: Optional[Path],
) -> Optional[str]:
    """Resolve a missing import name to an installable requirement string."""
    module_name = _normalize_pip_constraint_name((module or "").split(".", 1)[0])
    if not module_name:
        return None

    candidate_package_names: List[str] = []
    fallback_requirement = module_name
    if module_name in _KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS:
        package_name, fallback_requirement = _KNOWN_MISSING_MODULE_PACKAGE_FALLBACKS[module_name]
        candidate_package_names.append(package_name)
    candidate_package_names.append(module_name)

    for package_name in candidate_package_names:
        declared = _find_declared_requirement_in_workspace(workspace_root, package_name)
        if declared:
            return declared
    return fallback_requirement


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic repair (recipe-level)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_commands(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _recipe_already_installs(build_commands: List[str], requirement: str) -> bool:
    """True if any build command already pip-installs ``requirement`` (by name)."""
    requirement_name = _pip_requirement_name(requirement)
    if not requirement_name:
        return True  # un-nameable (path/url/editable) — assume present, do not duplicate
    for command in build_commands:
        lowered = command.lower()
        if "pip" not in lowered or "install" not in lowered:
            continue
        # token-level name match to avoid substring false positives (e.g. "redis" in "redis-py")
        for token in re.split(r"[\s'\"]+", command):
            if _pip_requirement_name(token) == requirement_name:
                return True
    return False


def repair_recipe_for_missing_modules(
    recipe: Dict[str, Any],
    missing_modules: List[str],
    workspace_root: Optional[Path] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Append pip installs for missing modules to ``build_commands``.

    Returns ``(new_recipe, installed_requirements)``. Does not mutate ``recipe``.
    """
    new_recipe = copy.deepcopy(recipe or {})
    build_commands = _normalize_commands(new_recipe.get("build_commands"))
    installed: List[str] = []

    for module in missing_modules:
        requirement = requirement_for_missing_module(module, workspace_root)
        if not requirement:
            continue
        # build_commands grows as we append, so this also dedupes within this call.
        if _recipe_already_installs(build_commands, requirement):
            continue
        build_commands.append(f"pip install {shlex.quote(requirement)}")
        installed.append(requirement)

    new_recipe["build_commands"] = build_commands
    return new_recipe, installed


# ─────────────────────────────────────────────────────────────────────────────
# LLM repair (recipe-level)
# ─────────────────────────────────────────────────────────────────────────────

_REPAIR_CONTEXT_FILE_CANDIDATES = (
    "tox.ini",
    "setup.cfg",
    "setup.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "Pipfile",
    "poetry.lock",
    "Makefile",
    ".travis.yml",
)
_REPAIR_CONTEXT_MAX_FILE_CHARS = 6000
_REPAIR_CONTEXT_MAX_TOTAL_CHARS = 24000


def collect_project_config_context(workspace_root: Optional[Path]) -> Dict[str, str]:
    """Collect bounded snippets of dependency/test-runner config files for repair."""
    if not workspace_root:
        return {}
    root = Path(workspace_root)
    if not root.exists():
        return {}
    context: Dict[str, str] = {}
    remaining = _REPAIR_CONTEXT_MAX_TOTAL_CHARS
    for rel in _REPAIR_CONTEXT_FILE_CANDIDATES:
        if remaining <= 0:
            break
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        snippet = text[:_REPAIR_CONTEXT_MAX_FILE_CHARS]
        snippet = snippet[:remaining]
        if not snippet.strip():
            continue
        context[rel] = snippet
        remaining -= len(snippet)
    return context


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} chars truncated) ..."


def build_recipe_repair_input(
    recipe: Dict[str, Any],
    failure: Dict[str, Any],
    workspace_root: Optional[Path],
    repo_url: str = "",
    verified_test_commands: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assemble the JSON payload handed to the repair LLM."""
    return {
        "repo_url": repo_url,
        "current_recipe": recipe or {},
        "verified_test_commands": verified_test_commands or [],
        "project_config_context": collect_project_config_context(workspace_root),
        "failure": {
            "stage": failure.get("stage"),
            "returncode": failure.get("returncode"),
            "reason": failure.get("reason"),
            "missing_modules": failure.get("missing_modules") or [],
            "stdout_tail": _truncate(failure.get("stdout", ""), 6000),
            "stderr_tail": _truncate(failure.get("stderr", ""), 6000),
        },
    }


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    content = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    if fenced:
        content = fenced.group(1)
    start = content.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(content[start:index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def normalize_repaired_recipe(
    candidate: Optional[Dict[str, Any]],
    current_recipe: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Overlay the LLM's returned command buckets onto the current recipe.

    Returns None if the result has no test command (a repair must never drop it).
    """
    if not isinstance(candidate, dict):
        return None
    repaired = copy.deepcopy(current_recipe or {})
    for key in (*RECIPE_COMMAND_KEYS, "excluded_commands"):
        if key in candidate:
            value = candidate.get(key)
            if key == "excluded_commands":
                repaired[key] = value if isinstance(value, list) else []
            else:
                repaired[key] = _normalize_commands(value)
    repaired["rationale"] = str(candidate.get("rationale") or repaired.get("rationale") or "")
    confidence = str(candidate.get("confidence") or repaired.get("confidence") or "low").lower()
    repaired["confidence"] = confidence if confidence in {"high", "medium", "low"} else "low"

    if not _normalize_commands(repaired.get("test_commands")):
        return None
    for key in RECIPE_COMMAND_KEYS:
        repaired.setdefault(key, [])
    repaired.setdefault("excluded_commands", [])
    return repaired


def repair_recipe_with_llm(
    client: Any,
    model: str,
    recipe: Dict[str, Any],
    failure: Dict[str, Any],
    workspace_root: Optional[Path] = None,
    repo_url: str = "",
    verified_test_commands: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Ask the LLM to repair the recipe. Returns ``(repaired_recipe_or_None, record)``."""
    repair_input = build_recipe_repair_input(
        recipe, failure, workspace_root,
        repo_url=repo_url, verified_test_commands=verified_test_commands,
    )
    messages = [
        {"role": "system", "content": RECIPE_REPAIR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Repair this build recipe.\n\nInput JSON:\n```json\n"
            + json.dumps(repair_input, indent=2, ensure_ascii=False)
            + "\n```",
        },
    ]
    raw_content = ""
    usage: Dict[str, Any] = {}
    error: Optional[str] = None
    parsed: Optional[Dict[str, Any]] = None
    try:
        response = client.chat.completions.create(model=model, messages=messages, temperature=0)
        raw_content = response.choices[0].message.content or ""
        usage_obj = getattr(response, "usage", None)
        if usage_obj:
            usage = {
                "input_tokens": getattr(usage_obj, "prompt_tokens", 0),
                "output_tokens": getattr(usage_obj, "completion_tokens", 0),
                "total_tokens": getattr(usage_obj, "total_tokens", 0),
            }
        parsed = normalize_repaired_recipe(_extract_json_object(raw_content), recipe)
    except Exception as exc:  # noqa: BLE001 — surfaced in the record, never raised
        error = str(exc)

    record = {
        "source": "llm",
        "error": error,
        "usage": usage,
        "rationale": (parsed or {}).get("rationale", ""),
        "confidence": (parsed or {}).get("confidence", "low"),
        "recipe": parsed,
    }
    return parsed, record
