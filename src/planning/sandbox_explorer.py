import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from src.planning.schemas import EnvironmentBuildPlan, TaskEdge, TaskNode
from src.planning.todo_generator import TodoListGenerator
from src.planning.topo_sorter import TopologicalSorter


@dataclass
class SandboxProbeResult:
    name: str
    command: str
    success: bool
    output: str
    round_index: int = 1

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "command": self.command,
            "success": self.success,
            "output": self.output,
            "round_index": self.round_index,
        }


@dataclass
class SandboxExplorationResult:
    probes: List[SandboxProbeResult]
    findings: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "probes": [probe.to_dict() for probe in self.probes],
            "findings": dict(self.findings),
        }


class SandboxPlanningExplorer:
    MAX_PROBE_OUTPUT_CHARS = 18000
    MAX_FINDING_ITEMS = 12
    MAX_PROBE_ROUNDS = 3

    def __init__(self):
        self.topo_sorter = TopologicalSorter()
        self.todo_generator = TodoListGenerator()
        self.last_exploration = None

    def explore(self, plan: EnvironmentBuildPlan, sandbox, log_dir: str = None) -> EnvironmentBuildPlan:
        self.last_exploration = None
        if plan.repo_summary.get("primary_language") != "python":
            return plan

        probe_results = []
        executed_probe_names = set()
        pending_probes = self._initial_python_probe_commands()
        round_index = 1

        while pending_probes and round_index <= self.MAX_PROBE_ROUNDS:
            for name, command in pending_probes:
                if name in executed_probe_names:
                    continue
                executed_probe_names.add(name)
                success, output = self._run_probe(sandbox, command)
                probe_results.append(
                    SandboxProbeResult(
                        name=name,
                        command=command,
                        success=success,
                        output=self._truncate(output),
                        round_index=round_index,
                    )
                )

            findings = self._extract_findings(probe_results)
            pending_probes = self._next_python_probe_commands(
                plan,
                findings,
                executed_probe_names,
                round_index=round_index + 1,
            )
            round_index += 1

        exploration = SandboxExplorationResult(
            probes=probe_results,
            findings=self._extract_findings(probe_results),
        )
        self.last_exploration = exploration
        self._write_exploration_log(log_dir, exploration)
        self._apply_findings(plan, exploration)
        return plan

    def _run_probe(self, sandbox, command: str):
        if hasattr(sandbox, "inspect"):
            return sandbox.inspect(command)
        return sandbox.execute(command)

    def _initial_python_probe_commands(self) -> List[Tuple[str, str]]:
        return [
            ("runtime", self._runtime_probe()),
            ("pyproject", self._pyproject_probe()),
            ("poetry_lock", self._poetry_lock_probe()),
            ("import_scan", self._import_scan_probe()),
        ]

    def _next_python_probe_commands(
        self,
        plan: EnvironmentBuildPlan,
        findings: Dict[str, object],
        executed_probe_names: set,
        round_index: int,
    ) -> List[Tuple[str, str]]:
        probes = []
        pytest_config = findings.get("pytest_config") or []
        platform_deps = findings.get("platform_specific_dependencies") or []
        pyproject_deps = findings.get("pyproject_dependencies") or []
        package_manager = plan.repo_summary.get("package_manager")

        if (
            round_index <= 2
            and "focused_import_scan" not in executed_probe_names
            and (pytest_config or pyproject_deps)
        ):
            probes.append(("focused_import_scan", self._focused_import_scan_probe()))

        if (
            round_index <= 2
            and "dependency_strategy" not in executed_probe_names
            and package_manager == "poetry"
            and (platform_deps or pyproject_deps)
        ):
            probes.append(("dependency_strategy", self._dependency_strategy_probe()))

        return probes

    def _runtime_probe(self) -> str:
        return """# repo2run-planning-probe: runtime
python - <<'PY'
import importlib.util
import platform
import shutil
import sys

print("RUNTIME_PYTHON", sys.version.split()[0])
print("RUNTIME_EXECUTABLE", sys.executable)
print("RUNTIME_PLATFORM", platform.system(), platform.machine(), platform.release())
print("TOOL_AVAILABLE poetry", bool(shutil.which("poetry")))
print("TOOL_AVAILABLE pytest", bool(shutil.which("pytest")))
print("PYTHON_MODULE_AVAILABLE pkg_resources", importlib.util.find_spec("pkg_resources") is not None)
PY"""

    def _pyproject_probe(self) -> str:
        return r"""# repo2run-planning-probe: pyproject
python - <<'PY'
from pathlib import Path
import json
import re
try:
    import tomllib
except ImportError:
    tomllib = None

path = Path("pyproject.toml")
if not path.exists():
    print("PYPROJECT_PRESENT false")
    raise SystemExit(0)

text = path.read_text(encoding="utf-8", errors="replace")
print("PYPROJECT_PRESENT true")

PLATFORM_MARKERS = (
    "pyobjc", "eventkit", "foundation", "objc", "appscript", "pywin32",
    "sys_platform", "platform_system", "darwin", "win32", "macos"
)

def compact(value):
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, sort_keys=True)
    else:
        raw = str(value)
    return re.sub(r"\s+", " ", raw).strip()[:260]

def dep_name_from_requirement(requirement):
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", str(requirement))
    return match.group(1) if match else ""

def emit_dep(group, name, value):
    if not name or str(name).lower() == "python":
        return
    detail = compact(value)
    print("PYPROJECT_DEP", group, name, detail)
    lowered = f"{name} {detail}".lower()
    if any(marker in lowered for marker in PLATFORM_MARKERS):
        print("PYPROJECT_PLATFORM_DEP", group, name, detail)

data = None
if tomllib is not None:
    try:
        data = tomllib.loads(text)
    except Exception as exc:
        print("PYPROJECT_PARSE_ERROR", type(exc).__name__, str(exc)[:160])

if data:
    build_backend = (data.get("build-system") or {}).get("build-backend")
    if build_backend:
        print("PYPROJECT_BUILD_BACKEND", build_backend)

    project = data.get("project") or {}
    if project.get("requires-python"):
        print("PYPROJECT_REQUIRES_PYTHON", project.get("requires-python"))
    for requirement in project.get("dependencies") or []:
        emit_dep("project", dep_name_from_requirement(requirement), requirement)
    for group, requirements in (project.get("optional-dependencies") or {}).items():
        for requirement in requirements or []:
            emit_dep(f"optional:{group}", dep_name_from_requirement(requirement), requirement)

    poetry = ((data.get("tool") or {}).get("poetry") or {})
    poetry_deps = poetry.get("dependencies") or {}
    if poetry_deps.get("python"):
        print("PYPROJECT_POETRY_PYTHON", compact(poetry_deps.get("python")))
    for name, value in poetry_deps.items():
        emit_dep("main", name, value)
    for group, group_payload in (poetry.get("group") or {}).items():
        for name, value in (group_payload.get("dependencies") or {}).items():
            emit_dep(group, name, value)

    pytest_config = (((data.get("tool") or {}).get("pytest") or {}).get("ini_options") or {})
    for key in ("addopts", "testpaths", "python_files"):
        if key in pytest_config:
            print("PYTEST_CONFIG", key, compact(pytest_config[key]))
else:
    backend = re.search(r'^\s*build-backend\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if backend:
        print("PYPROJECT_BUILD_BACKEND", backend.group(1))
    requires_python = re.search(r'^\s*requires-python\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if requires_python:
        print("PYPROJECT_REQUIRES_PYTHON", requires_python.group(1))
    poetry_python = re.search(r'^\s*python\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if poetry_python:
        print("PYPROJECT_POETRY_PYTHON", poetry_python.group(1))

    project_section = re.search(r'^\[project\](.*?)(?=^\[|\Z)', text, re.M | re.S)
    if project_section:
        deps_match = re.search(r'(?ms)^\s*dependencies\s*=\s*\[(.*?)\]', project_section.group(1))
        if deps_match:
            for requirement in re.findall(r'["\']([^"\']+)["\']', deps_match.group(1)):
                emit_dep("project", dep_name_from_requirement(requirement), requirement)

    current_section = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        section_match = re.match(r'^\[(.+)\]$', line)
        if section_match:
            current_section = section_match.group(1)
            continue
        if not current_section:
            continue

        group = None
        if current_section == "tool.poetry.dependencies":
            group = "main"
        else:
            group_match = re.match(r'tool\.poetry\.group\.([^.]+)\.dependencies$', current_section)
            if group_match:
                group = group_match.group(1)
        if not group:
            continue

        dep_match = re.match(r'^([A-Za-z0-9_.-]+)\s*=\s*(.+)$', line)
        if dep_match:
            emit_dep(group, dep_match.group(1), dep_match.group(2).strip())

    pytest_section = re.search(r'^\[tool\.pytest\.ini_options\](.*?)(?=^\[|\Z)', text, re.M | re.S)
    if pytest_section:
        section_text = pytest_section.group(1)
        for key in ("addopts", "testpaths", "python_files"):
            value_match = re.search(rf'^\s*{key}\s*=\s*(.+)$', section_text, re.M)
            if value_match:
                print("PYTEST_CONFIG", key, compact(value_match.group(1).strip()))
PY"""

    def _poetry_lock_probe(self) -> str:
        return r"""# repo2run-planning-probe: poetry_lock
python - <<'PY'
from pathlib import Path
import re

path = Path("poetry.lock")
if not path.exists():
    print("POETRY_LOCK_PRESENT false")
    raise SystemExit(0)

text = path.read_text(encoding="utf-8", errors="replace")
print("POETRY_LOCK_PRESENT true")

suspicious_terms = (
    "pyobjc",
    "appscript",
    "pywin32",
    "sys_platform",
    "platform_system",
    "darwin",
    "win32",
    "macos",
)

packages = re.split(r'(?m)^\[\[package\]\]\s*$', text)
reported = set()
reported_packages = 0
for chunk in packages:
    name_match = re.search(r'(?m)^name\s*=\s*"([^"]+)"', chunk)
    if not name_match:
        continue
    name = name_match.group(1)
    name_lower = name.lower()
    lowered = chunk.lower()
    version_match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', chunk)
    if version_match and reported_packages < 160:
        print("LOCK_PACKAGE", name, version_match.group(1))
        reported_packages += 1

    lines = []
    for line in chunk.splitlines():
        stripped = line.strip()
        line_lower = line.lower()
        is_marker_line = (
            line_lower.startswith("markers =")
            or line_lower.startswith("python-versions =")
            or line_lower.startswith("dependencies =")
            or " sys_platform " in line_lower
            or "platform_system" in line_lower
        )
        if any(term in name_lower for term in suspicious_terms):
            if line_lower.startswith(("name =", "version =", "markers =", "python-versions =", "dependencies =")):
                lines.append(stripped)
        elif is_marker_line and any(term in line_lower for term in suspicious_terms):
            lines.append(stripped)
    if not lines:
        continue
    detail = " | ".join(lines[:4])
    key = (name, detail)
    if key in reported:
        continue
    reported.add(key)
    print("LOCK_PLATFORM_PACKAGE", name, detail[:260])

for marker in ("platform_release", "linuxkit"):
    if marker in text.lower():
        print("LOCK_GLOBAL_MARKER", marker)
PY"""

    def _import_scan_probe(self) -> str:
        return r"""# repo2run-planning-probe: import_scan
python - <<'PY'
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", "dist", "build", "logs"
}
COMMON_OPTIONAL = {
    "pytest", "typing_extensions", "setuptools", "pkg_resources", "yaml", "dotenv",
    "PIL", "cv2", "sklearn", "bs4", "jinja2", "dateutil"
}

def normalize(name):
    return name.lower().replace("-", "_").replace(".", "_")

declared = set()
pyproject = Path("pyproject.toml")
if pyproject.exists():
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    for raw_line in text.splitlines():
        dep = re.match(r'^\s*([A-Za-z0-9_.-]+)\s*=', raw_line)
        if dep and dep.group(1).lower() != "python":
            declared.add(normalize(dep.group(1)))

local_roots = set()
for candidate in Path(".").iterdir():
    if candidate.name.startswith("."):
        continue
    if candidate.is_dir() and (candidate / "__init__.py").exists():
        local_roots.add(candidate.name)
src = Path("src")
if src.exists():
    for candidate in src.iterdir():
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            local_roots.add(candidate.name)

stdlib = set(getattr(sys, "stdlib_module_names", set()))
imports = defaultdict(list)
files_seen = 0
for path in Path(".").rglob("*.py"):
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        continue
    if path.stat().st_size > 250_000:
        continue
    files_seen += 1
    if files_seen > 700:
        break
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.name.split(".")[0]].append(str(path))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports[node.module.split(".")[0]].append(str(path))

for name, paths in sorted(imports.items(), key=lambda item: (-len(item[1]), item[0]))[:160]:
    normalized = normalize(name)
    if name in stdlib or normalized in declared or name in local_roots or name in COMMON_OPTIONAL:
        continue
    sample = ",".join(sorted(set(paths))[:3])
    print("UNDECLARED_IMPORT", name, len(paths), sample)
PY"""

    def _focused_import_scan_probe(self) -> str:
        return r"""# repo2run-planning-probe: focused_import_scan
python - <<'PY'
import ast
import re
import shlex
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", "dist", "build", "logs", "docs"
}
COMMON_OPTIONAL = {
    "pytest", "typing_extensions", "setuptools", "pkg_resources", "yaml", "dotenv",
    "PIL", "cv2", "sklearn", "bs4", "jinja2", "dateutil", "click"
}

def normalize(name):
    return name.lower().replace("-", "_").replace(".", "_")

def dep_name_from_requirement(requirement):
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", str(requirement))
    return match.group(1) if match else ""

def parse_listish(value):
    raw = value.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return re.findall(r'["\']([^"\']+)["\']', raw)
    return [raw.strip('"\'')]

declared = set()
test_roots = []
ignore_paths = set()
pyproject = Path("pyproject.toml")
if pyproject.exists():
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    for requirement in re.findall(r'["\']([A-Za-z0-9_.-]+[^"\']*)["\']', text):
        name = dep_name_from_requirement(requirement)
        if name and name.lower() != "python":
            declared.add(normalize(name))
    for dep_line in re.finditer(r'(?m)^\s*([A-Za-z0-9_.-]+)\s*=', text):
        name = dep_line.group(1)
        if name.lower() != "python":
            declared.add(normalize(name))

    pytest_section = re.search(r'^\[tool\.pytest\.ini_options\](.*?)(?=^\[|\Z)', text, re.M | re.S)
    if pytest_section:
        section = pytest_section.group(1)
        testpaths = re.search(r'(?m)^\s*testpaths\s*=\s*(.+)$', section)
        if testpaths:
            test_roots.extend(parse_listish(testpaths.group(1)))
        addopts = re.search(r'(?m)^\s*addopts\s*=\s*(.+)$', section)
        if addopts:
            raw = addopts.group(1).strip().strip('"\'')
            try:
                tokens = shlex.split(raw)
            except ValueError:
                tokens = raw.split()
            for index, token in enumerate(tokens):
                if token.startswith("--ignore="):
                    ignore_paths.add(token.split("=", 1)[1].strip())
                elif token == "--ignore" and index + 1 < len(tokens):
                    ignore_paths.add(tokens[index + 1].strip())

if not test_roots:
    if Path("tests").exists():
        test_roots.append("tests")
    elif Path("src").exists():
        test_roots.append("src")
    else:
        test_roots.append(".")

local_roots = set()
for candidate in Path(".").iterdir():
    if candidate.name.startswith("."):
        continue
    if candidate.is_dir() and (candidate / "__init__.py").exists():
        local_roots.add(candidate.name)
src = Path("src")
if src.exists():
    for candidate in src.iterdir():
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            local_roots.add(candidate.name)

stdlib = set(getattr(sys, "stdlib_module_names", set()))
imports = defaultdict(list)
seen = 0
for root in test_roots:
    root_path = Path(root)
    if not root_path.exists():
        continue
    paths = [root_path] if root_path.is_file() else root_path.rglob("*.py")
    for path in paths:
        if not path.exists() or path.suffix != ".py":
            continue
        rel = str(path)
        parts = set(path.parts)
        if parts & SKIP_DIRS:
            continue
        if any(rel == item or rel.startswith(item.rstrip("/") + "/") for item in ignore_paths):
            continue
        if path.stat().st_size > 250_000:
            continue
        seen += 1
        if seen > 700:
            break
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports[alias.name.split(".")[0]].append(rel)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports[node.module.split(".")[0]].append(rel)

for name, paths in sorted(imports.items(), key=lambda item: (-len(item[1]), item[0]))[:160]:
    normalized = normalize(name)
    if name in stdlib or normalized in declared or name in local_roots or name in COMMON_OPTIONAL:
        continue
    sample = ",".join(sorted(set(paths))[:3])
    print("FOCUSED_UNDECLARED_IMPORT", name, len(paths), sample)
PY"""

    def _dependency_strategy_probe(self) -> str:
        return r"""# repo2run-planning-probe: dependency_strategy
python - <<'PY'
from pathlib import Path
import re

pyproject = Path("pyproject.toml")
lock = Path("poetry.lock")
if not pyproject.exists():
    print("INSTALL_STRATEGY none no_pyproject")
    raise SystemExit(0)

text = pyproject.read_text(encoding="utf-8", errors="replace")
lock_text = lock.read_text(encoding="utf-8", errors="replace") if lock.exists() else ""
combined = f"{text}\n{lock_text}"
lowered = combined.lower()
platform_terms = ("pyobjc", "eventkit", "foundation", "objc", "appscript", "pywin32", "darwin", "win32", "macos")
found_terms = sorted({term for term in platform_terms if term in lowered})
has_poetry = "[tool.poetry" in lowered or lock.exists()

if has_poetry and found_terms:
    print("INSTALL_STRATEGY avoid_full_poetry_install poetry_platform_specific_dependencies")
    print("INSTALL_STRATEGY_STEP install_build_basics python -m pip install --upgrade pip setuptools wheel")
    print("INSTALL_STRATEGY_STEP manifest_driven_deps install direct Linux-compatible dependencies from pyproject/lock before pytest-derived installs")
    print("INSTALL_STRATEGY_STEP editable_no_deps pip install -e . --no-deps")
    print("INSTALL_STRATEGY_AVOID_COMMAND poetry install")
    for term in found_terms:
        print("INSTALL_STRATEGY_AVOID_PACKAGE", term)
else:
    print("INSTALL_STRATEGY normal_manifest_install no_platform_specific_poetry_risk")
PY"""

    def _extract_findings(self, probes: List[SandboxProbeResult]) -> Dict[str, object]:
        findings: Dict[str, object] = {
            "runtime": {},
            "pyproject_present": None,
            "poetry_lock_present": None,
            "pyproject_dependencies": [],
            "lock_packages": {},
            "dependency_resolution_plan": {},
            "platform_specific_dependencies": [],
            "undeclared_import_candidates": [],
            "focused_undeclared_import_candidates": [],
            "pytest_config": [],
            "tool_availability": {},
            "install_strategies": [],
            "strategy_steps": [],
            "avoid_commands": [],
            "avoid_packages": [],
            "probe_failures": [],
            "probe_rounds": 0,
        }

        platform_deps = []
        undeclared = []
        focused_undeclared = []
        pyproject_deps = []
        lock_packages = {}
        pytest_config = []
        install_strategies = []
        strategy_steps = []
        avoid_commands = []
        avoid_packages = []

        for probe in probes:
            findings["probe_rounds"] = max(
                int(findings["probe_rounds"]),
                int(getattr(probe, "round_index", 1) or 1),
            )
            if not probe.success:
                findings["probe_failures"].append(probe.name)
            for line in (probe.output or "").splitlines():
                parts = line.split()
                if not parts:
                    continue
                marker = parts[0]
                if marker == "RUNTIME_PYTHON" and len(parts) >= 2:
                    findings["runtime"]["python"] = parts[1]
                elif marker == "RUNTIME_PLATFORM" and len(parts) >= 2:
                    findings["runtime"]["platform"] = " ".join(parts[1:])
                elif marker == "TOOL_AVAILABLE" and len(parts) >= 3:
                    findings["tool_availability"][parts[1]] = parts[2] == "True"
                elif marker == "PYTHON_MODULE_AVAILABLE" and len(parts) >= 3:
                    findings["tool_availability"][parts[1]] = parts[2] == "True"
                elif marker == "PYPROJECT_PRESENT" and len(parts) >= 2:
                    findings["pyproject_present"] = parts[1] == "true"
                elif marker == "POETRY_LOCK_PRESENT" and len(parts) >= 2:
                    findings["poetry_lock_present"] = parts[1] == "true"
                elif marker == "LOCK_PACKAGE" and len(parts) >= 3:
                    lock_packages[parts[1].lower()] = parts[2]
                elif marker == "PYPROJECT_DEP" and len(parts) >= 4:
                    pyproject_deps.append(
                        {
                            "group": parts[1],
                            "name": parts[2],
                            "constraint": " ".join(parts[3:]),
                        }
                    )
                elif marker in {"PYPROJECT_PLATFORM_DEP", "LOCK_PLATFORM_PACKAGE"} and len(parts) >= 2:
                    platform_deps.append(" ".join(parts[1:]))
                elif marker == "UNDECLARED_IMPORT" and len(parts) >= 3:
                    undeclared.append(
                        {
                            "name": parts[1],
                            "count": int(parts[2]) if parts[2].isdigit() else None,
                            "sample": " ".join(parts[3:]) if len(parts) > 3 else "",
                        }
                    )
                elif marker == "FOCUSED_UNDECLARED_IMPORT" and len(parts) >= 3:
                    focused_undeclared.append(
                        {
                            "name": parts[1],
                            "count": int(parts[2]) if parts[2].isdigit() else None,
                            "sample": " ".join(parts[3:]) if len(parts) > 3 else "",
                            "focused": True,
                        }
                    )
                elif marker == "PYTEST_CONFIG" and len(parts) >= 3:
                    pytest_config.append({"key": parts[1], "value": " ".join(parts[2:])})
                elif marker == "INSTALL_STRATEGY" and len(parts) >= 2:
                    install_strategies.append(
                        {
                            "name": parts[1],
                            "reason": " ".join(parts[2:]) if len(parts) > 2 else "",
                        }
                    )
                elif marker == "INSTALL_STRATEGY_STEP" and len(parts) >= 3:
                    strategy_steps.append(
                        {
                            "name": parts[1],
                            "action": " ".join(parts[2:]),
                        }
                    )
                elif marker == "INSTALL_STRATEGY_AVOID_COMMAND" and len(parts) >= 2:
                    avoid_commands.append(" ".join(parts[1:]))
                elif marker == "INSTALL_STRATEGY_AVOID_PACKAGE" and len(parts) >= 2:
                    avoid_packages.append(parts[1])

        findings["pyproject_dependencies"] = pyproject_deps[: self.MAX_FINDING_ITEMS]
        findings["lock_packages"] = dict(list(lock_packages.items())[: self.MAX_FINDING_ITEMS])
        findings["platform_specific_dependencies"] = self._unique(platform_deps)[: self.MAX_FINDING_ITEMS]
        findings["undeclared_import_candidates"] = undeclared[: self.MAX_FINDING_ITEMS]
        findings["focused_undeclared_import_candidates"] = focused_undeclared[: self.MAX_FINDING_ITEMS]
        findings["pytest_config"] = pytest_config[: self.MAX_FINDING_ITEMS]
        findings["install_strategies"] = install_strategies[: self.MAX_FINDING_ITEMS]
        findings["strategy_steps"] = strategy_steps[: self.MAX_FINDING_ITEMS]
        findings["avoid_commands"] = self._unique(avoid_commands)[: self.MAX_FINDING_ITEMS]
        findings["avoid_packages"] = self._unique(avoid_packages)[: self.MAX_FINDING_ITEMS]
        return findings

    def _build_dependency_resolution_plan(self, findings: Dict[str, object]) -> Dict[str, object]:
        dependencies = findings.get("pyproject_dependencies") or []
        lock_packages = findings.get("lock_packages") or {}
        pytest_config = findings.get("pytest_config") or []
        platform_terms = {
            "pyobjc",
            "eventkit",
            "foundation",
            "objc",
            "appscript",
            "pywin32",
            "darwin",
            "win32",
            "macos",
        }

        runtime_specs = []
        test_specs = []
        excluded = []
        for dependency in dependencies:
            name = str(dependency.get("name") or "").strip()
            if not name or name.lower() == "python":
                continue
            constraint = str(dependency.get("constraint") or "").strip()
            group = str(dependency.get("group") or "").lower()
            lowered = f"{name} {constraint} {group}".lower()
            if any(term in lowered for term in platform_terms):
                excluded.append(name)
                continue

            spec = self._locked_or_named_pip_spec(name, lock_packages)
            if self._dependency_group_is_test_like(group):
                test_specs.append(spec)
            elif group in {"main", "project"} or not group:
                runtime_specs.append(spec)

        if pytest_config:
            test_specs.append(self._locked_or_named_pip_spec("pytest", lock_packages))

        runtime_specs = self._unique(runtime_specs)[:30]
        test_specs = self._unique(test_specs)[:30]
        excluded = self._unique(excluded)[:20]
        steps = [{"name": "install_build_basics", "command": "python -m pip install --upgrade pip setuptools wheel"}]
        if runtime_specs:
            steps.append(
                {
                    "name": "install_manifest_runtime_deps",
                    "command": "pip install " + " ".join(runtime_specs),
                }
            )
        if test_specs:
            steps.append(
                {
                    "name": "install_manifest_test_deps",
                    "command": "pip install " + " ".join(test_specs),
                }
            )
        steps.append({"name": "editable_no_deps", "command": "pip install -e . --no-deps"})

        if not runtime_specs and not test_specs and not excluded:
            return {}

        return {
            "source": "pyproject.toml+poetry.lock+pytest_config",
            "strategy": "manifest_driven_linux_direct_deps_before_pytest_feedback",
            "runtime_dependency_specs": runtime_specs,
            "test_dependency_specs": test_specs,
            "excluded_platform_dependencies": excluded,
            "steps": steps,
            "notes": [
                "Install this manifest-derived direct dependency set before reacting to individual pytest import errors.",
                "If these commands expose resolver conflicts, edit dependency configuration or rollback/re-solve instead of stacking ad-hoc packages.",
            ],
        }

    def _dependency_group_is_test_like(self, group: str) -> bool:
        normalized = (group or "").lower()
        return any(marker in normalized for marker in ("test", "dev", "qa", "lint", "check"))

    def _locked_or_named_pip_spec(self, name: str, lock_packages: Dict[str, str]) -> str:
        normalized_name = name.lower().replace("_", "-")
        version = lock_packages.get(name.lower()) or lock_packages.get(normalized_name)
        if version and re.match(r"^[A-Za-z0-9_.!+~-]+$", str(version)):
            return f"{name}=={version}"
        return name

    def _apply_findings(
        self,
        plan: EnvironmentBuildPlan,
        exploration: SandboxExplorationResult,
    ):
        findings = exploration.findings
        summary = plan.repo_summary
        summary["sandbox_planning_exploration"] = {
            "probe_count": len(exploration.probes),
            "findings": findings,
        }
        summary["sandbox_runtime_environment"] = dict(findings.get("runtime") or {})
        dependency_plan = self._build_dependency_resolution_plan(findings)
        if dependency_plan:
            findings["dependency_resolution_plan"] = dependency_plan
            summary["dependency_resolution_plan"] = dependency_plan
        plan.plan_source = self._append_source(plan.plan_source, "sandbox_exploration")

        undeclared = self._combined_undeclared_imports(
            findings.get("focused_undeclared_import_candidates") or [],
            findings.get("undeclared_import_candidates") or [],
        )
        platform_deps = findings.get("platform_specific_dependencies") or []
        tool_availability = findings.get("tool_availability") or {}
        install_strategy_names = {
            item.get("name")
            for item in findings.get("install_strategies") or []
            if isinstance(item, dict)
        }
        needs_poetry_linux_strategy = (
            "avoid_full_poetry_install" in install_strategy_names
            or (plan.repo_summary.get("package_manager") == "poetry" and platform_deps)
        )

        if undeclared:
            names = self._actionable_python_import_names([item["name"] for item in undeclared[:10]])
            plan.risk_notes.append(
                "Sandbox planning probes found source imports not covered by manifest names: "
                + ", ".join(item["name"] for item in undeclared[:8])
                + ". Treat them as likely missing-module candidates if pytest collection fails."
            )
            self._add_node_once(
                plan,
                TaskNode(
                    id="sandbox probe: undeclared python imports",
                    type="language_dependency",
                    label="Potential undeclared Python import dependencies",
                    command_hint=self._pip_install_hint(names),
                    evidence=["sandbox import scan"],
                    confidence=0.55,
                    scope="test",
                    metadata={"candidates": undeclared[:8]},
                ),
            )
            self._add_soft_edge_to_verification(
                plan,
                "sandbox probe: undeclared python imports",
                "test_dependency_before_verify",
                ["sandbox import scan"],
            )
            plan.fallback_plan.append(
                {
                    "trigger": "pytest collection fails with ModuleNotFoundError for one of the sandbox import-scan candidates.",
                    "suggested_action": "Install only the reported missing module first, then rerun pytest collection before broad dependency installation.",
                    "evidence": ["sandbox import scan"],
                    "confidence": 0.55,
                    "source_node_id": "sandbox probe: undeclared python imports",
                    "target_node_id": self._first_verification_node_id(plan),
                    "edge_type": "test_dependency_before_verify",
                }
            )

        if needs_poetry_linux_strategy:
            self._apply_poetry_linux_strategy(plan, findings)

        if platform_deps:
            plan.risk_notes.append(
                "Sandbox planning probes found platform-specific dependency markers/packages: "
                + "; ".join(platform_deps[:4])
                + ". On Linux, avoid installing macOS/Windows-only packages unless markers are honored."
            )
            self._add_node_once(
                plan,
                TaskNode(
                    id="sandbox probe: platform-specific poetry dependencies",
                    type="language_dependency",
                    label="Platform-specific dependency risk from Poetry metadata",
                    command_hint=None,
                    evidence=["sandbox pyproject/poetry.lock probe"],
                    confidence=0.7,
                    scope="test",
                    metadata={"platform_dependencies": platform_deps[:8]},
                ),
            )
            self._add_soft_edge_to_verification(
                plan,
                "sandbox probe: platform-specific poetry dependencies",
                "test_dependency_before_verify",
                ["sandbox pyproject/poetry.lock probe"],
            )
            plan.fallback_plan.append(
                {
                    "trigger": "Poetry install fails while building a macOS/Windows-only dependency such as PyObjC.",
                    "suggested_action": "Do not brute-force install the platform package on Linux. Prefer honoring markers, constraining dependency groups, or falling back to targeted Linux/test dependencies plus editable source install.",
                    "source_node_id": "sandbox probe: platform-specific poetry dependencies",
                    "target_node_id": self._first_verification_node_id(plan),
                }
            )

        if tool_availability.get("pkg_resources") is False:
            plan.risk_notes.append(
                "Sandbox planning probe found `pkg_resources` unavailable in the base runtime; install/upgrade setuptools before build steps that require it."
            )

        self._recompute_todo(plan)

    def _add_node_once(self, plan: EnvironmentBuildPlan, node: TaskNode):
        if any(existing.id == node.id for existing in plan.nodes):
            return
        plan.nodes.append(node)

    def _apply_poetry_linux_strategy(
        self,
        plan: EnvironmentBuildPlan,
        findings: Dict[str, object],
    ):
        strategy_id = "install strategy: targeted linux deps before poetry"
        steps = findings.get("strategy_steps") or []
        dependency_plan = findings.get("dependency_resolution_plan") or {}
        dependency_steps = [
            {"name": step.get("name"), "action": step.get("command")}
            for step in dependency_plan.get("steps", [])
            if step.get("name") and step.get("command")
        ]
        if dependency_steps:
            merged_steps = [
                step
                for step in steps
                if isinstance(step, dict) and step.get("name") != "manifest_driven_deps"
            ]
            existing_step_names = {step.get("name") for step in merged_steps}
            insert_at = 1 if merged_steps else 0
            for dependency_step in dependency_steps:
                if dependency_step["name"] in existing_step_names:
                    continue
                merged_steps.insert(insert_at, dependency_step)
                existing_step_names.add(dependency_step["name"])
                insert_at += 1
            steps = merged_steps
        avoid_commands = findings.get("avoid_commands") or ["poetry install"]
        avoid_packages = findings.get("avoid_packages") or []
        platform_deps = findings.get("platform_specific_dependencies") or []

        plan.risk_notes.append(
            "Planning strategy: Poetry full install is high-risk on Linux because the "
            "manifest or lock metadata references platform-specific packages. Prefer "
            "targeted Linux/test dependencies plus editable source installation before "
            "attempting full Poetry resolution."
        )
        self._add_node_once(
            plan,
            TaskNode(
                id=strategy_id,
                type="install_strategy",
                label="Avoid high-risk full Poetry install on Linux",
                command_hint="follow dependency_resolution_plan, then `pip install -e . --no-deps`; avoid `poetry install` until proven safe",
                evidence=[
                    "sandbox dependency strategy probe",
                    "sandbox pyproject/poetry.lock probe",
                ],
                confidence=0.82,
                scope="test",
                metadata={
                    "strategy": "avoid_full_poetry_install",
                    "recommended_steps": steps,
                    "dependency_resolution_plan": dependency_plan,
                    "avoid_commands": avoid_commands,
                    "avoid_packages": avoid_packages,
                    "platform_dependencies": platform_deps[:8],
                },
            ),
        )

        package_manager_node = self._first_node_id_by_type(plan, "package_manager")
        if package_manager_node:
            self._add_hard_edge_once(
                plan,
                package_manager_node,
                strategy_id,
                "strategy_after_package_manager",
                ["sandbox dependency strategy probe"],
                0.82,
            )

        for node in list(plan.nodes):
            if node.id == strategy_id:
                continue
            if node.type == "language_dependency":
                self._add_hard_edge_once(
                    plan,
                    strategy_id,
                    node.id,
                    "strategy_before_dependency",
                    ["sandbox dependency strategy probe"],
                    0.82,
                )
                self._rewrite_poetry_dependency_hint(node)
            elif node.type == "project_build":
                self._add_hard_edge_once(
                    plan,
                    strategy_id,
                    node.id,
                    "strategy_before_build",
                    ["sandbox dependency strategy probe"],
                    0.82,
                )
                self._rewrite_poetry_build_hint(node)

        plan.fallback_plan.append(
            {
                "trigger": "Poetry full install or PyObjC/macOS dependency resolution fails on Linux.",
                "suggested_action": (
                    "Follow the install_strategy node first: install build basics, use "
                    "the manifest-driven Linux/test dependency set, then install the project editable "
                    "without full dependency resolution."
                ),
                "evidence": ["sandbox dependency strategy probe"],
                "source_node_id": strategy_id,
                "target_node_id": self._first_verification_node_id(plan),
                "edge_type": "strategy_before_build",
                "confidence": 0.82,
            }
        )

    def _rewrite_poetry_dependency_hint(self, node: TaskNode):
        if node.command_hint and "poetry install" in node.command_hint:
            node.command_hint = (
                "follow dependency_resolution_plan from pyproject/poetry.lock; "
                "install direct Linux/test dependencies before pytest-derived installs"
            )
            node.metadata["planning_rewrite"] = "avoid_full_poetry_install"

    def _rewrite_poetry_build_hint(self, node: TaskNode):
        if node.command_hint and "poetry install" in node.command_hint:
            node.label = "Install project with targeted Linux-compatible strategy"
            node.command_hint = "pip install -e . --no-deps"
            node.metadata["planning_rewrite"] = "avoid_full_poetry_install"

    def _add_hard_edge_once(
        self,
        plan: EnvironmentBuildPlan,
        from_id: str,
        to_id: str,
        edge_type: str,
        evidence: List[str],
        confidence: float,
    ):
        if any(
            edge.from_id == from_id
            and edge.to_id == to_id
            and edge.type == edge_type
            and edge.strength == "hard"
            for edge in plan.edges
        ):
            return
        plan.edges.append(
            TaskEdge(
                from_id=from_id,
                to_id=to_id,
                type=edge_type,
                strength="hard",
                evidence=evidence,
                confidence=confidence,
            )
        )

    def _first_node_id_by_type(self, plan: EnvironmentBuildPlan, node_type: str):
        for node in plan.nodes:
            if node.type == node_type:
                return node.id
        return None

    def _combined_undeclared_imports(self, focused: List[dict], general: List[dict]) -> List[dict]:
        combined = []
        seen = set()
        for item in list(focused) + list(general):
            name = item.get("name") if isinstance(item, dict) else None
            if not name or name in seen:
                continue
            seen.add(name)
            combined.append(item)
        return combined

    def _actionable_python_import_names(self, names: List[str]) -> List[str]:
        blocked = {
            "EventKit",
            "Foundation",
            "objc",
            "pyobjc",
            "pyobjc_core",
            "pyobjc_framework_eventkit",
        }
        normalized_blocked = {name.lower().replace("-", "_") for name in blocked}
        result = []
        for name in names:
            normalized = (name or "").lower().replace("-", "_")
            if normalized in normalized_blocked:
                continue
            result.append(name)
        return result

    def _add_soft_edge_to_verification(
        self,
        plan: EnvironmentBuildPlan,
        source_node_id: str,
        edge_type: str,
        evidence: List[str],
    ):
        target = self._first_verification_node_id(plan)
        if not target:
            return
        if any(edge.from_id == source_node_id and edge.to_id == target for edge in plan.edges):
            return
        plan.edges.append(
            TaskEdge(
                from_id=source_node_id,
                to_id=target,
                type=edge_type,
                strength="soft",
                evidence=evidence,
                confidence=0.55,
            )
        )

    def _first_verification_node_id(self, plan: EnvironmentBuildPlan):
        for node in plan.nodes:
            if node.type == "verification":
                return node.id
        return None

    def _recompute_todo(self, plan: EnvironmentBuildPlan):
        ordered_ids = self.topo_sorter.sort(plan.nodes, plan.edges)
        plan.ordered_todo_list = self.todo_generator.generate(ordered_ids, plan.nodes)

    def _pip_install_hint(self, names: List[str]) -> str:
        if not names:
            return None
        blocked = {
            "eventkit",
            "foundation",
            "objc",
            "pyobjc",
            "pyobjc-core",
            "pyobjc-framework-eventkit",
        }
        safe_names = [
            name
            for name in names[:6]
            if name.replace("_", "-").replace(".", "-").replace("-", "").isalnum()
            and name.lower().replace("_", "-") not in blocked
        ]
        if not safe_names:
            return None
        packages = " ".join(name.replace("_", "-") for name in safe_names)
        return f"pip install {packages}"

    def _append_source(self, current: str, suffix: str) -> str:
        current = current or "heuristic"
        parts = current.split("+")
        if suffix not in parts:
            parts.append(suffix)
        return "+".join(parts)

    def _write_exploration_log(self, log_dir: str, exploration: SandboxExplorationResult):
        if not log_dir:
            return
        os.makedirs(log_dir, exist_ok=True)
        json_path = os.path.join(log_dir, "sandbox_planning_exploration.json")
        with open(json_path, "w", encoding="utf-8") as file_obj:
            json.dump(exploration.to_dict(), file_obj, indent=2, ensure_ascii=False)

        md_path = os.path.join(log_dir, "sandbox_planning_exploration.md")
        with open(md_path, "w", encoding="utf-8") as file_obj:
            file_obj.write("# Sandbox Planning Exploration\n\n")
            file_obj.write("## Findings\n\n")
            file_obj.write("```json\n")
            json.dump(exploration.findings, file_obj, indent=2, ensure_ascii=False)
            file_obj.write("\n```\n\n")
            for probe in exploration.probes:
                file_obj.write(f"## Probe: {probe.name}\n\n")
                file_obj.write(f"- Success: `{probe.success}`\n\n")
                file_obj.write("Command:\n\n```bash\n")
                file_obj.write(probe.command)
                file_obj.write("\n```\n\nOutput:\n\n```text\n")
                file_obj.write(probe.output)
                file_obj.write("\n```\n\n")

    def _truncate(self, text: str) -> str:
        text = text or ""
        if len(text) <= self.MAX_PROBE_OUTPUT_CHARS:
            return text
        omitted = len(text) - self.MAX_PROBE_OUTPUT_CHARS
        return text[: self.MAX_PROBE_OUTPUT_CHARS] + f"\n... ({omitted} characters omitted)"

    def _unique(self, items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result
