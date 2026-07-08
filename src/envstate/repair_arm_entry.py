"""Production entry for arm C (spec 2026-07-08 §11) — a NEW arm, no cutover.

``run_v3_session`` assembles the real pieces around ``run_repair_arm``: a ``SessionAgent``
(built from an LLM client/model), the real ``DiagnosisRouter`` (routes repo-internal /
residual failures OUT of env-repair), and — in production — Docker replay/certify/readonly
adapters. Tagged ``loop_mode="v3_session_repair"``. The current ``run_v1``/``run_v3`` arms and
the ``trace_verify`` proof are untouched.

Adapters are injectable so the assembly is unit-testable offline (inject FakeWorld); the
Docker glue in ``docker_adapters`` is thin and exercised only on the live path."""
from __future__ import annotations

from src.envstate.repair_arm import run_repair_arm
from src.envstate.repair_log import DesignLog
from src.envstate.repair_types import ReplayResult
from src.envstate.session_agent import SessionAgent

LOOP_MODE = "v3_session_repair"


def _make_diagnose(repo_path):
    """Route repo-internal-import / residual-test failures OUT of env-repair (spec §5.1).
    With no repo_path (offline/unit) there are no local module names to identify, so every
    failure is ENVIRONMENT — the router only ever demotes on positive local/ residual evidence."""
    if not repo_path:
        return lambda error: "ENVIRONMENT"

    from python_deps.depgraph import scan
    from python_deps.depgraph.diagnose import RepoContext, diagnose_all, Mode

    local = frozenset(scan.local_module_names(repo_path))

    def diagnose(error):
        obs = ((error.failing_command or "", error.output or ""),)
        modes = {d.mode for d in diagnose_all(obs, RepoContext(local, frozenset()))}
        if Mode.REPO_INTERNAL_REF in modes or Mode.RESIDUAL in modes:
            return "OUT_OF_SCOPE"
        return "ENVIRONMENT"

    return diagnose


def docker_adapters(sandbox, exec_readonly):
    """Live-only glue: build (replay, certify, readonly) from a Sandbox + read-only Executor.
    Not unit-tested (needs Docker); exercised on the live path. Faithful to the current
    Model-B replay (render → reset_to_base → run_install_script)."""
    from python_deps.depgraph.build_script import render_build_script
    from python_deps.depgraph.certify import certify_all
    from src.envstate.install_localizer import localize_install_failure

    def replay(graph, manual_blocks=()):
        script = render_build_script(graph, manual_blocks)
        sandbox.reset_to_base()
        r = sandbox.run_install_script(script)
        if r.rc == 0:
            return ReplayResult(True)
        node = localize_install_failure(script, r.failing_command).node_id
        # failing_cap = the failing command: a signature that changes when the failure moves
        # forward, which is exactly what the progress rule keys on.
        return ReplayResult(False, node, r.failing_command, r.failing_command,
                            getattr(r, "output", "") or "")

    def certify(graph):
        return certify_all(graph, exec_readonly)

    def readonly(cmd):
        res = exec_readonly.run(cmd)
        return (res.returncode, (res.stdout or res.stderr or "")[:200])

    return replay, certify, readonly


def run_v3_session(graph, *, replay, certify, readonly=None, agent=None, log=None,
                   client=None, model=None, repo_path=None,
                   known_evidence_ids=frozenset(), loop_mode_sink=None, max_errors=20):
    """Assemble + run arm C. Provide ``agent`` directly (tests), or ``client``+``model`` to
    build a ``SessionAgent``. ``log`` defaults to a verbose ``DesignLog`` (the heavy
    design-area logging). ``loop_mode_sink`` (e.g. ``tracer.set_loop_mode``) is called with
    ``LOOP_MODE`` so the run is tagged as the session-repair arm."""
    if agent is None:
        agent = SessionAgent(client, model, known_evidence_ids=known_evidence_ids)
    if log is None:
        log = DesignLog()
    if loop_mode_sink is not None:
        loop_mode_sink(LOOP_MODE)
    return run_repair_arm(graph, replay=replay, certify=certify, readonly=readonly,
                          agent=agent, log=log, diagnose=_make_diagnose(repo_path),
                          max_errors=max_errors)
