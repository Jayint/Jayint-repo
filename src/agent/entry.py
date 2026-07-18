"""Production entry for the react arm (spec §14). Builds the arm's OWN docker adapters over the
shared Sandbox and assembles planner + loop. `certify` runs install-tier layers only (no TESTS
layer) so the suite is not run twice; `run_tests` is the single authoritative pytest run fed
through the 80% verdict. No arm-C imports."""
from __future__ import annotations

import os
import pathlib

from graph.python.read import repo_modules
from graph.core.certify import EXECUTION_LAYER_ORDER, certify_all
from graph.diagnose import RepoContext
from graph.discovery_expand import expand_discovery
from graph.contracts.executor import CommandResult
from graph.graph_context import render_graph_context
from graph.graph_enrich import certify_only, enrich
from graph.model import Layer
from src.constants import VERIFY_TEST_CMD           # shared canonical "python -m pytest -q" (neutral leaf)
from src.agent.gate import test_verdict
from src.agent.history import History
from src.agent.log import ReactLog
from src.agent.loop import RunResult, run_react
from src.agent.prompt import ReactPlanner
from src.agent.script_prep import strip_graph_framing

# Install-tier layers only — drop TESTS so certify never re-runs the suite (spec §5).
_INSTALL_LAYERS = tuple(l for l in EXECUTION_LAYER_ORDER if l is not Layer.TESTS)

# Hard cap on a single pytest run so a hanging suite can't stall the whole benchmark. Matches the
# eval harness cap (1800s) so a WORKING-but-slow seed isn't read as false-0 by a harsher internal
# cap than the eval applies — that false-0 was what drove the agent to gut a working closure (darts).
# Override with REACT_TEST_TIMEOUT. This is only the COARSE whole-suite backstop now — the per-test
# timeout below is the primary bound (see _test_command).
_TEST_TIMEOUT_S = int(os.getenv("REACT_TEST_TIMEOUT", "1800"))

# Per-test timeout (seconds), matching the ratbench OFFICIAL scorer's `--timeout=120
# --timeout-method=signal`. This is the KEY fix for the timeout-bound repos: the coarse whole-suite
# `timeout` above kills the ENTIRE run on the first hang and, with no pytest summary line printed,
# scores a 99%-passing suite as 0 (Qiskit); a hanging network test zeroes the whole suite
# (websockets). A per-test timeout instead fails just that one test and lets the suite FINISH with a
# real count — and it matches how the benchmark actually scores. Override with REACT_PER_TEST_TIMEOUT.
_PER_TEST_TIMEOUT_S = int(os.getenv("REACT_PER_TEST_TIMEOUT", "120"))


def _test_command() -> str:
    """The react test-gate shell. Best-effort ensures `pytest-timeout`, then appends the scorer's
    per-test timeout (`--timeout=<N> --timeout-method=signal`) ONLY if the plugin imports — so a
    hanging test fails ALONE (not the coarse whole-suite timeout zeroing the run) and a container
    lacking the plugin degrades to plain pytest instead of an "unrecognized arguments" hard failure.
    The whole run stays bounded by the coarse `timeout` backstop. Deliberately NOT `-n auto`: the
    scorer runs serially, and xdist can flip parallel-unsafe tests — a fresh divergence from it."""
    ensure = "python -m pip install -q pytest-timeout >/dev/null 2>&1 || true"
    guard = ('F=""; python -c "import pytest_timeout" >/dev/null 2>&1 && '
             f'F="--timeout={_PER_TEST_TIMEOUT_S} --timeout-method=signal"')
    run = f"{VERIFY_TEST_CMD} $F"
    bounded = (f"if command -v timeout >/dev/null 2>&1; then timeout -k 10 {_TEST_TIMEOUT_S} {run}; "
               f"else {run}; fi")
    return f"{ensure}; {guard}; {bounded}"


class _ExecAdapter:
    """certify_all wants an executor.run(cmd) -> CommandResult; wrap the sandbox's (rc,out)."""
    def __init__(self, exec_readonly):
        self._e = exec_readonly

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        rc, out = self._e(command)
        return CommandResult(command, rc, out if rc == 0 else "", "" if rc == 0 else out)


def docker_adapters(sandbox, test_threshold: float = 0.9):
    def reset():
        sandbox.reset_to_base()

    def run_script(script: str) -> RunResult:
        r = sandbox.run_install_script(script)
        return RunResult(ok=(r.rc == 0), failing_command=r.failing_command, output=r.stderr or "",
                         lineno=r.lineno)

    def certify(graph):
        return certify_all(graph, _ExecAdapter(sandbox.exec_readonly), layer_order=_INSTALL_LAYERS)

    def exec_readonly(cmd):
        return sandbox.exec_readonly(cmd)

    def run_tests():
        # Per-test timeout (matches the scorer) so a hang fails ONE test, not the whole suite; the
        # coarse `timeout` backstop still bounds the total. See _test_command.
        rc, out = sandbox.exec_readonly(_test_command())
        if rc in (124, 137):               # 124 = SIGTERM at the cap, 137 = SIGKILL 10s later
            # Surface the timeout as a DISTINCT signal + an explicit anti-strip hint: a timeout means
            # the env may be fine but the suite is slow, so removing installs to "fix" it only makes
            # things worse (the darts failure). Appended last so it survives tail-truncation.
            out = (f"{out or ''}\n[react] TIMEOUT: pytest exceeded {_TEST_TIMEOUT_S}s and was killed. "
                   f"The environment may be correctly set up but the suite is just too slow to finish "
                   f"in the cap — do NOT remove installs to make it faster (a smaller env passes fewer "
                   f"tests, not more).")
        return test_verdict(out, threshold=test_threshold)

    return reset, run_script, certify, exec_readonly, run_tests


