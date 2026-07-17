from python_deps.depgraph.cure import render_cure_commands
from python_deps.depgraph.invocation_resolver import resolve


def test_cure_commands_are_the_fallback_chain(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=['setuptools','cython']\n")
    plan = resolve(str(tmp_path))
    cmds = render_cure_commands(plan, "/workspace/repo")
    assert any("pip install" in c and "-e ." in c for c in cmds)           # rung 1 isolated
    assert any("--no-build-isolation" in c for c in cmds)                  # rung 2 fallback
    assert any("setuptools" in c and "wheel" in c for c in cmds)           # backend ensured for rung 2
    assert any("pytest --collect-only" in c for c in cmds)                 # collect-gate
    assert all(c.startswith("cd /workspace/repo") for c in cmds)           # run from the mount
