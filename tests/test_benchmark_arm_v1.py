"""
TDD for the --arm v1 preset and retirement of Arms A/B/C in
run_repo2run_benchmark.py (canonical contract §run_repo2run_benchmark.py).

Spec:
  - --arm choices must include 'v1' and '0'
  - --arm v1 sets enable_v1=True, enable_cleanroom=True, max_steps=12, _label='armV1_three_role'
  - --arm 0  sets enable_supervisor=False, enable_v1=False, max_steps=180 (unchanged)
  - --arm A/B/C must NOT be valid choices (retired)
  - build_agent_command() forwards --enable-v1 when args.enable_v1 is True
  - build_agent_command() must NOT forward --enable-supervisor / --enable-fullstate-worker
    when called with an arm-v1 namespace (those flags are False)
"""
import argparse
import pathlib
import sys
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from run_repo2run_benchmark import build_agent_command, _ARM_PRESETS  # noqa: E402


def _make_namespace(**kwargs) -> argparse.Namespace:
    defaults = dict(
        base_image="auto",
        model="claude-sonnet-4-6",
        max_steps=30,
        agent_command_timeout=1800,
        enable_observation_compression=False,
        enable_long_term_memory=False,
        memory_embedding_model="text-embedding-3-small",
        memory_path=None,
        keep_container=False,
        enable_supervisor=False,
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_envstate=False,
        enable_cleanroom=False,
        enable_v1=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class ArmPresetsTest(unittest.TestCase):
    def test_arm_v1_preset_exists(self):
        self.assertIn("v1", _ARM_PRESETS, "--arm v1 preset must exist in _ARM_PRESETS")

    def test_arm_0_preset_exists(self):
        self.assertIn("0", _ARM_PRESETS, "--arm 0 preset must exist in _ARM_PRESETS")

    def test_arm_A_retired(self):
        self.assertNotIn("A", _ARM_PRESETS, "Arm A must be retired from _ARM_PRESETS")

    def test_arm_B_retired(self):
        self.assertNotIn("B", _ARM_PRESETS, "Arm B must be retired from _ARM_PRESETS")

    def test_arm_C_retired(self):
        self.assertNotIn("C", _ARM_PRESETS, "Arm C must be retired from _ARM_PRESETS")

    def test_arm_v1_preset_fields(self):
        p = _ARM_PRESETS["v1"]
        self.assertTrue(p["enable_v1"], "arm v1 preset must set enable_v1=True")
        self.assertTrue(p["enable_cleanroom"], "arm v1 must set enable_cleanroom=True")
        self.assertEqual(p["max_steps"], 12, "arm v1 max_steps must be 12 (maps to max_cycles)")
        self.assertFalse(p.get("enable_supervisor", False))
        self.assertFalse(p.get("enable_fullstate_worker", False))
        self.assertFalse(p.get("fullstate_worker_prompt", False))
        self.assertEqual(p["_label"], "armV1_three_role")

    def test_arm_0_preset_unchanged(self):
        p = _ARM_PRESETS["0"]
        self.assertFalse(p.get("enable_v1", False))
        self.assertFalse(p["enable_supervisor"])
        self.assertFalse(p["enable_fullstate_worker"])
        self.assertEqual(p["max_steps"], 180)
        self.assertEqual(p["_label"], "arm0_bare_react")


class BuildAgentCommandV1Test(unittest.TestCase):
    def _run_build(self, **kwargs):
        ns = _make_namespace(**kwargs)
        return build_agent_command(
            python_executable="/usr/bin/python3",
            repo_root=REPO_ROOT,
            instance={"repo_url": "https://github.com/example/repo", "base_commit": "abc123"},
            workplace=REPO_ROOT / "workplace_test",
            args=ns,
        )

    def test_enable_v1_flag_forwarded(self):
        cmd = self._run_build(enable_v1=True)
        self.assertIn("--enable-v1", cmd)

    def test_enable_v1_not_forwarded_when_false(self):
        cmd = self._run_build(enable_v1=False)
        self.assertNotIn("--enable-v1", cmd)

    def test_supervisor_not_forwarded_for_arm_v1_namespace(self):
        cmd = self._run_build(enable_v1=True, enable_supervisor=False)
        self.assertNotIn("--enable-supervisor", cmd)

    def test_arm_0_does_not_include_v1_flag(self):
        cmd = self._run_build(enable_v1=False, enable_supervisor=False,
                               enable_fullstate_worker=False)
        self.assertNotIn("--enable-v1", cmd)
        self.assertNotIn("--enable-supervisor", cmd)


class ArgparseChoicesTest(unittest.TestCase):
    """The --arm argument in the benchmark parser must accept '0' and 'v1' only."""

    def _src(self):
        return (REPO_ROOT / "run_repo2run_benchmark.py").read_text(encoding="utf-8")

    def test_choices_include_v1(self):
        self.assertIn('"v1"', self._src(), '--arm choices must include "v1"')

    def test_choices_do_not_include_A(self):
        src = self._src()
        # "A" must not appear in choices=[...]
        import re
        m = re.search(r'choices=\[([^\]]+)\]', src)
        if m:
            choices_str = m.group(1)
            self.assertNotIn('"A"', choices_str, 'Arm A must be retired from --arm choices')