# Files that declare a repo's dependencies / build / test setup — surfaced in the ENVIRONMENT block
# so the agent sees where they live (esp. in a monorepo) instead of guessing from one error.
_MANIFEST_NAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "noxfile.py", "Pipfile", "poetry.lock",
    "uv.lock", "environment.yml", "environment.yaml", "Makefile"})
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".idea", ".eggs", "build", "dist", ".ruff_cache", ".hypothesis", ".git"})


def _repo_layout(repo_path, *, max_top=40, max_manifests=40) -> str:
    """A curated view of the repo (from the HOST checkout, read-only): top-level entries + every
    dependency/config manifest up to depth 3 — the structural signal that fixes 'where is the
    package / which dir owns pyproject.toml' without an exploration turn."""
    root = pathlib.Path(repo_path)
    if not root.is_dir():
        return ""
    top = []
    for p in sorted(root.iterdir(), key=lambda q: (q.is_file(), q.name.lower())):
        if p.name in _SKIP_DIRS or p.name.startswith("."):
            continue
        top.append(p.name + "/" if p.is_dir() else p.name)
    manifests = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        rel = pathlib.Path(dirpath).relative_to(root)
        if len(rel.parts) > 3:
            dirnames[:] = []
            continue
        for f in filenames:
            if f in _MANIFEST_NAMES or (f.startswith("requirements") and f.endswith(".txt")):
                manifests.add(f if str(rel) == "." else str(rel / f))
    lines = ["    top level: " + ", ".join(top[:max_top])] if top else []
    if manifests:
        lines.append("    dependency/config files: " + ", ".join(sorted(manifests)[:max_manifests]))
    return "\n".join(lines)


def project_name(repo_path) -> "str | None":
    """The repo's OWN distribution name (pyproject `[project].name`, poetry, or setup.cfg). The HOST
    reads it from the checkout so the anti-cheat gate can reject a setup.sh that installs the project
    under test from a package index instead of the local source (a live false green: the agent shipped
    `pip install itsdangerous`, went 297/297 green, and never installed the repo). Best-effort: an
    unparseable/absent manifest returns None, which simply disables that gate."""
    if not repo_path:
        return None
    root = pathlib.Path(repo_path)
    try:
        import tomllib
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        name = (data.get("project") or {}).get("name")
        if not name:
            name = ((data.get("tool") or {}).get("poetry") or {}).get("name")
        if name:
            return str(name).strip()
    except Exception:
        pass
    try:
        import configparser
        cp = configparser.ConfigParser()
        cp.read(root / "setup.cfg")
        name = cp.get("metadata", "name", fallback=None)
        if name:
            return str(name).strip()
    except Exception:
        pass
    return None


def _gather_env_info(sandbox, repo_path) -> str:
    """Render the ENVIRONMENT block: base image + OS, the in-container repo/working dir, and the
    repo layout. Best-effort — any part that can't be gathered is simply omitted."""
    base = getattr(sandbox, "base_image", None) or "?"
    workdir = getattr(sandbox, "workdir", "/app")
    os_family = ""
    try:                               # one read-only probe of the running container
        _rc, out = sandbox.exec_readonly(". /etc/os-release 2>/dev/null; printf '%s' \"$PRETTY_NAME\"")
        os_family = (out or "").strip().splitlines()[0].strip() if (out or "").strip() else ""
    except Exception:
        os_family = ""
    lines = [f"  Base image : {base}" + (f" ({os_family})" if os_family else ""),
             f"  Working dir: {workdir}   (setup.sh runs here; the repo is checked out AT this path — "
             f"its files are directly under {workdir}, e.g. {workdir}/pyproject.toml)"]
    layout = _repo_layout(repo_path) if repo_path else ""
    if layout:
        lines += ["  Repo layout:", layout]
    return "\n".join(lines)


