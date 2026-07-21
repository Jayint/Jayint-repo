import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.env_state import env_state_enabled, render_declared


def test_flag_defaults_on_and_off_only_for_zero(monkeypatch):
    monkeypatch.delenv("REACT_ENV_STATE", raising=False)
    assert env_state_enabled() is True
    monkeypatch.setenv("REACT_ENV_STATE", "0")
    assert env_state_enabled() is False
    monkeypatch.setenv("REACT_ENV_STATE", "1")
    assert env_state_enabled() is True


def test_render_declared_reads_requirements_and_pyproject_extras(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask>=3\npsycopg2-binary\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["click"]\n'
        '[project.optional-dependencies]\ntest = ["pytest", "pytest-cov"]\n')
    out = render_declared(tmp_path)
    assert out.startswith("DECLARED (from the repo, static)\n")
    assert "requirements.txt:" in out and "psycopg2-binary" in out
    assert "[project.dependencies]" in out and "click" in out
    assert "optional-dependencies].test" in out and "pytest-cov" in out


def test_render_declared_caps_oversized_file(tmp_path):
    (tmp_path / "requirements.txt").write_text("x==1\n" * 5000)
    out = render_declared(tmp_path, cap_bytes=200)
    assert "… (truncated)" in out
    assert len(out) < 1000


def test_render_declared_empty_when_no_manifests(tmp_path):
    assert render_declared(tmp_path) == ""
    assert render_declared(None) == ""
    assert render_declared(tmp_path / "does-not-exist") == ""


def test_render_declared_reads_setup_cfg(tmp_path):
    (tmp_path / "setup.cfg").write_text(
        "[options]\ninstall_requires =\n    requests>=2\n    pyyaml\n"
        "[options.extras_require]\ntest = pytest\n    pytest-mock\n")
    out = render_declared(tmp_path)
    assert "setup.cfg:" in out
    assert "install_requires" in out and "requests>=2" in out
    assert "extras_require.test" in out and "pytest-mock" in out


def test_render_declared_reads_pipfile(tmp_path):
    (tmp_path / "Pipfile").write_text(
        '[packages]\nrequests = "*"\ndjango = ">=4.0"\n'
        '[dev-packages]\npytest = "*"\n')
    out = render_declared(tmp_path)
    assert "Pipfile:" in out
    assert "[packages]" in out and "django >=4.0" in out
    assert "[dev-packages]" in out and "pytest" in out


def test_render_declared_per_file_cap_not_per_section(tmp_path):
    # A single manifest with dependencies AND several oversized extras groups must still
    # obey ONE cap_bytes bound for the whole file — not cap_bytes multiplied by the
    # number of groups.
    extras = "\n".join(
        f'group{i} = [{", ".join(repr(f"pkg-{i}-{j}") for j in range(200))}]'
        for i in range(5)
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["click"]\n'
        f'[project.optional-dependencies]\n{extras}\n')
    out = render_declared(tmp_path, cap_bytes=200)
    assert "… (truncated)" in out
    # Loosely bounded: label + heading + one capped body, nowhere near 5 * 200 bytes.
    assert len(out.encode("utf-8")) < 600


from src.agent.env_state import PIP_LIST_CMD, render_pip_list


def test_pip_list_cmd_is_read_only_freeze():
    assert "pip list" in PIP_LIST_CMD and "--format=freeze" in PIP_LIST_CMD


def test_render_pip_list_formats_name_and_version():
    raw = "flask==3.0.0\npsycopg2-binary==2.9.9\n"
    out = render_pip_list(raw)
    assert out == "pip installed (2): flask 3.0.0, psycopg2-binary 2.9.9"


def test_render_pip_list_keeps_editable_and_skips_noise():
    raw = "# comment\n\n-e git+https://x#egg=app\nclick==8.1.7\n"
    out = render_pip_list(raw)
    assert out.startswith("pip installed (2): ")
    assert "-e git+https://x#egg=app" in out and "click 8.1.7" in out


def test_render_pip_list_caps_and_counts_remainder():
    raw = "".join(f"p{i}==1.0\n" for i in range(250))
    out = render_pip_list(raw, cap=200)
    assert out.startswith("pip installed (250): ")
    assert ", +50 more" in out


def test_render_pip_list_empty_when_no_packages():
    assert render_pip_list("") == "" and render_pip_list("\n#x\n") == ""


from src.agent.env_state import SYSPKG_PROBE_CMD, parse_syspkgs, render_syspkg_delta


def test_syspkg_probe_covers_dpkg_and_apk():
    assert "dpkg-query" in SYSPKG_PROBE_CMD and "apk" in SYSPKG_PROBE_CMD


def test_parse_syspkgs_one_name_per_line():
    assert parse_syspkgs("libc6\nlibpq5\n\n# note\n") == frozenset({"libc6", "libpq5"})


def test_render_syspkg_delta_shows_only_added():
    base = frozenset({"libc6", "bash"})
    now = frozenset({"libc6", "bash", "libpq5", "libpq-dev"})
    assert render_syspkg_delta(base, now) == (
        "system packages added on top of base: libpq-dev, libpq5")


def test_render_syspkg_delta_none_added():
    base = frozenset({"libc6"})
    assert render_syspkg_delta(base, base) == "system packages added on top of base: (none)"


def test_render_syspkg_delta_omitted_when_base_unknown():
    # empty base = capture failed; must NOT dump the whole `now` set as if all were "added".
    assert render_syspkg_delta(frozenset(), frozenset({"libc6", "libpq5"})) == ""


def test_render_syspkg_delta_caps():
    base = frozenset()
    added = frozenset(f"pkg{i}" for i in range(80))
    # base non-empty so the delta renders; make base a sentinel not in `added`.
    out = render_syspkg_delta(frozenset({"_base_"}), added | {"_base_"}, cap=60)
    assert out.startswith("system packages added on top of base: ")
    assert ", +20 more" in out
