import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.entry import docker_adapters
from src.agent.log import ReactLog


class _FakeSandbox:
    def __init__(self, rc, out, lineno=None): self._rc, self._out, self._lineno = rc, out, lineno
    def reset_to_base(self): pass
    def run_install_script(self, script):
        from src.sandbox import InstallResult
        return InstallResult(rc=self._rc, failing_command=None if self._rc == 0 else "pip install x",
                             lineno=self._lineno, stderr=self._out)
    def exec_readonly(self, cmd): return (0, self._out)


def test_run_script_adapter_maps_lineno():
    _, run_script, _, _, _ = docker_adapters(_FakeSandbox(1, "boom", lineno=7))
    r = run_script("pip install x")
    assert r.lineno == 7 and r.ok is False


def test_run_script_adapter_maps_installresult():
    _, run_script, _, _, _ = docker_adapters(_FakeSandbox(1, "boom"))
    r = run_script("pip install x")
    assert r.ok is False and r.failing_command == "pip install x" and "boom" in r.output


def test_verify_test_cmd_is_lenient_on_collection_errors():
    # Align the react gate with the ratbench OFFICIAL scorer: one un-importable module must NOT abort
    # the whole run. Strict `pytest -q` zeroed repos (e.g. PerfKit) that actually had 73 passing
    # tests — hiding real progress from the agent AND scoring a different target than the benchmark.
    from src.envstate.constants import VERIFY_TEST_CMD
    assert "--continue-on-collection-errors" in VERIFY_TEST_CMD

def test_run_tests_runs_the_lenient_command():
    seen = {}
    class _Cap(_FakeSandbox):
        def exec_readonly(self, cmd): seen["cmd"] = cmd; return (0, "5 passed")
    _, _, _, _, run_tests = docker_adapters(_Cap(0, "5 passed"))
    run_tests()
    assert "--continue-on-collection-errors" in seen["cmd"]

def test_run_tests_adapter_applies_80pct_verdict():
    _, _, _, _, run_tests = docker_adapters(_FakeSandbox(0, "9 passed, 1 failed in 1s"))
    assert run_tests().ok is True                       # 0.9 >= 0.8


class _CapSandbox:
    """Captures the command run_tests passes to exec_readonly, returns a scripted (rc, out)."""
    def __init__(self, rc, out): self._rc, self._out = rc, out
    def reset_to_base(self): pass
    def exec_readonly(self, cmd):
        self.last_cmd = cmd
        return (self._rc, self._out)


def test_run_tests_bounds_pytest_with_timeout():
    sb = _CapSandbox(0, "5 passed in 0.1s")
    _, _, _, _, run_tests = docker_adapters(sb)
    r = run_tests()
    assert "timeout" in sb.last_cmd and "pytest" in sb.last_cmd     # bounded
    assert "command -v timeout" in sb.last_cmd                      # graceful fallback if absent
    assert r.ok is True                                            # verdict still applied to output

def test_run_tests_adds_per_test_timeout_matching_scorer():
    # Align with the ratbench scorer: a PER-TEST timeout so ONE hanging/slow test fails alone,
    # instead of the coarse whole-suite `timeout` killing a 99%-passing suite and scoring it 0.
    sb = _CapSandbox(0, "5 passed in 0.1s")
    _, _, _, _, run_tests = docker_adapters(sb)
    run_tests()
    assert "--timeout=120" in sb.last_cmd and "--timeout-method=signal" in sb.last_cmd

def test_run_tests_per_test_timeout_is_guarded_on_plugin_presence():
    # The flag rides behind a `python -c import pytest_timeout && F=...` guard + a best-effort
    # install, so a container without pytest-timeout degrades to plain pytest (no "unrecognized
    # arguments" error that would break the whole gate), never a hard failure.
    sb = _CapSandbox(0, "5 passed")
    _, _, _, _, run_tests = docker_adapters(sb)
    run_tests()
    assert "import pytest_timeout" in sb.last_cmd        # conditional guard
    assert "pytest-timeout" in sb.last_cmd               # best-effort ensured

def test_run_tests_does_not_parallelize_with_xdist():
    # Deliberately NOT `-n auto`: the scorer runs serially, and xdist can flip parallel-unsafe tests
    # — a fresh divergence from the scorer. Per-test timeout is the correct lever, not parallelism.
    sb = _CapSandbox(0, "5 passed")
    _, _, _, _, run_tests = docker_adapters(sb)
    run_tests()
    assert "-n auto" not in sb.last_cmd and "xdist" not in sb.last_cmd

