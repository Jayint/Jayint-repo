"""
fullstate_worker.py and its test must be absent.
agent.py must not define _run_fullstate_worker.
No file may import from src.envstate.fullstate_worker.
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

FULLSTATE_FILE = REPO_ROOT / "src" / "envstate" / "fullstate_worker.py"
FULLSTATE_TEST = REPO_ROOT / "tests" / "test_fullstate_worker.py"

SKIP_PATHS = {
    "tests/test_deletions_fullstate_worker_gone.py",
    "tests/test_deletions_final_verification.py",  # lists deleted modules as strings, not live imports
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


class FullstateWorkerGoneTests(unittest.TestCase):
    def test_fullstate_worker_py_does_not_exist(self):
        self.assertFalse(
            FULLSTATE_FILE.exists(),
            f"Expected {FULLSTATE_FILE} to be deleted but it still exists.",
        )

    def test_fullstate_worker_test_does_not_exist(self):
        self.assertFalse(
            FULLSTATE_TEST.exists(),
            f"Expected {FULLSTATE_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_fullstate_worker(self):
        refs = _find_references(REPO_ROOT, "src.envstate.fullstate_worker")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.fullstate_worker found: {refs}",
        )

    def test_run_fullstate_worker_method_removed_from_agent(self):
        agent_text = (REPO_ROOT / "agent.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "def _run_fullstate_worker",
            agent_text,
            "agent.py must not define _run_fullstate_worker after fullstate_worker.py is deleted",
        )
