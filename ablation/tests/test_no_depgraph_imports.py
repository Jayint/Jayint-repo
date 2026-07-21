from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path


ABLATION_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ABLATION_ROOT.parent
FORBIDDEN = (
    "python_deps.depgraph",
    "src.ecosystems.registry",
    "src.envstate.agent_action",
    "src.envstate.incremental_executor",
    "src.envstate.graph_scheduler",
    "src.envstate.depgraph_live",
)


def test_runtime_source_has_no_forbidden_graph_imports():
    violations: list[str] = []
    for path in sorted(ABLATION_ROOT.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                if any(name == item or name.startswith(item + ".") for item in FORBIDDEN):
                    violations.append(f"{path.name}:{node.lineno}:{name}")
    assert violations == []


def test_importing_ablation_runtime_does_not_load_depgraph_modules():
    code = """
import json
import sys
import ablation.controller
import ablation.discovery
import ablation.evidence
import ablation.execute_agent
import ablation.policy
import ablation.runtime
import ablation.run_execute_only
import ablation.rat_adapter
import ablation.run_rat_ablation
print(json.dumps(sorted(
    name for name in sys.modules if name.startswith("python_deps.depgraph")
)))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == []
