"""run_v3_session assembly: wires run_repair_arm, tags loop_mode, resolves the chain."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
for p in (str(_ROOT), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.schema import DepGraph  # noqa: E402
from src.envstate.repair_arm_entry import docker_adapters, run_v3_session, LOOP_MODE  # noqa: E402
from src.envstate.repair_log import DesignLog  # noqa: E402
from src.eval.repair_arm_eval.scenarios import scenario_chain  # noqa: E402


def test_run_v3_session_resolves_and_tags_loop_mode():
    g, world, agent = scenario_chain()          # inject the scripted agent + FakeWorld adapters
    seen = []
    outcome, _ = run_v3_session(
        g, replay=lambda gr, mb=(): world.replay_from_base(gr),
        certify=world.certify, readonly=world.readonly, agent=agent,
        log=DesignLog(silent=True), loop_mode_sink=seen.append)
    assert outcome == "DONE"
    assert seen == [LOOP_MODE]


class _FakeSandbox:
    """Duck-typed Sandbox double: no Docker, just a scripted InstallResult."""
    def __init__(self, stderr):
        self._stderr = stderr

    def reset_to_base(self):
        pass

    def run_install_script(self, script):
        from src.sandbox import InstallResult
        return InstallResult(
            rc=1,
            failing_command="python3 -m pip install --break-system-packages --no-deps psycopg2==2.9",
            lineno=10, stderr=self._stderr)

    def exec_readonly(self, cmd):
        return (0, "")


def test_docker_replay_cap_reflects_root_cause_not_raw_command():
    """Audit finding: the SAME node's install command is fixed text (populate.py emits
    one command per node), so keying made_progress's failing_cap on the raw
    failing_command collapses two DIFFERENT root causes for the SAME failing node into
    one identical signature — a false stall. failing_cap must instead reflect the
    classified error (spec §5.4's 'normalized error class'), which DOES differ here."""
    replay_libpq, _, _ = docker_adapters(_FakeSandbox(
        "error while loading shared libraries: libpq.so.5: cannot open shared object file"))
    replay_openssl, _, _ = docker_adapters(_FakeSandbox(
        "fatal error: openssl/ssl.h: No such file or directory"))

    g = DepGraph()
    result_libpq = replay_libpq(g)
    result_openssl = replay_openssl(g)

    assert result_libpq.failing_command == result_openssl.failing_command  # same node/command
    assert result_libpq.failing_cap != result_openssl.failing_cap          # different root cause
