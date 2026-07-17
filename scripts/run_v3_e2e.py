"""run_v3_e2e — the single legible entrypoint of the v3 (GSM) environment builder.

This is the whole story in one driver:

  1. SELECT      pick (or honor an explicit) base image, pin it to the repo's
                 ``requires-python``, and normalize to a ``-slim`` variant —
                 one decision feeds the sandbox boot AND the dep-graph build.
  2. BELIEF      build the dependency graph (static evidence + a bounded LLM
                 classifier that proposes typed Config/Service/DataAsset nodes).
  3. PROJECTION  the graph is materialized into ONE install-only setup.sh.
  4. INNER LOOP  a graph-linked Build Plan executes block-by-block. Verified
                 prefixes become semantic Docker checkpoints; a graph patch
                 invalidates only the affected suffix.
  5. EXECUTE AGENT  on a failed block, GraphExecuteAgent receives its DepGraph
                 slice + structured block packet and proposes ONE typed patch.
  6. INSTALL GATE  success always triggers one clean full setup.sh replay from
                 the raw base image; search checkpoints can never certify a run.
  7. TEST GATE   the done-gate runs real pytest; observability reports both gates.
  8. ARTIFACT    the final certified graph is rendered to setup.sh.

The agent has proposal power only: every path to a certified node runs through
PatchGate -> rendered block -> host execution -> deterministic host check.

NOT run in CI — requires Docker + a real LLM API key
(OPENROUTER_API_KEY / MINIMAX_API_KEY / OPENAI_API_KEY).

Usage:
  python scripts/run_v3_e2e.py <repo_path> [--model <slug>]
         [--base-image auto|python:3.11-slim] [--out setup.sh]
         [--trace-out trace.json]

  --base-image defaults to "auto" (LLM-selected, then pinned to
  requires-python and normalized to a -slim variant); pass an explicit
  tag (e.g. python:3.11-slim) to override verbatim.

  --trace-out, if given, builds a RunTracer, threads it through run_v3, and
  on exit writes the run's RunTrace JSON plus prints the
  verify_canonical_trace / verify_artifact_consistency / local-import-guard
  results. Omitted -> no tracer is built and behavior is unchanged.
"""
from __future__ import annotations

import argparse
import json
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
from src.envstate.orchestrator import MAX_CYCLES, run_v3
from src.envstate.v3_build_agent import GraphExecuteAgent, V3BuildAgent
from src.envstate.incremental_executor import IncrementalPlanExecutor
from src.envstate.deterministic_maintainer import DeterministicMaintainer
from src.envstate.world_model import initial_map
from src.envstate.ledger import ActionLedger
from src.envstate.snapshot import probe_env
from src.envstate.manifest import parse_manifests
from src.envstate.llm_response import complete_with_retry
from src.envstate.env_classifier import make_construction_classifier
from src.envstate.base_image_selection import choose_base_image
from src.envstate.run_trace import RunTracer
from src.envstate.proof import finalize_trace
from python_deps.depgraph.advise import build_advisory_for_repo
from python_deps.depgraph.build_script import render_build_script
from python_deps.depgraph.schema import NodeType, State
from python_deps.depgraph.test_intent import discover_test_dependency_intent


_DONE_STOPS = frozenset({"done", "done_flag", "planner_done"})


def _positive_env_seconds(name: str, default: int) -> int:
    """Read a positive command budget without making configuration fatal."""
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _e2e_succeeded(stop: str, unresolved: list[str], gates_seen: list) -> bool:
    """Final user-facing status, bound to the same gates as canonical success.

    Missing soft Config/Service hints may remain in the graph after a successful
    clean replay.  They must not override binding installability/testability
    certificates.  The legacy unresolved-node rule remains the fallback for
    callers that do not enable gate observability.
    """
    if stop not in _DONE_STOPS:
        return False
    if gates_seen:
        latest = {gate.name: gate for gate in gates_seen[-1]}
        required = (latest.get("installability"), latest.get("testability"))
        if all(gate is not None for gate in required):
            return all(
                bool(gate.passed) and not bool(getattr(gate, "provisional", False))
                for gate in required
            )
    return not unresolved


