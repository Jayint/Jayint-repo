"""Slice B L2 gate: real container + real LLM. Runs run_v3
(enable_script_materialization=True) on a repo with a known missing system lib
and asserts the typed-patch repair loop recovered it end-to-end.

Usage: python scripts/l2_repair_loop_smoke.py <repo_path>
           [--model <slug>] [--base-image <image>]

NOT run in CI — requires Docker + a real LLM API key
(OPENROUTER_API_KEY / MINIMAX_API_KEY / OPENAI_API_KEY).

Adaptation notes vs brief
--------------------------
* ``make_real_executors`` — scripts/l1_engine_swap_smoke.py is a stub (doc-
  comment only, no function defined). Executors are wired inline here using
  ``src.sandbox.Sandbox`` exactly as agent.py does.
* ``make_client`` — src/envstate/llm_client.py does not exist. Client is
  built inline via ``openai.OpenAI + httpx.Timeout``, mirroring agent.py
  lines 420-439.
* ``WorldModelMap(dep_graph=graph)`` — WorldModelMap is a frozen dataclass
  with many required fields; that call would TypeError. Replaced with the
  ``initial_map()`` factory (same factory used by agent.py).
* ``DeterministicMaintainer()`` → ``DeterministicMaintainer(v3_only=True)``:
  the v3 arm must use the slim done-gate path (dep_graph is truth; no
  contract_graph write). Default v3_only=False would write the wrong map slice.
"""
from __future__ import annotations
import argparse, os, sys


def main() -> int:  # noqa: C901 — deliberately all-in-one driver
    ap = argparse.ArgumentParser(
        description=(
            "Slice B L2 gate: real container + real LLM repair-loop smoke test. "
            "NOT run in CI."
        )
    )
    ap.add_argument("repo", help="Path to repo with a known missing system lib")
    ap.add_argument("--model", default=None, help="LLM model slug")
    ap.add_argument(
        "--base-image",
        default="python:3.11-slim",
        dest="base_image",
        help="Docker base image (default: python:3.11-slim)",
    )
    ap.add_argument(
        "--enable-binding-install",
        action="store_true",
        dest="enable_binding_install",
        help="Stage 2: render the graph to setup.sh, reset to base, install, certify reciped nodes",
    )
    args = ap.parse_args()

    # ── Heavy imports (inside main so top-level import is clean) ─────────────
    from openai import OpenAI
    from httpx import Timeout                                   # mirrors agent.py:12
    from src.sandbox import Sandbox
    from src.envstate.orchestrator import run_v3
    from src.envstate.build_agent import BuildAgent
    from src.envstate.deterministic_maintainer import DeterministicMaintainer
    from src.envstate.world_model import initial_map
    from src.envstate.ledger import ActionLedger
    from src.envstate.snapshot import probe_env
    from src.envstate.manifest import parse_manifests

    # ── 1. LLM client ────────────────────────────────────────────────────────
    # src/envstate/llm_client.py does not exist; build the client inline.
    # Provider precedence: OpenRouter -> MiniMax -> OpenAI (all OAI-compatible).
    api_key = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("MINIMAX_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    base_url = (
        os.getenv("OPENROUTER_API_BASE")
        or os.getenv("MINIMAX_API_BASE")
        or os.getenv("OPENAI_API_BASE")
    )
    if not api_key:
        print(
            "ERROR: No LLM API key found. "
            "Set OPENROUTER_API_KEY, MINIMAX_API_KEY, or OPENAI_API_KEY.",
            file=sys.stderr,
        )
        return 2
    _read_timeout = float(os.getenv("LLM_READ_TIMEOUT", "120"))
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=Timeout(connect=10.0, read=_read_timeout, write=30.0, pool=10.0),
        max_retries=0,
    )
    model = args.model or os.getenv("LLM_MODEL", "gpt-4o")

    # ── 2. Dep-graph (scratch container — leaves agent container untouched) ──
    # Mirrors agent.py lines 1151-1163; graceful degradation on any failure.
    graph = None
    try:
        from python_deps.depgraph.advise import build_advisory_for_repo
        _advisory, graph = build_advisory_for_repo(args.repo, args.base_image)
        _n = sum(1 for _ in graph.nodes) if graph is not None else 0
        print(f"[l2-smoke] dep-graph: {_n} nodes")
    except Exception as exc:
        print(
            f"[l2-smoke] dep-graph build failed (graceful degradation): {exc}",
            file=sys.stderr,
        )
        graph = None

    # ── 3. Host-side manifest (no container) ─────────────────────────────────
    manifest = parse_manifests(args.repo)

    # ── 4. Initial world map ─────────────────────────────────────────────────
    # Cannot use WorldModelMap(dep_graph=graph) — frozen dataclass with many
    # required fields. Use initial_map() factory (same as agent.py line 1192).
    world_map = initial_map(
        base_image=args.base_image,
        workdir="/app",
        language="unknown",      # probed and refined in the first cycle
        build_system=manifest.build_system if manifest is not None else "unknown",
        repo_layout=(),          # empty is fine; dep_graph is the truth source
        dep_graph=graph,
    )

    # ── 5. Real-container executors ──────────────────────────────────────────
    # l1_engine_swap_smoke.py contains only a doc-comment stub — make_real_executors
    # is not defined there. Wired inline using Sandbox (mirrors agent.py wiring).
    sandbox = Sandbox(base_image=args.base_image, workdir="/app", seed_dir=args.repo)
    try:
        sandbox_execute = sandbox.execute         # (cmd: str) -> (bool, str)
        exec_readonly = sandbox.exec_readonly     # (cmd: str) -> (int, str)
        probe = lambda: probe_env(exec_readonly)  # () -> EnvSnapshot

        # ── 6. Collaborators ─────────────────────────────────────────────────
        # synthesizer=None: BuildAgent accepts None; mutation classification
        # is only needed for ledger attribution, not for the v3 repair loop.
        # v3_only=True: slim done-gate; dep_graph is the sole world-model in
        # the v3/graph-scheduler arm (contract_graph must NOT be written).
        agent = BuildAgent(client, model, synthesizer=None)
        maintainer = DeterministicMaintainer(v3_only=True)
        ledger = ActionLedger()

        # ── 7. v3 graph-scheduler loop ────────────────────────────────────────
        final_map, stop = run_v3(
            agent,
            maintainer=maintainer,
            initial_world_map=world_map,
            ledger=ledger,
            sandbox_execute=sandbox_execute,
            probe=probe,
            manifest=manifest,
            exec_readonly=exec_readonly,
            enable_script_materialization=True,
            enable_binding_install=args.enable_binding_install,
            reset_to_base=sandbox.reset_to_base,
            run_install_script=sandbox.run_install_script,
        )
    finally:
        # Best-effort container cleanup — must not mask the test result.
        try:
            if getattr(sandbox, "container", None) is not None:
                sandbox.container.stop(timeout=5)
                sandbox.container.remove(force=True)
        except Exception:
            pass

    # ── 8. Evaluate recovery ─────────────────────────────────────────────────
    dep_graph = getattr(final_map, "dep_graph", None)
    if dep_graph is not None:
        from python_deps.depgraph.schema import State
        unresolved = [n.id for n in dep_graph.nodes if n.state is State.MISSING]
    else:
        unresolved = []

    print(f"stop_reason={stop} unresolved={unresolved}")
    ok = stop in ("done", "done_flag", "planner_done") and not unresolved
    print("L2 SLICE B:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
