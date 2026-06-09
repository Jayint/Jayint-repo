# tests/test_deletions_final_verification.py
"""
Final gate: confirm the deletion + wiring work is complete.

Checks:
  1. probes.py, acl.py, supervisor.py, worker.py, fullstate_worker.py,
     types.py, and serde.py files do not exist.
  2. No file imports from any of those deleted modules at top level.
  3. Arm 0 (bare ReAct) imports cleanly via DockerAgent(enable_v1=False).
  4. --arm v1 preset is present and --arm A/B/C are absent.
  5. DockerAgent accepts enable_v1 and _run_v1 method exists.
  6. agent.py does NOT contain 'from src.envstate.probes import' at module top-level
     (only allowed inside guard-gated inner imports that remain for back-compat).
"""
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

SELF = "tests/test_deletions_final_verification.py"

DELETED_MODULES = [
    "src.envstate.probes",
    "src.envstate.acl",
    "src.envstate.supervisor",
    "src.envstate.worker",
    "src.envstate.fullstate_worker",
    "src.envstate.types",
    "src.envstate.serde",
]

DELETED_FILES = [
    REPO_ROOT / "src" / "envstate" / "probes.py",
    REPO_ROOT / "src" / "envstate" / "acl.py",
    REPO_ROOT / "src" / "envstate" / "supervisor.py",
    REPO_ROOT / "src" / "envstate" / "worker.py",
    REPO_ROOT / "src" / "envstate" / "fullstate_worker.py",
    REPO_ROOT / "src" / "envstate" / "types.py",
    REPO_ROOT / "src" / "envstate" / "serde.py",
]


def _find_top_level_import(root: pathlib.Path, module_substr: str) -> list[str]:
    """Find files that import module_substr OUTSIDE a function body (top-level import)."""
    import ast
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(root))
        if rel == SELF:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr not in src:
            continue
        # Parse and check if import is at top level (not inside a function/method).
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = (getattr(node, "module", None) or "")
                if module_substr in name:
                    # Check if this node is a direct child of Module (top-level).
                    for parent in ast.walk(tree):
                        if hasattr(parent, "body") and node in getattr(parent, "body", []):
                            if isinstance(parent, ast.Module):
                                hits.append(rel)
                            break
    return hits


class FinalVerificationTests(unittest.TestCase):
    def test_all_deleted_files_absent(self):
        for f in DELETED_FILES:
            self.assertFalse(
                f.exists(),
                f"Expected {f.name} to be deleted but it still exists at {f}",
            )

    def test_no_top_level_imports_of_deleted_modules(self):
        for module in DELETED_MODULES:
            with self.subTest(module=module):
                hits = _find_top_level_import(REPO_ROOT, module)
                self.assertEqual(
                    hits, [],
                    f"Top-level imports of {module} remain: {hits}",
                )

    def test_arm_0_import_is_clean(self):
        """Importing DockerAgent with default (enable_v1=False) must not raise."""
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        # enable_v1 must be present; supervisor and fullstate_worker still present for back-compat
        self.assertIn("enable_v1", sig.parameters)
        self.assertIn("enable_supervisor", sig.parameters)
        self.assertIn("enable_fullstate_worker", sig.parameters)

    def test_v1_preset_exists_in_benchmark(self):
        from run_repo2run_benchmark import _ARM_PRESETS
        self.assertIn("v1", _ARM_PRESETS)
        self.assertNotIn("A", _ARM_PRESETS)
        self.assertNotIn("B", _ARM_PRESETS)
        self.assertNotIn("C", _ARM_PRESETS)

    def test_run_v1_method_exists_on_docker_agent(self):
        from agent import DockerAgent
        self.assertTrue(hasattr(DockerAgent, "_run_v1"))

    def test_enable_v1_defaults_false(self):
        import inspect
        from agent import DockerAgent
        sig = inspect.signature(DockerAgent.__init__)
        self.assertIs(sig.parameters["enable_v1"].default, False)

    def test_envstate_package_imports_cleanly(self):
        """The envstate package must import without errors after all deletions."""
        import src.envstate.build_agent  # noqa: F401
        import src.envstate.world_model  # noqa: F401
        import src.envstate.cleanroom    # noqa: F401
