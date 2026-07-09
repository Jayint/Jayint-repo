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

from multi_docker_eval_adapter import MultiDockerEvalAdapter


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
    assert df.startswith("FROM python:3.12-slim")
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
    # _run_v3 returning an explicit base flows straight to FROM.
    a = _adapter(tmp_path, base="python:3.10-slim")
    r = a.process_single_instance(
        {"instance_id": "o__r", "repo_url": "https://github.com/o/r"})["o__r"]
    assert r["dockerfile"].startswith("FROM python:3.10-slim")


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