def test_per_test_timeout_default_matches_scorer():
    import os, src.agent.entry as entry_mod
    if "REACT_PER_TEST_TIMEOUT" not in os.environ:
        assert entry_mod._PER_TEST_TIMEOUT_S == 120     # == the ratbench scorer's --timeout=120


def test_run_tests_timeout_kill_is_not_ok():
    # coreutils `timeout` exits 124 on kill; a killed run has no real passes -> not ok.
    sb = _CapSandbox(124, "")
    _, _, _, _, run_tests = docker_adapters(sb)
    assert run_tests().ok is False

def test_run_tests_sigkill_137_also_flagged_as_timeout():
    # `timeout -k 10` SIGKILLs (rc 137) when pytest ignores the SIGTERM (124); both must read as a
    # timeout, never as a broken env — a WORKING-but-slow seed must not look like "0 tests".
    sb = _CapSandbox(137, "")
    _, _, _, _, run_tests = docker_adapters(sb)
    r = run_tests()
    assert r.ok is False and "TIMEOUT" in r.output

def test_run_tests_timeout_banner_warns_against_stripping():
    # The timeout observation carries an explicit anti-strip hint (darts pattern: the agent gutted a
    # working-but-slow closure because the suite timed out). The banner tells it NOT to remove installs.
    sb = _CapSandbox(124, "")
    _, _, _, _, run_tests = docker_adapters(sb)
    out = run_tests().output.lower()
    assert "timeout" in out and "do not remove installs" in out

def test_default_test_timeout_matches_eval_cap():
    # Raised 600 -> 1800 to match the eval harness, so a working-but-slow seed is not read as false-0
    # by a HARSHER internal cap than the eval will apply.
    import os, src.agent.entry as entry_mod
    if "REACT_TEST_TIMEOUT" not in os.environ:
        assert entry_mod._TEST_TIMEOUT_S == 1800


def test_run_tests_threshold_is_configurable():
    _, _, _, _, rt_low = docker_adapters(_CapSandbox(0, "7 passed, 3 failed in 1s"), test_threshold=0.6)
    assert rt_low().ok is True                    # 0.7 >= 0.6
    _, _, _, _, rt_default = docker_adapters(_CapSandbox(0, "7 passed, 3 failed in 1s"))
    assert rt_default().ok is False               # 0.7 < 0.9 default


class _EnvSandbox:
    base_image = "python:3.10-slim"
    workdir = "/app"
    def __init__(self, os_out="Debian GNU/Linux 12 (bookworm)", raise_probe=False):
        self._os_out, self._raise = os_out, raise_probe
    def exec_readonly(self, cmd):
        if self._raise:
            raise RuntimeError("no container")
        return (0, self._os_out)

def test_gather_env_info_includes_base_dir_and_layout(tmp_path):
    from src.agent.entry import _gather_env_info
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "rq").mkdir(); (tmp_path / "rq" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "requirements.txt").write_text("redis\n")
    (tmp_path / ".git").mkdir(); (tmp_path / ".git" / "junk").write_text("x")   # skipped
    info = _gather_env_info(_EnvSandbox(), str(tmp_path))
    assert "python:3.10-slim" in info and "Debian" in info and "/app" in info
    assert "pyproject.toml" in info and "requirements.txt" in info      # manifests surfaced
    assert "rq/" in info and "tests/" in info                            # top-level layout
    assert ".git" not in info                                           # noise dir skipped

def test_gather_env_info_surfaces_monorepo_manifest_paths(tmp_path):
    # a nested package manifest (monorepo) must show its PATH, not just the name — this is what
    # lets the agent target the right editable-install dir instead of guessing `pip install -e .`.
    from src.agent.entry import _gather_env_info
    sub = tmp_path / "premium" / "backend"; sub.mkdir(parents=True)
    (sub / "pyproject.toml").write_text("[project]\nname='p'\n")
    info = _gather_env_info(_EnvSandbox(), str(tmp_path))
    assert "premium/backend/pyproject.toml" in info

def test_gather_env_info_survives_probe_failure(tmp_path):
    from src.agent.entry import _gather_env_info
    info = _gather_env_info(_EnvSandbox(raise_probe=True), str(tmp_path))
    assert "python:3.10-slim" in info and "/app" in info    # base+dir kept; OS omitted, no crash