def rung_flags(graph_context: bool = False) -> tuple[bool, bool]:
    """(render the graph?, grow the graph?) — the ablation rungs. Pure; reads only the env.

    G2 = render only (frozen topology). G3 = render + observation-driven growth (§7). Two flags
    and not one, so a G3 lift is never misattributed to the renderer — the ablation invariant
    (spec §2). `graph_context` is the pre-existing bool param; REACT_GRAPH_CONTEXT lets a VM run
    flip it without touching call sites.

    G3 IMPLIES G2. Growing the graph without rendering it is not a rung — it is a silent no-op
    arm: the loop pays for enrich + expand + certify on every turn and the agent never sees a
    byte of it, so the run LOOKS like a valid G3 datapoint while measuring nothing at all. The
    rungs stay separable in the only direction that matters: G2 renders WITHOUT growing, which
    is precisely what isolates structure from growth.
    """
    want_update = os.getenv("REACT_GRAPH_UPDATE") == "1"
    want_ctx = (bool(graph_context)
                or os.getenv("REACT_GRAPH_CONTEXT") == "1"
                or want_update)
    return want_ctx, want_update


def build_graph_hooks(want_ctx: bool, want_update: bool, exec_readonly, repo_path=None) -> dict:
    """The four callables that ARE the graph arm. Everything else is identical across rungs.

    Returned as a dict so a caller can splat it straight into `run_react(...)`:
    `graph_context` (the planner seam) plus `enrich_fn`/`expand_fn`/`certify_new_fn` (the loop's
    growth hooks). On G0/G1 every one of them is None, which is what makes the prompt bytes
    byte-identical to every baseline already run.

    Factored out of `run_react_arm` so a test or a demo builds the SAME hooks the real arm does,
    rather than a plausible-looking replica that can drift away from it.
    """
    ctx = None
    if want_ctx:
        def ctx(g, result, causes, prev_states):
            return render_graph_context(g, result, causes, prev_states, repo_path=repo_path)

    enrich_fn = expand_fn = certify_new_fn = None
    if want_update:
        # RepoContext MUST come from repo_modules.top_level_names — the sys.path-accurate set.
        # diagnose.py:48-52 explicitly warns NOT to use scan.local_module_names, which is
        # deliberately over-broad (it harvests every .py stem) and makes the router give up
        # silently on azure/traitlets/jinja2. Mirrors orchestrator.py:740-751. Built only under
        # REACT_GRAPH_UPDATE — it is the sole consumer, and a repo walk is wasted work on G0/G1/G2.
        repo_ctx = RepoContext(
            local_names=repo_modules.top_level_names(repo_path) if repo_path else frozenset(),
            invalid_names=frozenset(),
            collisions=repo_modules.stem_collisions(repo_path) if repo_path else {},
        )
        _ex = _ExecAdapter(exec_readonly)

        def enrich_fn(g, r, causes):
            return enrich(g, r, causes, repo_ctx)

        def expand_fn(g, new_ids, already):
            return expand_discovery(g, new_ids, _ex, already)

        def certify_new_fn(g, new_ids):
            return certify_only(g, new_ids, _ex)

    return {"graph_context": ctx, "enrich_fn": enrich_fn,
            "expand_fn": expand_fn, "certify_new_fn": certify_new_fn}


def run_react_arm(graph, *, sandbox, client, model, repo_path=None,
                  graph_context: bool = False, trace_out=None, log=None, max_steps: int = 30,
                  test_threshold: float = 0.9, initial_script: str | None = None):
    owns_log = log is None
    log = log or ReactLog(trace_path=trace_out)
    reset, run_script, certify, exec_readonly, run_tests = docker_adapters(sandbox, test_threshold)

    # G2 = render only (frozen topology). G3 = render + observation-driven growth (§7). Two
    # flags, not one, so a G3 lift is never misattributed to the renderer — the ablation
    # invariant (spec §2). `graph_context` is the pre-existing bool param; REACT_GRAPH_CONTEXT
    # lets a VM run flip it without touching call sites.
    want_ctx, want_update = rung_flags(graph_context)
    hooks = build_graph_hooks(want_ctx, want_update, sandbox.exec_readonly, repo_path)
    ctx = hooks["graph_context"]
    enrich_fn, expand_fn = hooks["enrich_fn"], hooks["expand_fn"]
    certify_new_fn = hooks["certify_new_fn"]

    env_info = _gather_env_info(sandbox, repo_path)
    planner = ReactPlanner(client, model, graph_context=(ctx if want_ctx else None),
                           log=log, env_info=env_info)
    # No LLM observation compressor: the grouped blocker history view (render_history) is the
    # compaction now — it distills each step to a blocker signature + score, and never renders the
    # per-step body, so a Tier-2 summariser would only fire wasted LLM calls whose output is unused.
    history = History(log=log)
    # Seed step-0 from a pre-generated setup.sh (repair-only ablation); strip the graph-primary
    # header so a copy from a prior v3 run doesn't carry "DO NOT EDIT / edit the graph" contradictions.
    seed = strip_graph_framing(initial_script) if initial_script is not None else None
    try:
        return run_react(graph, reset=reset, run_script=run_script, certify=certify,
                         exec_readonly=exec_readonly, run_tests=run_tests, planner=planner,
                         history=history, log=log, max_steps=max_steps, _initial_script=seed,
                         project_name=project_name(repo_path),
                         enrich_fn=enrich_fn, expand_fn=expand_fn, certify_new_fn=certify_new_fn)
    finally:
        if owns_log:
            log.close()
