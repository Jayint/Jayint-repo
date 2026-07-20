"""Runtime-feedback classifier (design 2026-06-26 §5, §6, §10).

Pure module — no src.envstate imports. Unit-testable with plain strings.

``classify_observation(command, output) -> Discovery | None``

Tries four sub-classifiers in priority order (spec §6) and returns the first
non-None hit.  Returns None for ignored observations (build-time install
failures, assertion errors, and anything not requirement-bearing).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import re
import shlex

from python_deps.depgraph.schema import Ecosystem, Layer, NodeType
from python_deps.failure_classifier import classify_dependency_failure


# ---------------------------------------------------------------------------
# Data structures (spec §10)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Observation:
    command: str
    output: str   # combined stdout/stderr text from the ledger event


@dataclass(frozen=True)
class Discovery:
    node_type: NodeType           # PACKAGE | SYSTEM_LIB | TOOL | CONFIG | SERVICE
    name: str                     # dist / soname / tool / VAR / service-kind
    layer: Layer
    evidence: str                 # failure excerpt that revealed the requirement
    check_command: str | None     # None only for SERVICE (advisory)
    confidence: str = "runtime-deterministic"
    data: dict = field(default_factory=dict)
    requires_of: str | None = None   # owner node id this is a dependency OF (spec §7)
    ecosystem: Ecosystem | None = None
    workspace: str | None = None
    package_manager: str | None = None


# ---------------------------------------------------------------------------
# Ignored failure_type values from classify_dependency_failure (spec §6).
#
# These are genuine build-time / unrelated DEP failures owned elsewhere.
# CRITICAL: "not_dependency_related" is deliberately NOT in this set.
# classify_dependency_failure returns "not_dependency_related" for ANY non-dep
# failure — including the very connection-refused / KeyError / command-not-found
# shapes the Service/Config/Tool classifiers handle. If we short-circuited on it,
# priorities 2/3/4 would never run and 3 of the 5 classes would be silently
# dropped. It therefore FALLS THROUGH to the service→config→tool chain; the
# dispatcher returns None only at the very end, when nothing matched.
# ---------------------------------------------------------------------------
_IGNORED_FAILURE_TYPES: frozenset[str] = frozenset({
    "no_matching_distribution",
    "dependency_conflict",
    "syntax_requires_newer_python",
})


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def classify_observation(command: str, output: str) -> Discovery | None:
    """Classify one (command, output) observation.  Returns Discovery or None.

    Priority order (spec §6):
      1. classify_dependency_failure  — Package (module/import) or SystemLib
      2. classify_service_error       — Service
      3. classify_config_error        — Config
      4. classify_tool_error          — Tool
    """
    text = output or ""

    polyglot = _classify_polyglot_dependency_failure(command, text)
    if polyglot is not None:
        return polyglot

    # ── Priority 1: python import / native-lib failures ──────────────────
    dep = classify_dependency_failure(command, text)
    if dep.failure_type == "module_not_found":
        from python_deps.import_mapping import map_import_to_package
        import_name = dep.import_name or ""
        pkg_name = map_import_to_package(import_name).package_name
        return Discovery(
            node_type=NodeType.PACKAGE,
            name=pkg_name,
            layer=Layer.PIP,
            evidence=dep.message[:500],
            check_command=f'python3 -c "import {import_name}"',
            data={"import_name": import_name},
        )
    if dep.failure_type == "import_name_error":
        from python_deps.import_mapping import map_import_to_package
        import_name = dep.import_name or ""
        pkg_name = map_import_to_package(import_name).package_name
        return Discovery(
            node_type=NodeType.PACKAGE,
            name=pkg_name,
            layer=Layer.PIP,
            evidence=dep.message[:500],
            check_command=f'python3 -c "import {import_name}"',
            data={"import_name": import_name},
        )
    if dep.failure_type == "native_library_missing":
        soname = dep.details.get("library", "")
        return Discovery(
            node_type=NodeType.SYSTEM_LIB,
            name=soname,
            layer=Layer.SYSTEM,
            evidence=dep.message[:500],
            check_command=f"ldconfig -p | grep -q {soname}",
        )
    if dep.failure_type in _IGNORED_FAILURE_TYPES:
        return None

    # dep.failure_type == "not_dependency_related" is NOT ignored — it only means
    # the dep classifier did not match, so we FALL THROUGH to the service→config→
    # tool classifiers below. Returning None here would silently drop 3 of 5 classes.

    # ── Priority 2: service connection failures ───────────────────────────
    from python_deps.depgraph.service_scan import classify_service_error
    svc_kind = classify_service_error(text)
    if svc_kind is not None:
        # A runtime connection refusal is stronger than a package-based service
        # hint.  Promote only services for which we have a curated, in-image
        # recipe; all others preserve the historical advisory behaviour.
        from python_deps.depgraph.service_tables import in_image_service_recipe
        recipe = in_image_service_recipe(svc_kind)
        if recipe is not None:
            check_command = recipe.pop("check")
            data = {
                "service_confidence": "confirmed",
                "start_recipe": recipe,
            }
        else:
            check_command = None
            data = {}
        return Discovery(
            node_type=NodeType.SERVICE,
            name=svc_kind,
            layer=Layer.SERVICES,
            evidence=text[:500],
            check_command=check_command,
            data=data,
        )

    # ── Priority 3: missing config / env-var ─────────────────────────────
    from python_deps.failure_classifier import classify_config_error
    var_name = classify_config_error(command, text)
    if var_name is not None:
        return Discovery(
            node_type=NodeType.CONFIG,
            name=var_name,
            layer=Layer.CONFIG,
            evidence=text[:500],
            check_command=f"printenv {var_name}",
        )

    # ── Priority 4: missing tool / executable ────────────────────────────
    from python_deps.failure_classifier import (
        classify_apt_install_hint,
        classify_tool_error,
    )
    apt_package = classify_apt_install_hint(text)
    if apt_package is not None:
        return Discovery(
            node_type=NodeType.SYSTEM_LIB,
            name=apt_package,
            layer=Layer.SYSTEM,
            evidence=text[:500],
            check_command=f"dpkg -s {apt_package} >/dev/null 2>&1",
            data={"apt_package": apt_package},
        )
    tool_name = classify_tool_error(command, text)
    if tool_name is not None:
        from python_deps.depgraph.tables import apt_for_tool
        apt_package = apt_for_tool(tool_name)
        return Discovery(
            node_type=NodeType.TOOL,
            name=tool_name,
            layer=Layer.TOOLCHAIN,
            evidence=text[:500],
            check_command=f"command -v {tool_name}",
            data={"apt_package": apt_package} if apt_package else {},
        )

    return None


def _command_workspace(command: str) -> str:
    match = re.search(r"(?:^|[;&]\s*)cd\s+((?:'[^']*'|\"[^\"]*\"|[^\s;&]+))\s*&&", command or "")
    if not match:
        return "."
    try:
        value = shlex.split(match.group(1))[0]
    except (ValueError, IndexError):
        value = match.group(1).strip("'\"")
    return value.strip("/") or "."


def _dependency_discovery(
    command: str,
    text: str,
    *,
    ecosystem: Ecosystem,
    package_manager: str,
    pattern: str,
    check: str,
) -> Discovery | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    workspace = _command_workspace(command)
    missing = next((group for group in match.groups() if group), "")
    if missing and package_manager in {"npm", "pnpm", "yarn"}:
        check = f"node -e {shlex.quote(f'require.resolve({missing!r})')}"
    elif missing and package_manager == "go":
        check = f"go list {shlex.quote(missing)}"
    return Discovery(
        node_type=NodeType.DEPENDENCY_SET,
        name=f"{package_manager} dependencies ({workspace})",
        layer=Layer.DEPENDENCIES,
        evidence=(match.group(0) or text)[:500],
        check_command=(
            check if workspace == "." else f"cd {shlex.quote(workspace)} && {check}"
        ),
        data={"missing_dependency": missing} if missing else {},
        ecosystem=ecosystem,
        workspace=workspace,
        package_manager=package_manager,
    )


def _classify_polyglot_dependency_failure(
    command: str,
    text: str,
) -> Discovery | None:
    """Map native package-manager failures to their workspace transaction."""
    lowered_command = (command or "").lower()
    candidates = []
    if any(token in lowered_command for token in ("npm ", "pnpm ", "yarn ", "node ")):
        candidates.append((
            Ecosystem.NPM,
            "npm" if "npm " in lowered_command else (
                "pnpm" if "pnpm " in lowered_command else "yarn"
            ),
            r"(?:cannot find (?:module|package)|err_module_not_found).*?['\"]([^'\"]+)['\"]",
        ))
    if "cargo " in lowered_command:
        candidates.append((
            Ecosystem.CARGO,
            "cargo",
            r"(?:no matching package named|failed to (?:select|get)|can't find crate)[^\n`'\"]*[`'\"]?([A-Za-z0-9_.-]+)?",
        ))
    if re.search(r"\bgo\s+(?:test|build|list|run|mod)\b", lowered_command):
        candidates.append((
            Ecosystem.GO_MODULE,
            "go",
            r"(?:missing go\.sum entry for module providing package|no required module provides package)\s+([^\s;]+)",
        ))
    if "mvn " in lowered_command or "mvnw " in lowered_command:
        candidates.append((
            Ecosystem.MAVEN,
            "maven",
            r"(?:could not resolve dependencies|could not find artifact)\s*([^\s]*)",
        ))
    if "gradle " in lowered_command or "gradlew " in lowered_command:
        candidates.append((
            Ecosystem.GRADLE,
            "gradle",
            r"(?:could not resolve all files|could not find)\s*([A-Za-z0-9_.:-]*)",
        ))
    checks = {
        "npm": "npm ls --depth=0",
        "pnpm": "pnpm list --depth 0",
        "yarn": "yarn list --depth=0",
        "cargo": "cargo metadata --format-version 1",
        "go": "go mod verify",
        "maven": "mvn -B -o -DskipTests dependency:resolve",
        "gradle": "./gradlew --offline dependencies",
    }
    for ecosystem, manager, pattern in candidates:
        discovery = _dependency_discovery(
            command,
            text,
            ecosystem=ecosystem,
            package_manager=manager,
            pattern=pattern,
            check=checks[manager],
        )
        if discovery is not None:
            return discovery
    return None
