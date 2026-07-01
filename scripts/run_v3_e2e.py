"""run_v3_e2e — the single legible entrypoint of the v3 (GSM) environment builder.

This is the whole story in one driver:

  1. BELIEF      build the dependency graph (static evidence + a bounded LLM
                 classifier that proposes typed Config/Service/DataAsset nodes).
  2. PROJECTION  the graph is materialized into ONE install-only setup.sh.
  3. INNER LOOP  run_v3 executes the script block-by-block, the host certifies
                 each node, and on a failed block the V3BuildAgent proposes ONE
                 typed PatchProposal (gated by PatchGate, bounded by repair_loop).
  4. INSTALL GATE  enable_binding_install resets the container to a clean base,
                 runs the whole rendered setup.sh, and certifies reciped nodes
                 (the fresh-replay installability proof).
  5. TEST GATE   the done-gate runs real pytest; observability reports both gates.
  6. ARTIFACT    the final certified graph is rendered to setup.sh.

The agent has proposal power only: every path to a certified node runs through
PatchGate -> rendered block -> host execution -> deterministic host check.

NOT run in CI — requires Docker + a real LLM API key
(OPENROUTER_API_KEY / MINIMAX_API_KEY / OPENAI_API_KEY).

Usage:
  python scripts/run_v3_e2e.py <repo_path> [--model <slug>]
         [--base-image auto|python:3.11-slim] [--out setup.sh]
         [--no-binding-install]   # ablate the fresh-replay install gate

  --base-image defaults to "auto" (LLM-selected, then pinned to
  requires-python and normalized to a -slim variant); pass an explicit
  tag (e.g. python:3.11-slim) to override verbatim.
"""
from __future__ import annotations

import argparse
import os
import sys

# repo root + src/ both on path (mirrors the test bootstrap): `src.sandbox`
# resolves from root, `python_deps.*` resolves from src/.
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_root, os.path.join(_root, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── module-level imports so tests can monkeypatch these names on the module ──
from openai import OpenAI
from httpx import Timeout
from src.sandbox import Sandbox
from src.envstate.orchestrator import run_v3
from src.envstate.v3_build_agent import V3BuildAgent
from src.envstate.deterministic_maintainer import DeterministicMaintainer
from src.envstate.world_model import initial_map
from src.envstate.ledger import ActionLedger
from src.envstate.snapshot import probe_env
from src.envstate.manifest import parse_manifests
from src.envstate.llm_response import complete_with_retry
from src.envstate.env_classifier import make_construction_classifier
from src.envstate.base_image_selection import choose_base_image
from python_deps.depgraph.advise import build_advisory_for_repo
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.schema import State


def _parse_args(argv):
    ap = argparse.ArgumentParser(description="v3 (GSM) environment builder — end to end.")
    ap.add_argument("repo", help="Path to the target repository")
    ap.add_argument("--model", default=None, help="LLM model slug")
    ap.add_argument("--base-image", default="auto", dest="base_image")
    ap.add_argument("--out", default="setup.sh", help="Where to write the final setup.sh")
    ap.add_argument(
        "--no-binding-install",
        action="store_false",
        dest="binding_install",
        help="Ablate the fresh-replay installability gate (inner loop only).",
    )
    return ap.parse_args(argv)


def _run(args) -> int:  # noqa: C901 — deliberately one all-in-one driver
    # ── 1. LLM client (OAI-compatible; OpenRouter -> MiniMax -> OpenAI) ───────
    api_key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("MINIMAX_API_KEY")
               or os.getenv("OPENAI_API_KEY"))
    base_url = (os.getenv("OPENROUTER_API_BASE") or os.getenv("MINIMAX_API_BASE")
                or os.getenv("OPENAI_API_BASE"))
    if not api_key:
        print("ERROR: set OPENROUTER_API_KEY / MINIMAX_API_KEY / OPENAI_API_KEY.",
              file=sys.stderr)
        return 2
    client = OpenAI(
        api_key=api_key, base_url=base_url or None, max_retries=0,
        timeout=Timeout(connect=10.0, read=float(os.getenv("LLM_READ_TIMEOUT", "120")),
                        write=30.0, pool=10.0),
    )
    model = args.model or os.getenv("LLM_MODEL", "gpt-4o")

    def _complete(messages) -> str:
        return complete_with_retry(client, model, messages, temperature=0)[0]

    # ── 1.5 SELECT: pick + pin the base image (auto) or honor an explicit tag ─
    choice = choose_base_image(
        args.repo, client, model,
        explicit=(None if args.base_image == "auto" else args.base_image),
    )
    print(f"[v3] base-image: {choice.image} (py {choice.minor}) — {choice.reason}")
    base_image = choice.image

    # ── 2. BELIEF: build the dep-graph; the LLM classifier proposes typed nodes
    classify = make_construction_classifier(_complete)
    graph = None
    try:
        _advisory, graph = build_advisory_for_repo(
            args.repo, base_image, target_python=choice.minor, classify=classify,
        )
        n = sum(1 for _ in graph.nodes) if graph is not None else 0
        print(f"[v3] dep-graph: {n} nodes")
    except Exception as exc:  # graceful degradation — graph is advisory at construction
        print(f"[v3] dep-graph build failed (graceful degradation): {exc}", file=sys.stderr)
        graph = None

    manifest = parse_manifests(args.repo)
    world_map = initial_map(
        base_image=base_image, workdir="/app", language="unknown",
        build_system=manifest.build_system if manifest is not None else "unknown",
        repo_layout=(), dep_graph=graph,
    )

    # ── 3-5. Real container + the v3 loop ────────────────────────────────────
    sandbox = Sandbox(base_image=base_image, workdir="/app",
                       platform=choice.platform_override, seed_dir=args.repo)
    gates_seen: list = []
    try:
        final_map, stop = run_v3(
            V3BuildAgent(client, model),
            maintainer=DeterministicMaintainer(v3_only=True),
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=sandbox.execute,
            probe=lambda: probe_env(sandbox.exec_readonly),
            manifest=manifest,
            exec_readonly=sandbox.exec_readonly,
            enable_script_materialization=True,        # v3: graph -> setup.sh, host-certified
            enable_binding_install=args.binding_install,  # fresh-replay installability gate
            reset_to_base=sandbox.reset_to_base,
            run_install_script=sandbox.run_install_script,
            enable_gate_observability=True,            # report both maturity gates on exit
            gate_observer=gates_seen.append,
        )
    finally:
        try:
            if getattr(sandbox, "container", None) is not None:
                sandbox.container.stop(timeout=5)
                sandbox.container.remove(force=True)
        except Exception:
            pass

    # ── 6. ARTIFACT + report ─────────────────────────────────────────────────
    dep_graph = getattr(final_map, "dep_graph", None)
    unresolved = ([n.id for n in dep_graph.nodes if n.state is State.MISSING]
                  if dep_graph is not None else [])
    if dep_graph is not None:
        with open(args.out, "w") as fh:
            fh.write(render_build_script(dep_graph))
        print(f"[v3] wrote certified setup.sh -> {args.out}")

    if gates_seen:
        for g in gates_seen[-1]:
            print(f"[v3] gate: {g}")
    print(f"stop_reason={stop} unresolved={unresolved}")
    ok = stop in ("done", "done_flag", "planner_done") and not unresolved
    print("V3 E2E:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main_with_args(argv) -> int:
    args = _parse_args(argv)
    return _run(args)


def main() -> int:
    return main_with_args(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