def test_run_react_arm_strips_and_forwards_seed(monkeypatch):
    import src.agent.entry as entry_mod
    captured = {}
    def fake_run_react(graph, **kw):
        captured.update(kw)
        return ("DONE", kw.get("_initial_script"), graph)
    monkeypatch.setattr(entry_mod, "run_react", fake_run_react)
    seed = ("#!/usr/bin/env bash\n#\n"
            "# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.\n#\n"
            "set -Eeuo pipefail\npip install app\n")
    entry_mod.run_react_arm(object(), sandbox=_FakeSandbox(0, ""), client=object(),
                            model="m", initial_script=seed, log=ReactLog(silent=True))
    fwd = captured["_initial_script"]
    assert fwd is not None
    assert "DO NOT EDIT" not in fwd and "graph" not in fwd.lower()   # header stripped
    assert "pip install app" in fwd and "set -Eeuo pipefail" in fwd  # body preserved

def test_run_react_arm_injects_no_llm_compressor(monkeypatch):
    # The grouped history view IS the compaction; the old Tier-2 LLM compressor's output is never
    # rendered, so injecting it only burns wasted LLM calls. Lock that the arm builds no compressor.
    import src.agent.entry as entry_mod
    captured = {}
    class FakeHistory:
        def __init__(self, *a, compressor=None, **k):
            captured["compressor"] = compressor
    monkeypatch.setattr(entry_mod, "History", FakeHistory)
    monkeypatch.setattr(entry_mod, "run_react", lambda graph, **kw: ("DONE", "s", graph))
    entry_mod.run_react_arm(object(), sandbox=_FakeSandbox(0, ""), client=object(),
                            model="m", log=ReactLog(silent=True))
    assert captured["compressor"] is None

def test_run_react_arm_without_seed_forwards_none(monkeypatch):
    import src.agent.entry as entry_mod
    captured = {}
    monkeypatch.setattr(entry_mod, "run_react",
                        lambda graph, **kw: captured.update(kw) or ("DONE", None, graph))
    entry_mod.run_react_arm(object(), sandbox=_FakeSandbox(0, ""), client=object(),
                            model="m", log=ReactLog(silent=True))
    assert captured["_initial_script"] is None       # unchanged default behavior


# ── the ablation rungs (spec §2) ─────────────────────────────────────────────

from src.agent.entry import rung_flags


def test_G0_and_G1_do_not_touch_the_graph(monkeypatch):
    # G0/G1 differ only in REACT_OBS_MODE, which lives in loop.py and is no concern of the
    # graph seam. Neither renders nor grows the graph — graph_context stays None, and the
    # prompt bytes stay identical to every baseline we have already run.
    monkeypatch.delenv("REACT_GRAPH_CONTEXT", raising=False)
    monkeypatch.delenv("REACT_GRAPH_UPDATE", raising=False)
    assert rung_flags() == (False, False)


def test_G2_renders_the_graph_but_does_not_grow_it(monkeypatch):
    # This is the direction that has to stay separable: it is what isolates STRUCTURE from
    # GROWTH. G1→G2 measures what the structure adds; G2→G3 measures what growth adds.
    monkeypatch.setenv("REACT_GRAPH_CONTEXT", "1")
    monkeypatch.delenv("REACT_GRAPH_UPDATE", raising=False)
    assert rung_flags() == (True, False)


def test_G3_IMPLIES_G2(monkeypatch):
    """REACT_GRAPH_UPDATE=1 alone must still render.

    Growing the graph without rendering it is not a rung — it is a silent no-op arm. The loop
    would pay for enrich + expand + certify on every turn and the agent would never see a byte
    of it, so the run LOOKS like a valid G3 datapoint while measuring nothing at all.
    """
    monkeypatch.delenv("REACT_GRAPH_CONTEXT", raising=False)
    monkeypatch.setenv("REACT_GRAPH_UPDATE", "1")
    assert rung_flags() == (True, True)


def test_the_legacy_bool_param_still_turns_on_G2(monkeypatch):
    monkeypatch.delenv("REACT_GRAPH_CONTEXT", raising=False)
    monkeypatch.delenv("REACT_GRAPH_UPDATE", raising=False)
    assert rung_flags(graph_context=True) == (True, False)
