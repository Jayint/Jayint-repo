"""④ RUN — drive the certified graph against the live container + read-only probe (spec §4C loop/execute.py).

Folded (3b-6) from five leaves: the env probe (extractor + snapshot), the live
certify/soname drive (depgraph_live's LIVE half), the strict-shell block runner
(script_runner), and its pure text helper (text_util). The incremental-ablation
trio (block_emit + emit_drain + repair_failed_nodes) is PARKED next door in
execute_ablation.py (R7) and is NOT part of the canonical run path.

This is the ONLY module that bridges graph (pure) and the live agent container.
Mutations go through build_agent.run_recipe / block commands; certification runs
through a read-only executor — host-owns-truth (certify.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from graph.contracts.executor import CommandResult
from graph.core.certify import certify_all
from graph.evidence_log import Evidence, EvidenceBundle
from graph.mutate.block import Block
from src.orchestrate.loop.world_model import Fact



# === text_util.py: pure head+tail output truncation (no LLM/Docker imports) ===
# Mirror build_agent._truncate_output: head keeps tracebacks/setup, tail keeps
# the pytest '=== N passed ===' summary line.
_HEAD = 1500
_TAIL = 800


def truncate_output(output: str, head: int = _HEAD, tail: int = _TAIL) -> str:
    """Head+tail truncation preserving the start and the tail (traceback/pytest summary)."""
    s = output or ""
    if len(s) <= head + tail:
        return s
    return (
        s[:head].rstrip()
        + "\n...[output truncated]...\n"
        + s[-tail:].lstrip()
    )



# === extractor.py: read-only env field extractor (design §12 probe list) ===
ProbeExecutor = Callable[[str], Tuple[int, str]]

# Curated build/config tools that appear in system-layer failure signatures.
SYSTEM_TOOL_PROBES: tuple[str, ...] = (
    "gcc", "g++", "cc", "make", "cmake", "pkg-config",
    "pg_config", "mysql_config", "mariadb_config",
    "curl-config", "xml2-config", "xslt-config", "krb5-config", "icu-config",
)

# field_name -> read-only command (design §12 extractor list, V1 subset)
EXTRACTOR_COMMANDS: Dict[str, str] = {
    "os_release": "cat /etc/os-release",
    "arch": "uname -m",
    "python_version": "python --version 2>&1",
    "pip_version": "pip --version 2>&1",
    "path": "echo \"$PATH\"",
    "which_python": "command -v python",
    "venv": "echo \"${VIRTUAL_ENV:-}\"",
    "installed_pip": "pip list --format=freeze 2>/dev/null",
    "dpkg_packages": "dpkg -l 2>/dev/null | awk '/^ii/{print $2}'",
    "pkg_config_modules": "pkg-config --list-all 2>/dev/null",
    # Trailing `; true` forces a zero exit: the loop's status would otherwise be the
    # last `command -v` (non-zero whenever the final probed tool is absent), and
    # run_extractor drops any field whose command returns non-zero — silently
    # discarding the tools the loop already printed.
    "system_tools": (
        "for t in " + " ".join(SYSTEM_TOOL_PROBES) +
        "; do command -v \"$t\" >/dev/null 2>&1 && echo \"$t\"; done; true"
    ),
    "dep_tree": "python -m pip inspect 2>/dev/null || true",
}

# Cheap subset re-run after every env mutation (design §12 run schedule).
LIGHTWEIGHT_FIELDS = ("python_version", "pip_version", "installed_pip", "arch")


@dataclass(frozen=True)
class ExtractionResult:
    fields: Dict[str, str]            # successfully-read field -> trimmed stdout
    raw: Dict[str, Tuple[int, str]]   # field -> (rc, raw stdout) for every attempted command


def run_extractor(
    executor: ProbeExecutor, fields: Optional[Tuple[str, ...]] = None
) -> ExtractionResult:
    names = fields if fields is not None else tuple(EXTRACTOR_COMMANDS.keys())
    parsed: Dict[str, str] = {}
    raw: Dict[str, Tuple[int, str]] = {}
    for name in names:
        command = EXTRACTOR_COMMANDS[name]
        rc, stdout = executor(command)
        raw[name] = (rc, stdout)
        if rc == 0 and stdout.strip():
            parsed[name] = stdout.strip()
    return ExtractionResult(fields=parsed, raw=raw)



# === snapshot.py: read-only env probe -> EnvSnapshot(installed, env) ===
_SNAPSHOT_FIELDS = LIGHTWEIGHT_FIELDS + (
    "which_python", "venv", "dpkg_packages", "pkg_config_modules", "system_tools", "os_release",
    "dep_tree",
)


@dataclass(frozen=True)
class EnvSnapshot:
    installed: tuple[Fact, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    system_installed: tuple[Fact, ...] = ()
    import_results: tuple[tuple[str, bool], ...] = ()   # (import_name, ok); set by import sweep, not extractor
    dep_tree: str = ""                                   # raw output of `python -m pip inspect`


def _parse_installed(freeze_text: str) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    for raw in freeze_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        if "==" in line:
            name, _, ver = line.partition("==")
            name = name.strip()
            if name:
                facts.append(Fact(name=name, detail=ver.strip()))
    return tuple(facts)


def _names(text: str, *, first_token: bool) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(line.split()[0] if first_token else line)
    return out


def probe_env(exec_readonly: Callable[[str], tuple[int, str]]) -> EnvSnapshot:
    try:
        result = run_extractor(exec_readonly, _SNAPSHOT_FIELDS)
    except Exception:
        return EnvSnapshot()
    fields = result.fields
    installed = _parse_installed(fields.get("installed_pip", ""))

    # system providers: apt names + pkg-config module names + tools on PATH
    sys_facts: list[Fact] = []
    for name in _names(fields.get("dpkg_packages", ""), first_token=True):
        sys_facts.append(Fact(name=name, detail="dpkg"))
    for name in _names(fields.get("pkg_config_modules", ""), first_token=True):
        sys_facts.append(Fact(name=name, detail="pkgconfig"))
    tools = _names(fields.get("system_tools", ""), first_token=True)
    for name in tools:
        sys_facts.append(Fact(name=name, detail="tool"))

    dep_tree = fields.get("dep_tree", "")

    # env: keep ONLY compact, prompt-friendly scalars; drop bulky list fields
    bulky = {"installed_pip", "dpkg_packages", "pkg_config_modules", "system_tools", "dep_tree"}
    env = {k: v for k, v in fields.items() if k not in bulky}
    if tools:
        env["build_tools"] = ",".join(tools)
    return EnvSnapshot(installed=installed, env=env, system_installed=tuple(sys_facts),
                       dep_tree=dep_tree)



# === depgraph_live.py (LIVE half): certify-refresh + soname-refresh + python shim ===
class _ReadonlyExecAdapter:
    """Adapt the orchestrator's ``exec_readonly`` callable to the Executor protocol.

    ``certify_all`` only needs ``run(cmd).ok`` and ``.stderr``; check_commands are
    read-only presence checks (``command -v`` / ``ldconfig -p | grep`` /
    ``python -c import``), so the read-only path is the correct executor.
    """

    def __init__(self, exec_readonly: Callable[[str], tuple[int, str]]) -> None:
        self._f = exec_readonly

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        rc, out = self._f(command)
        return CommandResult(command=command, returncode=rc, stdout=out, stderr=out)


def certify_refresh(
    graph,
    exec_readonly,
    cycle: int,
    *,
    allow_service_certify: bool | None = None,
):
    """Re-flip every node's state via a host check in the live container.

    No-op (returns the input) when the graph is empty/None or no read-only
    executor is available — so the feature degrades gracefully.

    When ``allow_service_certify`` is ``None`` (the default), the arm flag is
    resolved from the environment variable ``DOCKERAGENT_ENABLE_SERVICE_PROVISION``
    so the three existing call sites need no change.  Pass an explicit ``True``/
    ``False`` in tests to override the env lookup.
    """
    if graph is None or not graph.nodes or exec_readonly is None:
        return graph
    import os
    from graph.core.certify import _SERVICE_LAYER_ORDER, _LAYER_ORDER
    if allow_service_certify is None:
        allow_service_certify = os.environ.get("DOCKERAGENT_ENABLE_SERVICE_PROVISION") == "1"
    order = _SERVICE_LAYER_ORDER if allow_service_certify else _LAYER_ORDER
    return certify_all(graph, _ReadonlyExecAdapter(exec_readonly), cycle=cycle,
                       allow_service_certify=allow_service_certify, layer_order=order)


def test_gate_soname_refresh(graph, exec_readonly, events, test_cmd):
    """Route the testability gate's failed output through ``test_gate_probe``.

    See docstring in the plan. The testability gate is the dlopen-tail oracle
    (design §3): only a soname surfaced by the repo's own test run reaches here.
    Filters ``events`` to the ones whose command IS ``test_cmd`` (the pytest
    gate) and feeds their combined output to ``test_gate_probe`` with the live
    read-only executor (``_ReadonlyExecAdapter``) so a dlopen-tail soname is
    apt-resolved. No-op (returns input) when graph/exec is absent. Immutable.
    """
    if graph is None or exec_readonly is None:
        return graph
    from graph.python.native.system_libs import test_gate_probe
    executor = _ReadonlyExecAdapter(exec_readonly)
    new = graph
    for cmd, out in events:
        if cmd != test_cmd:
            continue
        new = test_gate_probe(new, executor, out or "", command=test_cmd)
    return new


# Its name matches pytest's default ``test_*`` collection pattern; mark it
# not-a-test so importing it into tests/envstate/test_test_gate_soname_refresh.py
# does not make pytest try to call it as a test function (missing-fixture error).
# Mirrors the same guard on ``test_gate_probe`` (python_deps/depgraph/probe.py).
test_gate_soname_refresh.__test__ = False


def ensure_python_shim(sandbox_execute) -> None:
    """Symlink ``python`` -> ``python3`` in the live container.

    The depgraph's check_commands invoke a bare ``python`` (e.g. ``python -m pip
    show <pkg>``). On a python3-only base that exits 127, so a successfully-installed
    node never certifies and the drain re-emits the same closure every cycle (the
    e2e-smoke certify loop). This normalizes the container to the standard python:3.x
    layout. Runs through the MUTATING ``sandbox_execute`` so the symlink persists;
    best-effort, never raises.

    Issued as a SINGLE setup mutation (a lone idempotent ``ln -sf``), NOT the
    earlier ``command -v python || ln -sf`` compound: ``sandbox_execute`` is
    preflight-gated, and the preflight rejects a command that combines multiple
    steps (the guard + the symlink) — a rejection would silently defeat the shim,
    leaving bare ``python`` unresolved and the certify loop churning
    (reset_to_base -> shim rejected -> reset, never certifying). ``ln -sf`` is
    already idempotent: it re-points an existing ``python`` symlink to ``python3``
    and creates it when absent, so the guard was redundant as well as rejected.
    """
    if sandbox_execute is None:
        return
    try:
        sandbox_execute('ln -sf "$(command -v python3)" /usr/local/bin/python')
    except Exception:  # noqa: BLE001 — best-effort; must never break the loop
        pass



# === script_runner.py: strict-shell block runner (design §7); certify via host checks ===
def run_blocks(
    blocks: tuple[Block, ...],
    sandbox_execute: Callable[[str], tuple[bool, str]],
    exec_readonly: Callable[[str], tuple[int, str]],
    graph,
    cycle: int,
    *,
    container_kind: str = "canonical",
) -> tuple[object, EvidenceBundle, str | None]:
    ensure_python_shim(sandbox_execute)
    bundle = EvidenceBundle()
    failed_block_id: str | None = None
    ev_n = 0
    for block in blocks:
        ok = True
        out = ""
        for cmd in block.commands:
            ok, out = sandbox_execute(cmd)
            ev = Evidence(
                evidence_id=f"ev.{cycle}.{ev_n}", container_kind=container_kind,
                command=cmd, rc=0 if ok else 1,
                output_excerpt=truncate_output(out or ""), cycle=cycle,
                block_id=block.block_id,
                node_id=block.target_node_ids[0] if block.target_node_ids else None,
            )
            bundle = bundle.with_item(ev)
            ev_n += 1
            if not ok:
                failed_block_id = block.block_id
                break
        if not ok:
            break
        # block rc==0: certify the WHOLE graph via host checks (SATISFIED only on check pass)
        graph = certify_refresh(graph, exec_readonly, cycle)
    return graph, bundle, failed_block_id
