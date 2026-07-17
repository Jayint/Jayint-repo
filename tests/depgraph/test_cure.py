from python_deps.depgraph.cure import render_cure_commands
from python_deps.depgraph.invocation_resolver import TestEnvPlan, resolve


def test_cure_commands_are_the_fallback_chain(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=['setuptools','cython']\n")
    plan = resolve(str(tmp_path))
    cmds = render_cure_commands(plan, "/workspace/repo")
    assert any("pip install" in c and "-e ." in c for c in cmds)           # rung 1 isolated
    assert any("--no-build-isolation" in c for c in cmds)                  # rung 2 fallback
    assert any("setuptools" in c and "wheel" in c for c in cmds)           # backend ensured for rung 2
    assert any("pytest --collect-only" in c for c in cmds)                 # collect-gate
    assert all(c.startswith("cd /workspace/repo") for c in cmds)           # run from the mount


def _nested_plan() -> TestEnvPlan:
    """A feast-style nested-config plan: config rootdir is ``sdk/python`` and the
    tox ``setenv PYTHONPATH=src`` has already been folded into ``pythonpath`` by
    the resolver's ``_merge_pythonpath`` (so ``env`` still carries the raw
    ``PYTHONPATH`` entry — the second-assignment clobber this guards against)."""
    return TestEnvPlan(
        interpreter="3.11",
        interpreter_confidence="declared",
        project_dirs=("sdk/python",),
        install_plan=("editable:sdk/python",),
        rootdir="sdk/python",
        pythonpath=("src",),
        import_mode="prepend",
        layout="src",
        cwd="sdk/python",
        env=(("PYTHONPATH", "src"), ("DJANGO_SETTINGS_MODULE", "app.settings")),
    )


def test_cure_commands_run_under_config_rootdir_with_one_pythonpath():
    # Nested-config (feast-style) repo: cure/probe must cd into <mount>/sdk/python,
    # NOT the mount root, and emit exactly ONE (merged) PYTHONPATH per command —
    # a second raw PYTHONPATH assignment would win in shell and clobber the merge,
    # reopening the deferred-collision false-green vector.
    cmds = render_cure_commands(_nested_plan(), "/workspace/repo")
    for cmd in cmds:
        assert cmd.startswith("cd /workspace/repo/sdk/python"), cmd   # (a) config rootdir, not mount root
        assert "/workspace/repo/sdk/python" in cmd
        assert cmd.count("PYTHONPATH=") == 1, cmd                     # (b) exactly one PYTHONPATH assignment
        assert "DJANGO_SETTINGS_MODULE=app.settings" in cmd          # (c) authoritative env still carried
    # rootdir cd must NOT be the bare mount root for any command
    assert not any(c.startswith("cd /workspace/repo &&") for c in cmds)
