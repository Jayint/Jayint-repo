"""Offline contract tests for the run_v3 -> RAT-harness adapter.

The three side-effecting steps (_clone, _head_sha, _run_v3) are mocked, so these
run with no Docker, network, or LLM — they pin the harness contract:
``process_single_instance`` returns ``{instance_id: {dockerfile, setup_scripts,
base_image, logs}}`` where ``dockerfile`` is a self-contained image spec that
clones the repo into /testbed and runs the certified setup.sh.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from multi_docker_eval_adapter import MultiDockerEvalAdapter, _bake_base_image


def _adapter(tmp_path, *, setup_text="cd /app && pip install -e .", base="python:3.12-slim"):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._clone = lambda repo_url, **kw: tmp_path / "v3_src"    # type: ignore[assignment]
    a._head_sha = lambda src_dir: "deadbeef"                 # type: ignore[assignment]
    a._run_v3 = lambda src_dir, base_image, model, **kw: (setup_text, base)  # type: ignore[assignment]
    return a


def test_dockerfile_contract(tmp_path):
    a = _adapter(tmp_path)
    res = a.process_single_instance(
        {"instance_id": "anthropics__anthropic-sdk-python",
         "repo_url": "https://github.com/anthropics/anthropic-sdk-python"},
        base_image="auto", model="deepseek/deepseek-v4-flash",
    )
    # harness does res.get(instance_id, res) -> the result dict
    r = res["anthropics__anthropic-sdk-python"]
    assert r["base_image"] == "python:3.12-slim"
    df = r["dockerfile"]
    assert df.startswith("FROM python:3.12-slim")   # baked-base swap is opt-in (off by default)
    assert "git clone --depth=1 https://github.com/anthropics/anthropic-sdk-python /testbed" in df
    assert "COPY setup.sh /tmp/v3_setup.sh" in df
    assert "RUN bash /tmp/v3_setup.sh" in df
    # setup.sh is surfaced verbatim (harness writes it into the build context)...
    assert "setup.sh" in r["setup_scripts"]
    # ...with /app normalized to /testbed so the editable install resolves.
    assert r["setup_scripts"]["setup.sh"] == "cd /testbed && pip install -e ."
    assert r["logs"]["head_sha"] == "deadbeef"


def test_missing_repo_url_is_clean_failure(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    res = a.process_single_instance({"instance_id": "x__y"})  # no repo_url
    r = res["x__y"]
    assert r["dockerfile"] is None            # -> harness reports no_dockerfile, not a crash
    assert "repo_url" in r["logs"]["error"]


def test_run_v3_failure_yields_error_log_not_crash(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._clone = lambda repo_url, **kw: tmp_path / "v3_src"    # type: ignore[assignment]
    a._head_sha = lambda src_dir: "abc"                      # type: ignore[assignment]

    def _boom(src_dir, base_image, model, **kw):
        raise RuntimeError("run_v3_e2e produced no setup.sh")

    a._run_v3 = _boom                                        # type: ignore[assignment]
    res = a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r"})
    r = res["o__r"]
    assert r["dockerfile"] is None
    assert "no setup.sh" in r["logs"]["error"]


def test_base_image_fallback_when_unparsed(tmp_path):
    # _run_v3's resolved base flows straight into FROM (baked swap off by default).
    a = _adapter(tmp_path, base="python:3.10-slim")
    r = a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r"})["o__r"]
    assert r["dockerfile"].startswith("FROM python:3.10-slim")


# ── §4.4b: FROM-swap mapper — OPT-IN via V3_USE_BAKED_BASE=1 (default off). ──

def test_bake_default_off_is_noop(monkeypatch):
    # Default (env unset): byte-identical to pre-§4.4b — never emits a v3-base FROM
    # for an image that may not be built yet.
    monkeypatch.delenv("V3_USE_BAKED_BASE", raising=False)
    assert _bake_base_image("python:3.11-slim") == "python:3.11-slim"


def test_bake_maps_python_slim_to_v3_base_when_opted_in(monkeypatch):
    monkeypatch.setenv("V3_USE_BAKED_BASE", "1")
    assert _bake_base_image("python:3.11-slim") == "v3-base:3.11"
    assert _bake_base_image("python:3.14-slim") == "v3-base:3.14"


def test_bake_is_noop_for_non_python_and_unbuilt_when_opted_in(monkeypatch):
    # Even opted-in: explicit overrides / un-built minors / bare tags pass through
    # so FROM never points at an image that isn't built.
    monkeypatch.setenv("V3_USE_BAKED_BASE", "1")
    assert _bake_base_image("node:25") == "node:25"
    assert _bake_base_image("python:3.5-slim") == "python:3.5-slim"       # not in matrix
    assert _bake_base_image("buildpack-deps:jammy") == "buildpack-deps:jammy"
    assert _bake_base_image("python:3.12") == "python:3.12"               # bare (not -slim)


def test_render_dockerfile_uses_baked_base_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_USE_BAKED_BASE", "1")
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r")
    assert df.startswith("FROM v3-base:3.11")


def _capture_run_v3_cmd(tmp_path, monkeypatch):
    """Drive the REAL _run_v3 with subprocess mocked so we can inspect the argv
    it builds (and confirm the LLM model is still passed)."""
    import multi_docker_eval_adapter as M

    class _Proc:
        returncode = 0
        stdout = "[v3] base-image: python:3.11-slim (py 3.11) — auto"
        stderr = ""

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out = cmd[cmd.index("--out") + 1]  # write the artifact the adapter validates
        Path(out).write_text("echo built")
        return _Proc()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    a = M.MultiDockerEvalAdapter(output_dir=str(tmp_path))
    setup, base = a._run_v3(tmp_path / "src", "auto", "deepseek/deepseek-v4-flash")
    assert base == "python:3.11-slim"
    return captured["cmd"]


def test_construction_only_env_adds_flag_keeps_model(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_CONSTRUCTION_ONLY", "1")
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)
    assert "--construction-only" in cmd, "V3_CONSTRUCTION_ONLY=1 must pass the flag"
    # LLM stays ON in construction-only mode (base-image + service classify use it).
    assert "--model" in cmd and "deepseek/deepseek-v4-flash" in cmd


def test_construction_only_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("V3_CONSTRUCTION_ONLY", raising=False)
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)
    assert "--construction-only" not in cmd, "default must run the full repair loop"


def test_include_services_env_adds_services_out_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_INCLUDE_SERVICES", "1")
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)
    assert "--services-out" in cmd, "V3_INCLUDE_SERVICES=1 must request the services artifact"
    services_path = Path(cmd[cmd.index("--services-out") + 1])
    assert services_path.parent == tmp_path


def test_include_services_absent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("V3_INCLUDE_SERVICES", raising=False)
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)
    assert "--services-out" not in cmd, "default must not request the services artifact"


def test_dockerfile_no_entrypoint_by_default(tmp_path):
    # _adapter()'s mocked _run_v3 never writes services_start.sh -> no ENTRYPOINT,
    # byte-identical Dockerfile shape to before this mechanism existed.
    a = _adapter(tmp_path)
    r = a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r"})["o__r"]
    assert "ENTRYPOINT" not in r["dockerfile"]
    assert "services_start.sh" not in r["dockerfile"]
    assert "services_start.sh" not in r["setup_scripts"]


def test_dockerfile_gets_entrypoint_when_services_script_present(tmp_path):
    # Simulate what the REAL _run_v3 does under V3_INCLUDE_SERVICES=1: it leaves
    # a services_start.sh artifact in output_dir for process_single_instance to
    # pick up via _read_services_script.
    (tmp_path / "services_start.sh").write_text(
        "#!/usr/bin/env bash\nservice postgresql start\nexec \"$@\"\n")
    a = _adapter(tmp_path)
    r = a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r"})["o__r"]
    df = r["dockerfile"]
    assert 'ENTRYPOINT ["/bin/bash", "/v3_start_services.sh"]' in df
    assert "COPY services_start.sh /v3_start_services.sh" in df
    assert df.index("RUN bash /tmp/v3_setup.sh") < df.index("ENTRYPOINT")
    assert r["setup_scripts"]["services_start.sh"].startswith("#!/usr/bin/env bash")


# ── repair-only ablation (V3_REPAIR_ABLATION=1) ────────────────────────────────
# Seed the react repair loop from a construction run's PRE-GENERATED setup.sh so
# only the repair loop varies; construction (graph build) is skipped. Same eval
# path downstream -> ESSR/case_studies for free.

def _seed_corpus(tmp_path, full_name, *, setup="cd /app && pip install -e .",
                 base="python:3.11-slim", meta=True, dockerfile_from=None):
    """Lay down what a construction run leaves per instance:
    <root>/<owner>/<repo>/{setup.sh,_meta.json,eval_build/Dockerfile}. Returns the root."""
    inst = tmp_path / "seeds" / full_name
    inst.mkdir(parents=True)
    (inst / "setup.sh").write_text(setup)
    if meta:
        (inst / "_meta.json").write_text(json.dumps({"base_image": base}))
    if dockerfile_from:
        (inst / "eval_build").mkdir()
        (inst / "eval_build" / "Dockerfile").write_text(f"FROM {dockerfile_from}\nWORKDIR /testbed\n")
    return tmp_path / "seeds"


def _capture_ablation_cmd(tmp_path, monkeypatch, *, full_name="o/r"):
    """Drive the REAL _run_v3 (subprocess mocked) under repair-ablation and return
    the argv it builds + the resolved base."""
    import multi_docker_eval_adapter as M

    class _Proc:
        returncode = 0
        stdout = "[v3] base-image: python:3.11-slim (py 3.11) — explicit"
        stderr = ""

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("--out") + 1]).write_text("echo repaired")
        return _Proc()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    a = M.MultiDockerEvalAdapter(output_dir=str(tmp_path))
    _setup, captured["base"] = a._run_v3(
        tmp_path / "src", "auto", "MiniMax-M2.7-highspeed", full_name=full_name)
    return captured


def test_repair_ablation_seeds_react_arm(tmp_path, monkeypatch):
    root = _seed_corpus(tmp_path, "o/r", base="python:3.11-slim")
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(root))
    cmd = _capture_ablation_cmd(tmp_path, monkeypatch, full_name="o/r")["cmd"]
    assert cmd[cmd.index("--arm") + 1] == "react"
    assert cmd[cmd.index("--seed-script") + 1] == str(root / "o" / "r" / "setup.sh")
    # explicit base from _meta.json overrides the incoming "auto" (seed mode rejects auto).
    assert cmd[cmd.index("--base-image") + 1] == "python:3.11-slim"
    assert "--max-steps" in cmd
    assert "--construction-only" not in cmd
    # LLM still passed through (the repair loop needs it).
    assert "--model" in cmd and "MiniMax-M2.7-highspeed" in cmd


def test_repair_ablation_wires_trace_out_into_output_dir(tmp_path, monkeypatch):
    # Observability: the react loop's Thought/Action trace must be PERSISTED next to run.log so a
    # regressed repo is diagnosable from a real trace (not reconstruction). The adapter passes
    # --trace-out into its per-repo output_dir -> run_v3_e2e threads it to the react ReactLog.
    root = _seed_corpus(tmp_path, "o/r", base="python:3.11-slim")
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(root))
    cmd = _capture_ablation_cmd(tmp_path, monkeypatch, full_name="o/r")["cmd"]
    assert "--trace-out" in cmd
    assert cmd[cmd.index("--trace-out") + 1] == str(tmp_path / "react_trace.jsonl")


def test_construction_path_has_no_trace_out(tmp_path, monkeypatch):
    # --trace-out on the construction path would build the (different) Task-8d RunTracer; keep
    # construction byte-identical — trace-out is react/seed-only.
    monkeypatch.delenv("V3_REPAIR_ABLATION", raising=False)
    monkeypatch.delenv("V3_CONSTRUCTION_ONLY", raising=False)
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)
    assert "--trace-out" not in cmd


def test_repair_ablation_and_construction_only_are_mutually_exclusive(tmp_path, monkeypatch):
    root = _seed_corpus(tmp_path, "o/r")
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(root))
    monkeypatch.setenv("V3_CONSTRUCTION_ONLY", "1")     # ablation must win
    cmd = _capture_ablation_cmd(tmp_path, monkeypatch)["cmd"]
    assert "--seed-script" in cmd
    assert "--construction-only" not in cmd


def test_repair_ablation_base_from_dockerfile_when_no_meta(tmp_path, monkeypatch):
    # _meta.json absent -> fall back to the construction eval_build/Dockerfile FROM.
    root = _seed_corpus(tmp_path, "o/r", meta=False, dockerfile_from="python:3.9-slim")
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(root))
    cmd = _capture_ablation_cmd(tmp_path, monkeypatch)["cmd"]
    assert cmd[cmd.index("--base-image") + 1] == "python:3.9-slim"


def test_repair_ablation_off_by_default_no_seed_flags(tmp_path, monkeypatch):
    monkeypatch.delenv("V3_REPAIR_ABLATION", raising=False)
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)     # existing helper, full_name=None
    assert "--seed-script" not in cmd
    assert "--arm" not in cmd


def test_resolve_seed_off_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("V3_REPAIR_ABLATION", raising=False)
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    assert a._resolve_seed("o/r") == (None, None)


def test_resolve_seed_missing_seed_raises(tmp_path, monkeypatch):
    (tmp_path / "seeds").mkdir()
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(tmp_path / "seeds"))   # no o/r/setup.sh
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match="seed"):
        a._resolve_seed("o/r")


def test_resolve_seed_empty_seed_raises(tmp_path, monkeypatch):
    root = _seed_corpus(tmp_path, "o/r", setup="")     # 0-byte seed = construction produced nothing
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(root))
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match="seed"):
        a._resolve_seed("o/r")


def test_resolve_seed_requires_seed_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.delenv("V3_SEED_DIR", raising=False)
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match="V3_SEED_DIR"):
        a._resolve_seed("o/r")


def test_full_name_from_repo_url():
    fn = MultiDockerEvalAdapter._full_name
    assert fn("https://github.com/o/r", "o__r") == "o/r"
    assert fn("https://github.com/o/r.git", "o__r") == "o/r"
    assert fn("", "owner__repo") == "owner/repo"       # fallback to instance_id


# ── Bug A: pin the ablation to construction's commit (faithful baseline + VCS tags) ────────────

def test_seed_head_sha_off_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("V3_REPAIR_ABLATION", raising=False)
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    assert a._seed_head_sha("o/r") is None

def test_seed_head_sha_reads_meta(tmp_path, monkeypatch):
    inst = tmp_path / "seeds" / "o" / "r"
    inst.mkdir(parents=True)
    (inst / "_meta.json").write_text(json.dumps({"head_sha": "cafebabe", "base_image": "python:3.11-slim"}))
    monkeypatch.setenv("V3_REPAIR_ABLATION", "1")
    monkeypatch.setenv("V3_SEED_DIR", str(tmp_path / "seeds"))
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    assert a._seed_head_sha("o/r") == "cafebabe"

def _capture_clone_cmds(tmp_path, monkeypatch, *, pin_sha=None):
    import multi_docker_eval_adapter as M
    cmds = []
    class _P:
        returncode = 0; stdout = ""; stderr = ""
    def fake_run(cmd, **kw):
        cmds.append(cmd); return _P()
    monkeypatch.setattr(M.subprocess, "run", fake_run)
    a = M.MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._clone("https://github.com/o/r", pin_sha=pin_sha)
    return [c for c in cmds if isinstance(c, list) and c and c[0] == "git"]

def test_clone_default_is_shallow(tmp_path, monkeypatch):
    gits = _capture_clone_cmds(tmp_path, monkeypatch, pin_sha=None)
    clone = next(c for c in gits if "clone" in c)
    assert "--depth=1" in clone
    assert not any("checkout" in c for c in gits)

def test_clone_pins_sha_with_full_history(tmp_path, monkeypatch):
    gits = _capture_clone_cmds(tmp_path, monkeypatch, pin_sha="cafebabe")
    clone = next(c for c in gits if "clone" in c)
    assert "--depth=1" not in clone                 # full history + tags for VCS versioning
    checkout = next(c for c in gits if "checkout" in c)
    assert "cafebabe" in checkout

def test_process_single_instance_pins_analysis_clone_to_dataset_commit(tmp_path):
    # README adapter contract: our OWN analysis clone must honor instance["commit"] so run_v3
    # analyzes the pinned tree, not live HEAD.
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    seen = {}

    def _clone(repo_url, pin_sha=None):
        seen["pin_sha"] = pin_sha
        return tmp_path / "v3_src"

    a._clone = _clone                                             # type: ignore[assignment]
    a._head_sha = lambda src_dir: "deadbeef"                      # type: ignore[assignment]
    a._run_v3 = lambda src_dir, base_image, model, **kw: ("echo x", "python:3.11-slim")  # type: ignore[assignment]
    a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r", "commit": "cafebabe1234"})
    assert seen["pin_sha"] == "cafebabe1234"


def test_process_single_instance_no_commit_leaves_analysis_clone_unpinned(tmp_path):
    # No dataset commit -> byte-identical to before: shallow current-HEAD clone (pin_sha None).
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    seen = {}

    def _clone(repo_url, pin_sha=None):
        seen["pin_sha"] = pin_sha
        return tmp_path / "v3_src"

    a._clone = _clone                                             # type: ignore[assignment]
    a._head_sha = lambda src_dir: "deadbeef"                      # type: ignore[assignment]
    a._run_v3 = lambda src_dir, base_image, model, **kw: ("echo x", "python:3.11-slim")  # type: ignore[assignment]
    a.process_single_instance({"instance_id": "o__r", "repo_url": "https://github.com/o/r"})
    assert seen["pin_sha"] is None


def test_render_dockerfile_default_shallow(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r")
    assert "git clone --depth=1 https://github.com/o/r /testbed" in df
    assert "checkout" not in df

def test_render_dockerfile_pins_sha(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r", pin_sha="cafebabe")
    assert "git clone https://github.com/o/r /testbed" in df   # full clone (no --depth=1)
    assert "--depth=1" not in df
    assert "checkout" in df and "cafebabe" in df


# ── FIX B1 — bake CONFIG values render_build_script left as `#@config-env`
# markers into the Dockerfile as ENV (a setup.sh RUN layer cannot persist an
# export into the image or the later `docker exec` pytest runs through) ──────

def test_render_dockerfile_bakes_config_env_marker(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    setup_sh = (
        "#!/usr/bin/env bash\n"
        "#@config-env DJANGO_SETTINGS_MODULE=settings\n"
        "set -Eeuo pipefail\n"
    )
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r", setup_sh=setup_sh)
    assert 'ENV DJANGO_SETTINGS_MODULE="settings"' in df
    # before the RUN that executes setup.sh, so any step in it observes the var too
    assert df.index("DJANGO_SETTINGS_MODULE") < df.index("RUN bash /tmp/v3_setup.sh")


def test_render_dockerfile_no_env_lines_when_no_markers(tmp_path):
    # default setup_sh="" -> byte-identical to the pre-existing shape (no ENV
    # lines at all, no behavior change for every repo without a Config need).
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r")
    assert "ENV" not in df


def test_render_dockerfile_bakes_multiple_config_env_markers_deterministically(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    setup_sh = (
        "#@config-env REDIS_URL=redis://localhost:6379/0\n"
        "#@config-env DJANGO_SETTINGS_MODULE=settings\n"
    )
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r", setup_sh=setup_sh)
    assert 'ENV DJANGO_SETTINGS_MODULE="settings"' in df
    assert 'ENV REDIS_URL="redis://localhost:6379/0"' in df
    # sorted -> deterministic regardless of marker order in setup.sh
    assert df.index("DJANGO_SETTINGS_MODULE") < df.index("REDIS_URL")


def test_render_dockerfile_escapes_quotes_and_backslashes_in_config_value(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    setup_sh = '#@config-env GREETING=say "hi" \\ok\n'
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r", setup_sh=setup_sh)
    assert 'ENV GREETING="say \\"hi\\" \\\\ok"' in df


def test_render_dockerfile_bakes_pythonpath_marker_bound_to_testbed(tmp_path):
    # FIX 3a's fallback for a not-safely-installable project: the marker's own
    # payload (an analysis-time path, e.g. /app or a local clone dir) is
    # meaningless inside the eval image, so this always binds to /testbed --
    # the one place THIS adapter's own Dockerfile ever clones the repo.
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    setup_sh = "#@pythonpath-env /some/local/clone/dir\n"
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r", setup_sh=setup_sh)
    assert 'ENV PYTHONPATH="/testbed:${PYTHONPATH}"' in df


def test_render_dockerfile_no_pythonpath_env_without_marker(tmp_path):
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    df = a._render_dockerfile("python:3.11-slim", "https://github.com/o/r", setup_sh="echo hi\n")
    assert "PYTHONPATH" not in df


def test_process_single_instance_threads_setup_sh_into_dockerfile(tmp_path):
    # end-to-end: the setup.sh _run_v3 returns is the SAME text the Dockerfile
    # renderer sees (after /app -> /testbed normalization), not a stale/empty one.
    setup_text = "cd /app && echo x\n#@config-env DJANGO_SETTINGS_MODULE=settings\n"
    a = _adapter(tmp_path, setup_text=setup_text)
    r = a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r"})["o__r"]
    assert 'ENV DJANGO_SETTINGS_MODULE="settings"' in r["dockerfile"]


# ── token telemetry relay (react loop [Tokens] lines are swallowed by the subprocess) ──────────

def test_run_v3_relays_token_lines_and_writes_usage(tmp_path, monkeypatch, capsys):
    import multi_docker_eval_adapter as M

    class _Proc:
        returncode = 0
        stdout = ("[v3] base-image: python:3.11-slim (py 3.11) — explicit\n"
                  "[Tokens] Input: 100, Output: 20, Total: 120\n"
                  "[Tokens] Input: 200, Output: 30, Total: 230\n")
        stderr = ""

    def fake_run(cmd, **kw):
        Path(cmd[cmd.index("--out") + 1]).write_text("echo built")
        return _Proc()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    a = M.MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._run_v3(tmp_path / "src", "auto", "MiniMax-M2.7-highspeed")
    out = capsys.readouterr().out
    # both per-call lines relayed to stdout (→ worker run.log → unified_metrics arm0 economy)
    assert out.count("[Tokens]") == 2
    # summed total persisted for the case-study consolidator
    usage = json.loads((tmp_path / "usage.json").read_text())
    assert usage["total_tokens"] == 350

def test_run_v3_no_usage_file_when_no_token_lines(tmp_path, monkeypatch):
    import multi_docker_eval_adapter as M

    class _Proc:
        returncode = 0
        stdout = "[v3] base-image: python:3.11-slim (py 3.11) — explicit\n"
        stderr = ""

    def fake_run(cmd, **kw):
        Path(cmd[cmd.index("--out") + 1]).write_text("x")
        return _Proc()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    a = M.MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._run_v3(tmp_path / "src", "auto", "m")
    assert not (tmp_path / "usage.json").exists()


# ── §4.1: thread instance["language"] → _run_v3(language=...) → run_v3_e2e --language ──

def test_language_absent_by_default_no_flag(tmp_path, monkeypatch):
    # byte-identity guard: no language supplied -> no --language token in argv.
    cmd = _capture_run_v3_cmd(tmp_path, monkeypatch)
    assert "--language" not in cmd


def test_language_threads_to_run_v3_argv(tmp_path, monkeypatch):
    import multi_docker_eval_adapter as M

    class _Proc:
        returncode = 0
        stdout = "[v3] base-image: python:3.11-slim (py 3.11) — auto"
        stderr = ""

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[cmd.index("--out") + 1]).write_text("echo built")
        return _Proc()

    monkeypatch.setattr(M.subprocess, "run", fake_run)
    a = M.MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._run_v3(tmp_path / "src", "auto", "m", language="python")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--language") + 1] == "python"


def test_process_single_instance_threads_language_to_run_v3(tmp_path):
    # process_single_instance reads instance["language"] and passes it to _run_v3.
    a = MultiDockerEvalAdapter(output_dir=str(tmp_path))
    a._clone = lambda repo_url, **kw: tmp_path / "v3_src"     # type: ignore[assignment]
    a._head_sha = lambda src_dir: "deadbeef"                  # type: ignore[assignment]
    seen = {}

    def _run_v3(src_dir, base_image, model, **kw):
        seen.update(kw)
        return ("echo built", "python:3.11-slim")

    a._run_v3 = _run_v3                                        # type: ignore[assignment]
    a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r", "language": "python"})
    assert seen.get("language") == "python"
