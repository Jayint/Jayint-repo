"""
supervisor.py and its test must be absent.
agent.py must not import from src.envstate.supervisor anywhere.
agent.py._run_supervisor must be gone (the dispatch check enable_supervisor is
kept as a deprecated no-op so existing CLI invocations don't crash).
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

SUPERVISOR_FILE = REPO_ROOT / "src" / "envstate" / "supervisor.py"
SUPERVISOR_TEST = REPO_ROOT / "tests" / "test_envstate_supervisor.py"

SKIP_PATHS = {
    "tests/test_deletions_supervisor_gone.py",
}


def _find_references(root: pathlib.Path, module_substr: str) -> list[str]:
    hits = []
    for py in sorted(root.rglob("*.py")):
        if ".venv" in py.parts or "__pycache__" in py.parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except Exception:
            continue
        if module_substr in src:
            rel = str(py.relative_to(root))
            if rel not in SKIP_PATHS:
                hits.append(rel)
    return hits


class SupervisorGoneTests(unittest.TestCase):
    def test_supervisor_py_does_not_exist(self):
        self.assertFalse(
            SUPERVISOR_FILE.exists(),
            f"Expected {SUPERVISOR_FILE} to be deleted but it still exists.",
        )

    def test_supervisor_test_does_not_exist(self):
        self.assertFalse(
            SUPERVISOR_TEST.exists(),
            f"Expected {SUPERVISOR_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_supervisor(self):
        refs = _find_references(REPO_ROOT, "src.envstate.supervisor")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.supervisor found: {refs}",
        )

    def test_run_supervisor_method_removed_from_agent(self):
        """_run_supervisor must be gone from DockerAgent — the dispatch is a no-op stub."""
        agent_text = (REPO_ROOT / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "def _run_supervisor",
            agent_text,
            "agent.py must not define _run_supervisor after supervisor.py is deleted",
        )
