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


def docker_adapters(sandbox):
    """Live-only glue: build (replay, certify, readonly) from a ``Sandbox``. Not unit-tested
    (needs Docker); exercised on the live path. Faithful to the current Model-B replay
    (render → reset_to_base → run_install_script) and to how ``run_v3`` certifies —
    ``certify_refresh`` wraps the sandbox's ``exec_readonly`` callable in the read-only adapter."""
    from python_deps.depgraph.build_script import render_build_script
    from python_deps.failure_classifier import classify_dependency_failure
    from src.envstate.depgraph_live import certify_refresh
    from src.envstate.install_localizer import localize_install_failure

    def replay(graph, manual_blocks=()):
        script = render_build_script(graph, manual_blocks)
        sandbox.reset_to_base()
        r = sandbox.run_install_script(script)
        if r.rc == 0:
            return ReplayResult(True)
        node = localize_install_failure(script, r.failing_command).node_id
        # failing_cap = a normalized error-CLASS signature (spec §5.4's "normalized
        # error class"), NOT the raw failing command: populate.py emits one FIXED
        # install command per node, so the SAME node failing twice for two DIFFERENT
        # root causes (e.g. libpq fixed, now missing an openssl header) would show
        # identical command text — made_progress would then read genuine progress as
        # a stall. classify_dependency_failure is the existing failure classifier the
        # DiagnosisRouter already wraps (no new classifier introduced).
        failure = classify_dependency_failure(r.failing_command or "", r.stderr or "")
        detail = (failure.import_name or failure.package_name
                  or failure.details.get("library") or failure.details.get("symbol") or "")
        cap = f"{failure.failure_type}:{detail}" if detail else failure.failure_type
        return ReplayResult(False, node, cap, r.failing_command, r.stderr or "")

    def certify(graph):
        return certify_refresh(graph, sandbox.exec_readonly, cycle=0)

    def readonly(cmd):
        return sandbox.exec_readonly(cmd)          # already returns (rc, output)

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
