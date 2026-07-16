"""run_v3_e2e — the single legible entrypoint of the v3 (GSM) environment builder.

This is the whole story in one driver:

  1. SELECT      pick (or honor an explicit) base image, pin it to the repo's
                 ``requires-python``, and normalize to a ``-slim`` variant —
                 one decision feeds the sandbox boot AND the dep-graph build.
  2. BELIEF      build the dependency graph (static evidence + a bounded LLM
                 classifier that proposes typed Config/Service nodes).
  3. PROJECTION  the graph is materialized into ONE install-only setup.sh.
  4. INNER LOOP  run_v3 executes the script block-by-block, the host certifies
                 each node, and on a failed block the V3BuildAgent proposes ONE
                 typed PatchProposal (gated by PatchGate, bounded by repair_loop).
  5. INSTALL GATE  every cycle resets the container to a clean base and
                 replays the whole rendered setup.sh (Model B — run_v3's sole
                 executor, unconditional); the LATEST cycle's replay result is
                 the binding installability proof (no separate terminal-replay
                 step — see ``src.envstate.gates.evaluate_installability_gate``).
  6. TEST GATE   the done-gate runs real pytest; observability reports both gates.
  7. ARTIFACT    the final certified graph is rendered to setup.sh.

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
import platform as _platform
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
from src.envstate.classify_services_clean import make_construction_classifier
from src.envstate.base_image_selection import choose_base_image
from src.envstate.llm_dist_guess import make_dist_guesser
from src.envstate.llm_response import complete_with_retry
from src.envstate.jsonutil import extract_json_object
from src.envstate.run_trace import RunTracer
from src.envstate.proof import finalize_trace
from python_deps.depgraph.advise import build_advisory_for_repo
from python_deps.depgraph.build_script import (
    render_build_script,
    render_service_start_script,
)
from python_deps.depgraph.schema import State


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
        "--services-out", default=None, dest="services_out",
        help="Where to write the service-start ENTRYPOINT-wrapper script "
             "(render_service_start_script). Only written when the "
             "V3_INCLUDE_SERVICES=1 env var is set AND the graph has at least "
             "one reciped SERVICE node; omitted otherwise (and by default).",
    )
    ap.add_argument(
        "--trace-out", default=None, dest="trace_out",
        help="Where to write the run's RunTrace JSON (Task 8 proof harness). "
             "Omitted -> no tracer is built and behavior is unchanged.",
    )
    ap.add_argument(
        "--construction-only", action="store_true", dest="construction_only",
        help="Build the graph (LLM base-image + service/config classify STILL "
             "INCLUDED) and render the INITIAL setup.sh, then STOP — skip the "
             "run_v3 repair loop entirely. Measures how well first-pass "
             "construction alone provisions the repo; the caller (e.g. the "
             "ratbench harness) then runs pytest against that first build script.",
    )
    ap.add_argument(
        "--arm", default="v3", choices=("v3", "react"),
        help="Repair arm: 'v3' (default — the graph-scheduler loop) or 'react' "
             "(flat ReAct script-repair — one loop that patches the build script).",
    )
    ap.add_argument(
        "--graph-context", action="store_true", dest="graph_context",
        help="react arm only: feed certified graph state into the planner "
             "(graph-guided variant).",
    )
    ap.add_argument(
        "--max-steps", type=int, default=30, dest="max_steps",
        help="react arm only: max ReAct steps (full script re-runs) before GIVEUP.",
    )
    ap.add_argument(
        "--test-threshold", type=float, default=0.9, dest="test_threshold",
        help="react arm only: fraction of executed tests that must pass to certify DONE "
             "(default 0.9). Sweep this to trade accuracy vs cost.",
    )
    ap.add_argument(
        "--seed-script", default=None, dest="seed_script",
        help="react arm only: run the repair loop starting from THIS pre-generated setup.sh "
             "instead of rendering a fresh one; SKIPS graph construction (repair-only "
             "ablation). Requires an explicit --base-image (not auto).",
    )
    return ap


def _resolve_llm_endpoint(model: str) -> tuple[str | None, str | None]:
    """Pick the ``(api_key, base_url)`` for *model*, preferring the provider that
    matches the model slug.

    A ``minimax-*``/``abab-*`` slug (or ``LLM_API_PROVIDER=minimax``) routes to
    MINIMAX_API_KEY/MINIMAX_API_BASE **even when an OPENROUTER_API_KEY is also
    present** — otherwise the old first-non-empty fallback would send a MiniMax
    run to OpenRouter (wrong provider, and the ``minimaxi`` thinking-off gate
    would never fire). Mirrors ``libkit.config.get_llm_config``'s MiniMax-first
    selection order. Every other model keeps the original OpenRouter -> MiniMax
    -> OpenAI fallback.
    """
    provider = os.getenv("LLM_API_PROVIDER", "").strip().lower()
    if provider == "minimax" or model.lower().startswith(("minimax", "abab")):
        return os.getenv("MINIMAX_API_KEY"), os.getenv("MINIMAX_API_BASE")
    api_key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("MINIMAX_API_KEY")
               or os.getenv("OPENAI_API_KEY"))
    base_url = (os.getenv("OPENROUTER_API_BASE") or os.getenv("MINIMAX_API_BASE")
                or os.getenv("OPENAI_API_BASE"))
    return api_key, base_url


def _target_arch(platform_override: str | None) -> dict:
    """Map a docker --platform string (or None -> host default) to the
    {dpkg, uname} arch dict translate_service's exotic path fills into
    binary-download URLs. platform_override is "linux/amd64" or None."""
    token = (platform_override or "").rsplit("/", 1)[-1].lower()   # "amd64" | "arm64" | ""
    if not token:
        machine = _platform.machine().lower()                       # host default target
        token = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return ({"dpkg": "arm64", "uname": "aarch64"} if token == "arm64"
            else {"dpkg": "amd64", "uname": "x86_64"})


def _include_services() -> bool:
    """The V3_INCLUDE_SERVICES gate (default OFF — see build_script.
    render_build_script's docstring). Read ONLY here, at the impure
    orchestration boundary; render_build_script/render_service_start_script
    stay pure and take an explicit ``include_services``/graph argument."""
    return os.getenv("V3_INCLUDE_SERVICES") == "1"


def _uv_sources_enabled() -> bool:
    """The V3_UV_SOURCES gate (default OFF). Read ONLY here, at the impure
    orchestration boundary; build_dep_graph/build_advisory_for_repo stay pure
    and take an explicit ``uv_sources_enabled`` argument (same convention as
    ``_include_services`` above).

    NOT SAFE for scored/benchmark runs when turned ON. Setting V3_UV_SOURCES=1
    threads a repo's `[tool.uv.sources]` overrides (git/workspace/url/path/
    index) into the resolver's synthetic pyproject so ``uv lock`` can resolve
    them — but the package layer still installs everything by bare
    ``name==version`` from (at least) six independent sites:
    ``emit.py``'s ``_is_emittable`` (~line 84, blind to
    ``data['uninstallable']``), ``probe.py``'s ``_install_cmd`` (bare
    ``uv pip install --system ...`` with no ``--no-deps``, so an ordinary
    public dependency's own transitive deps can drag in the public namesake
    of a sourced package at install time), ``certify.py``'s ``certify``
    (~line 90, flips MISSING -> SATISFIED off ANY successful
    ``python -m pip show <name>``, also blind to ``uninstallable``),
    ``resolve.py``'s ``resolve_closure`` (~line 637, a `[tool.uv.sources]`
    override is a GLOBAL table applied over the whole resolved graph, so a
    name that is only a transitive dependency can still lose its override),
    and ``build_script.py``'s soft-requirements / pytest-bootstrap renders
    (~lines 165, 397) plus ``populate.py``'s PEP-517 editable-install build
    isolation (~line 52-60) — each can independently resolve a name this repo
    overrode straight from public PyPI. So a non-PyPI package (a git fork, a
    workspace member with no PyPI existence, a private-index package) can be
    silently replaced by its public PyPI namesake and still score green. This
    flag exists for DEVELOPMENT of the source-aware install layer, never for
    a benchmarked/scored run — leave it off unless you are actively working
    on that layer."""
    return os.getenv("V3_UV_SOURCES") == "1"


def _maybe_write_services_script(graph, args) -> None:
    """Write the service-start ENTRYPOINT-wrapper alongside setup.sh, iff the
    flag is on, a --services-out path was given, and the graph actually has a
    reciped SERVICE node (render_service_start_script returns "" otherwise —
    the common, zero-service case writes nothing, matching setup.sh's own
    no-op)."""
    if not args.services_out or not _include_services() or graph is None:
        return
    text = render_service_start_script(graph)
    if not text:
        return
    with open(args.services_out, "w") as fh:
        fh.write(text)
    print(f"[v3] wrote service start script -> {args.services_out}")


def _run(args) -> int:  # noqa: C901 — deliberately one all-in-one driver
    # ── 1. LLM client (OAI-compatible; provider chosen to match the model slug,
    #       else OpenRouter -> MiniMax -> OpenAI fallback) ───────────────────────
    model = args.model or os.getenv("LLM_MODEL", "gpt-4o")
    api_key, base_url = _resolve_llm_endpoint(model)
    if not api_key:
        print(f"ERROR: no API key for model {model!r} — set OPENROUTER_API_KEY / "
              "MINIMAX_API_KEY / OPENAI_API_KEY (a minimax-* model needs "
              "MINIMAX_API_KEY + MINIMAX_API_BASE).",
              file=sys.stderr)
        return 2
    client = OpenAI(
        api_key=api_key, base_url=base_url or None, max_retries=0,
        timeout=Timeout(connect=10.0, read=float(os.getenv("LLM_READ_TIMEOUT", "120")),
                        write=30.0, pool=10.0),
    )

    if args.seed_script and args.base_image == "auto":
        print("ERROR: --seed-script requires an explicit --base-image (not auto).", file=sys.stderr)
        return 2

    seed_script_text = None
    if args.seed_script:
        try:
            with open(args.seed_script) as fh:
                seed_script_text = fh.read()
        except OSError as exc:
            print(f"ERROR: cannot read --seed-script {args.seed_script!r}: {exc}", file=sys.stderr)
            return 2

    # ── 1.5 SELECT: pick + pin the base image (auto) or honor an explicit tag ─
    choice = choose_base_image(
        args.repo, client, model,
        explicit=(None if args.base_image == "auto" else args.base_image),
    )
    print(f"[v3] base-image: {choice.image} (py {choice.minor}) — {choice.reason}")
    base_image = choice.image

    # ── 2. BELIEF: build the dep-graph (skipped in --seed-script repair-only mode) ──────
    arch = _target_arch(choice.platform_override)
    if args.seed_script:
        from python_deps.depgraph.schema import DepGraph
        graph = DepGraph()          # empty — repair-only ablation doesn't use graph state
        print(f"[v3] seed-script mode: repair-only on {args.seed_script} "
              f"(construction skipped, empty graph)")
    else:
        classify = make_construction_classifier(client, model, arch)
        # Install-lane dist-guesser: proposes extra import->distribution
        # candidates ONLY on a pipreqs MISS; every candidate is still
        # RECORD-grounded downstream. temperature=0 is REQUIRED (determinism —
        # the guesser caches per (import, symbols), so the run must not depend
        # on sampling). Mirrors the orchestrator's `_complete` closure; built
        # here at the orchestration boundary so `python_deps` stays LLM-free.
        def _dist_complete_fn(messages):
            text, _usage, _resp = complete_with_retry(
                client, model, messages,
                accept=lambda t: extract_json_object(t) is not None,
                temperature=0, max_attempts=2,
            )
            return text

        dist_guesser = make_dist_guesser(_dist_complete_fn)
        graph = None
        try:
            _advisory, graph = build_advisory_for_repo(
                args.repo, base_image, target_python=choice.minor, classify=classify,
                uv_sources_enabled=_uv_sources_enabled(),
                llm_dist_guesser=dist_guesser,
            )
            n = sum(1 for _ in graph.nodes) if graph is not None else 0
            print(f"[v3] dep-graph: {n} nodes")
        except Exception as exc:  # graceful degradation — graph is advisory at construction
            print(f"[v3] dep-graph build failed (graceful degradation): {exc}", file=sys.stderr)
            graph = None

    # ── construction-only short-circuit ──────────────────────────────────────
    # Render the FIRST-PASS setup.sh from the just-constructed graph and STOP —
    # no repair loop, no fresh-replay cycles. The LLM-driven construction above
    # (base-image selection + service/config classify) is UNCHANGED; only the
    # iterative repair is skipped, so this isolates first-pass construction
    # quality. The caller builds the image from this setup.sh and runs pytest.
    if args.construction_only:
        script_text = (render_build_script(graph, (), include_services=_include_services())
                       if graph is not None else "")
        with open(args.out, "w") as fh:
            fh.write(script_text)
        n = sum(1 for _ in graph.nodes) if graph is not None else 0
        print(f"[v3] construction-only: wrote INITIAL setup.sh ({n} nodes) -> {args.out}")
        _maybe_write_services_script(graph, args)
        print("stop_reason=construction_only unresolved=[]")
        print("V3 E2E:", "CONSTRUCTION_ONLY")
        return 0

    manifest = parse_manifests(args.repo)
    world_map = initial_map(
        base_image=base_image, workdir="/app", language="unknown",
        build_system=manifest.build_system if manifest is not None else "unknown",
        repo_layout=(), dep_graph=graph,
    )

    # ── 3-6. Real container + the v3 loop ────────────────────────────────────
    # react arm = the repair-ablation, run concurrently by the ratbench scheduler → isolate the
    # pip/uv/apt cache per run so parallel repos don't race one shared volume or cross-contaminate.
    sandbox = Sandbox(base_image=base_image, workdir="/app",
                       platform=choice.platform_override, seed_dir=args.repo,
                       enable_cache_volume=True, isolate_cache=(args.arm == "react"))

    # ── arm react — flat ReAct script-repair: ONE loop that patches the build
    #     SCRIPT (script-primary). Reuses the SAME construction above (base
    #     image, dep-graph, sandbox); --graph-context feeds certified graph
    #     state into the planner (the graph-guided variant). ─────────────────
    if args.arm == "react":
        from src.react_repair.entry import run_react_arm
        if args.graph_context:
            print("[react] WARNING: --graph-context is not yet implemented; "
                  "running BASELINE (no graph guidance). The run is identical to omitting the flag.")
        try:
            outcome, script_text, _ = run_react_arm(
                graph, sandbox=sandbox, client=client, model=model, repo_path=args.repo,
                max_steps=args.max_steps, test_threshold=args.test_threshold,
                graph_context=args.graph_context, trace_out=args.trace_out,
                initial_script=seed_script_text)
        finally:
            try:
                if getattr(sandbox, "container", None) is not None:
                    sandbox.close()
            except Exception:
                pass
        with open(args.out, "w") as fh:
            fh.write(script_text)
        print(f"[v3] (react) wrote setup.sh -> {args.out}")
        print(f"stop_reason={outcome}")
        ok = outcome == "DONE"                          # host-owned done (script green + tests ≥80%)
        print("V3 E2E:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    gates_seen: list = []
    # Task 8d: only built when --trace-out is given — RunTracer only OBSERVES
    # (see run_trace.py's module docstring), so passing tracer=None (the
    # run_v3 default) when --trace-out is omitted keeps this driver's
    # behavior byte-identical to before Task 8d.
    tracer = RunTracer(repo=args.repo) if args.trace_out else None
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
            reset_to_base=sandbox.reset_to_base,
            run_install_script=sandbox.run_install_script,
            enable_gate_observability=True,            # report both maturity gates on exit
            gate_observer=gates_seen.append,
            repo_path=args.repo,                       # seeds the diagnosis router's RepoContext
            tracer=tracer,
        )
    finally:
        try:
            if getattr(sandbox, "container", None) is not None:
                sandbox.close()
        except Exception:
            pass

    # ── 7. ARTIFACT + report ─────────────────────────────────────────────────
    dep_graph = getattr(final_map, "dep_graph", None)
    unresolved = ([n.id for n in dep_graph.nodes if n.state is State.MISSING]
                  if dep_graph is not None else [])
    script_text = ""
    if dep_graph is not None:
        script_text = render_build_script(
            dep_graph, getattr(final_map, "manual_blocks", ()),
            include_services=_include_services(),
        )
        with open(args.out, "w") as fh:
            fh.write(script_text)
        print(f"[v3] wrote certified setup.sh -> {args.out}")
        _maybe_write_services_script(dep_graph, args)

    if gates_seen:
        for g in gates_seen[-1]:
            print(f"[v3] gate: {g}")
    print(f"stop_reason={stop} unresolved={unresolved}")
    ok = stop in ("done", "done_flag", "planner_done") and not unresolved
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