def _runtime_handoff(dep_graph, *, pytest_addopts: tuple[str, ...] = ()) -> dict:
    """Explicit, certified runtime state that an evaluator must replay.

    Process state cannot survive a Docker build layer.  Only host-certified,
    confirmed Service nodes are exported; setup-script text is never parsed to
    infer daemon commands.
    """
    services: list[dict[str, str]] = []
    if dep_graph is not None:
        for node in dep_graph.nodes:
            recipe = node.data.get("start_recipe") or {}
            start = recipe.get("start")
            check = node.check_command
            if (
                node.type is NodeType.SERVICE
                and node.state is State.SATISFIED
                and node.data.get("service_confidence") == "confirmed"
                and isinstance(start, str)
                and start.strip()
                and isinstance(check, str)
                and check.strip()
            ):
                services.append({
                    "kind": node.name,
                    "start": start.strip(),
                    "check": check.strip(),
                })
    services.sort(key=lambda item: (item["kind"], item["start"]))
    handoff = {
        "version": 1,
        "services": services,
        "runtime_commands": [item["start"] for item in services],
    }
    if pytest_addopts:
        handoff["environment"] = {
            "PYTEST_ADDOPTS": " ".join(pytest_addopts),
        }
    return handoff


def _build_arg_parser() -> argparse.ArgumentParser:
    """Pure argparse construction. All of this module's non-stdlib imports
    happen at module import time above (needed so tests can monkeypatch
    ``choose_base_image``/``Sandbox``/``run_v3``/etc. on the module), but none
    of them talk to Docker or an LLM API to *import* — only to *use* — so
    building/parsing against this parser stays safe in a test process with no
    Docker or LLM key (Task 8d smoke test: parses ``--trace-out`` without
    exercising the Docker/LLM path).
    """
    ap = argparse.ArgumentParser(description="v3 (GSM) environment builder — end to end.")
    ap.add_argument("repo", help="Path to the target repository")
    ap.add_argument("--model", default=None, help="LLM model slug")
    ap.add_argument(
        "--base-image", default="auto", dest="base_image",
        help='"auto" (default) selects + pins a base image via the LLM '
             "selector; pass an explicit tag (e.g. python:3.11-slim) to "
             "override verbatim.",
    )
    ap.add_argument("--out", default="setup.sh", help="Where to write the final setup.sh")
    ap.add_argument(
        "--language-hint", default=None, dest="language_hint",
        help="Optional oracle/runtime language hint for base-image selection. "
             "Generic v3 callers leave this unset; benchmark adapters may pass it.",
    )
    ap.add_argument(
        "--execution-mode", choices=("incremental", "fresh"), default="incremental",
        help="incremental graph/checkpoint search (default) or fresh full replay each cycle",
    )
    ap.add_argument(
        "--max-cycles", type=int, default=MAX_CYCLES,
        help=f"Maximum graph/repair cycles (default: {MAX_CYCLES})",
    )
    ap.add_argument(
        "--trace-out", default=None, dest="trace_out",
        help="Where to write the run's RunTrace JSON (Task 8 proof harness). "
             "Omitted -> no tracer is built and behavior is unchanged.",
    )
    ap.add_argument(
        "--runtime-out", default=None, dest="runtime_out",
        help="Optional JSON handoff for certified runtime preparation commands.",
    )
    return ap


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
        language_hint=args.language_hint,
    )
    print(f"[v3] base-image: {choice.image} (py {choice.minor}) — {choice.reason}")
    base_image = choice.image

    test_intent = discover_test_dependency_intent(args.repo)
    pytest_environment = (
        {"PYTEST_ADDOPTS": " ".join(test_intent.pytest_addopts)}
        if test_intent.pytest_addopts else None
    )
    if test_intent.pytest_addopts:
        print("[v3] pytest-policy: " + " ".join(test_intent.pytest_addopts))

    # Resolve platform identity before BELIEF construction.  The stable image
    # id produced by Sandbox is shared with the scratch graph container, so a
    # concurrent worker retagging python:X.Y-slim cannot make graph discovery
    # and live execution observe different architectures.
    sandbox = Sandbox(
        base_image=base_image,
        workdir="/app",
        platform=choice.platform_override,
        seed_dir=args.repo,
        enable_cache_volume=True,
        command_timeout_seconds=_positive_env_seconds(
            "V3_COMMAND_TIMEOUT_SECONDS", 600
        ),
        ensure_native_platform=choice.platform_override is None,
        environment=pytest_environment,
    )

    # ── 2. BELIEF: build the dep-graph; the LLM classifier proposes typed nodes
    classify = make_construction_classifier(_complete)
    if test_intent.needed_groups:
        print(
            "[v3] test-dependency-groups: "
            + ", ".join(sorted(test_intent.needed_groups))
        )
    graph = None
    try:
        _advisory, graph = build_advisory_for_repo(
            args.repo,
            sandbox.base_image_ref,
            target_python=choice.minor,
            classify=classify,
            needed_extras=test_intent.needed_groups,
            platform=sandbox.platform,
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

    # ── 3-6. Real container + the v3 loop ────────────────────────────────────
    gates_seen: list = []
    # Task 8d: only built when --trace-out is given — RunTracer only OBSERVES
    # (see run_trace.py's module docstring), so passing tracer=None (the
    # run_v3 default) when --trace-out is omitted keeps this driver's
    # behavior byte-identical to before Task 8d.
    loop_mode = (
        "v3_graph_execute_agent"
        if args.execution_mode == "incremental"
        else "v3_graph_typed_repair"
    )
    tracer = RunTracer(repo=args.repo, loop_mode=loop_mode) if args.trace_out else None
    incremental_executor = None
    if args.execution_mode == "incremental":
        restore_named = getattr(sandbox, "restore_checkpoint", None)
        incremental_executor = IncrementalPlanExecutor(
            run_install_script=sandbox.run_install_script,
            exec_readonly=sandbox.exec_readonly,
            restore_base=(
                (lambda: restore_named("base"))
                if restore_named is not None
                else sandbox.reset_to_base
            ),
            create_checkpoint=getattr(sandbox, "create_checkpoint", None),
            restore_checkpoint=restore_named,
            drop_checkpoint=getattr(sandbox, "drop_checkpoint", None),
            create_candidate=getattr(sandbox, "create_candidate_container", None),
            candidate_run_install_script=getattr(
                sandbox, "candidate_run_install_script", None
            ),
            candidate_exec_readonly=getattr(sandbox, "candidate_exec_readonly", None),
            promote_candidate=getattr(sandbox, "promote_candidate", None),
            abort_candidate=getattr(sandbox, "abort_candidate", None),
        )
    agent = (
        GraphExecuteAgent(client, model)
        if args.execution_mode == "incremental"
        else V3BuildAgent(client, model)
    )
    try:
        final_map, stop = run_v3(
            agent,
            maintainer=DeterministicMaintainer(v3_only=True),
            initial_world_map=world_map,
            ledger=ActionLedger(),
            sandbox_execute=sandbox.execute,
            probe=lambda: probe_env(sandbox.exec_readonly),
            manifest=manifest,
            exec_readonly=sandbox.exec_readonly,
            reset_to_base=sandbox.reset_to_base,
            run_install_script=sandbox.run_install_script,
            incremental_execute=(
                incremental_executor.execute if incremental_executor is not None else None
            ),
            candidate_validate=(
                incremental_executor.validate_candidate
                if incremental_executor is not None else None
            ),
            max_cycles=args.max_cycles,
            enable_gate_observability=True,            # report both maturity gates on exit
            gate_observer=gates_seen.append,
            repo_path=args.repo,                       # seeds the diagnosis router's RepoContext
            tracer=tracer,
        )
    finally:
        try:
            close = getattr(sandbox, "close", None)
            if close is not None:
                close()
            elif getattr(sandbox, "container", None) is not None:
                sandbox.container.stop()
                sandbox.container.remove()
        except Exception:
            pass

    # ── 7. ARTIFACT + report ─────────────────────────────────────────────────
    dep_graph = getattr(final_map, "dep_graph", None)
    unresolved = ([n.id for n in dep_graph.nodes if n.state is State.MISSING]
                  if dep_graph is not None else [])
    script_text = ""
    if dep_graph is not None:
        script_text = render_build_script(dep_graph, getattr(final_map, "manual_blocks", ()))
        with open(args.out, "w") as fh:
            fh.write(script_text)
        print(f"[v3] wrote certified setup.sh -> {args.out}")

    if args.runtime_out:
        with open(args.runtime_out, "w") as fh:
            json.dump(
                _runtime_handoff(
                    dep_graph,
                    pytest_addopts=test_intent.pytest_addopts,
                ),
                fh,
                indent=2,
            )
        print(f"[v3] wrote runtime handoff -> {args.runtime_out}")

    if gates_seen:
        for g in gates_seen[-1]:
            print(f"[v3] gate: {g}")
    print(f"stop_reason={stop} unresolved={unresolved}")
    ok = _e2e_succeeded(stop, unresolved, gates_seen)
    print("V3 E2E:", "PASS" if ok else "FAIL")

    # ── Task 8d: emit the RunTrace + verify report (ADDITIVE — the PASS/FAIL
    # logic above is unchanged; this only fires when --trace-out was given,
    # since `tracer` is None otherwise and there is nothing to snapshot).
    if tracer is not None:
        trace, report = finalize_trace(tracer, stop, gates_seen, script_text)
        with open(args.trace_out, "w") as fh:
            json.dump(trace.to_dict(), fh, indent=2)
        print(f"[v3] wrote run trace -> {args.trace_out}")
        for key in ("canonical", "artifact", "local_import"):
            errs = report[key]
            print(f"[v3] verify_{key}: {'CLEAN' if not errs else errs}")

    return 0 if ok else 1


def main_with_args(argv) -> int:
    args = _build_arg_parser().parse_args(argv)
    return _run(args)


def main() -> int:
    return main_with_args(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
