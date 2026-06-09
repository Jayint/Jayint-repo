"""
worker.py and its test must be absent.
No file outside the deleted test suite may import from src.envstate.worker.
The regex helpers (_extract_worker_action, _is_worker_finished) and WorkerReport
must be importable from src.envstate.build_agent (Group 4 inlined them there).
"""
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent

WORKER_FILE = REPO_ROOT / "src" / "envstate" / "worker.py"
WORKER_TEST = REPO_ROOT / "tests" / "test_envstate_worker.py"

SKIP_PATHS = {
    "tests/test_deletions_worker_gone.py",
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


class WorkerGoneTests(unittest.TestCase):
    def test_worker_py_does_not_exist(self):
        self.assertFalse(
            WORKER_FILE.exists(),
            f"Expected {WORKER_FILE} to be deleted but it still exists.",
        )

    def test_worker_test_does_not_exist(self):
        self.assertFalse(
            WORKER_TEST.exists(),
            f"Expected {WORKER_TEST} to be deleted but it still exists.",
        )

    def test_no_surviving_import_of_worker(self):
        refs = _find_references(REPO_ROOT, "src.envstate.worker")
        self.assertEqual(
            refs, [],
            f"Surviving imports of src.envstate.worker found: {refs}. "
            "Update these to import from src.envstate.build_agent instead.",
        )

    def test_regex_helpers_importable_from_build_agent(self):
        """_extract_worker_action and _is_worker_finished must have been inlined
        into build_agent.py by Group 4 before worker.py can be deleted."""
        from src.envstate.build_agent import _extract_worker_action, _is_worker_finished  # noqa: F401

    def test_worker_report_importable_from_build_agent(self):
        from src.envstate.build_agent import TaskReport  # canonical v1 name  # noqa: F401
