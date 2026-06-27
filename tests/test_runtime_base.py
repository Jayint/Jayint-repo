# tests/test_runtime_base.py
"""Runtime-tier base selection: derive a concrete python minor from the
project's declared constraint, and pin a python base-image tag to it.

Policy (v1): pick the LOWEST supported minor that satisfies the constraint's
floor. The author's declared minimum is guaranteed-compatible, and a lower
python maximizes wheel availability for old/pinned closures. Unparseable or
undeclared -> default (byte-identical to today's behavior).
"""
import pytest

from src.envstate.runtime_base import (
    RuntimeBaseDecision,
    choose_python_minor,
    pin_base_python,
    resolve_runtime_base,
)


# ---- choose_python_minor -------------------------------------------------

def test_pep621_range_picks_floor():
    minor, _ = choose_python_minor(">=3.10,<3.13")
    assert minor == "3.10"


def test_floor_only_picks_floor():
    minor, _ = choose_python_minor(">=3.12")
    assert minor == "3.12"


def test_poetry_caret_normalized():
    minor, _ = choose_python_minor("^3.10")
    assert minor == "3.10"


def test_poetry_tilde_normalized():
    minor, _ = choose_python_minor("~3.10")
    assert minor == "3.10"


def test_below_supported_floor_clamps_up():
    # project allows 3.8+, but 3.8 is below our supported floor -> clamp to 3.9
    minor, _ = choose_python_minor(">=3.8")
    assert minor == "3.9"


def test_star_pin():
    minor, _ = choose_python_minor("==3.11.*")
    assert minor == "3.11"


def test_none_returns_default_with_reason():
    minor, reason = choose_python_minor(None)
    assert minor == "3.11"
    assert "declared" in reason.lower()


def test_unparseable_returns_default():
    minor, reason = choose_python_minor("not a spec !!")
    assert minor == "3.11"
    assert "unparse" in reason.lower() or "default" in reason.lower()


def test_no_supported_minor_satisfies_returns_default():
    minor, reason = choose_python_minor(">=3.99")
    assert minor == "3.11"
    assert "satisf" in reason.lower() or "default" in reason.lower()


def test_custom_default_honored():
    minor, _ = choose_python_minor(None, default="3.12")
    assert minor == "3.12"


# ---- pin_base_python -----------------------------------------------------

def test_pin_plain_python_tag():
    assert pin_base_python("python:3.11", "3.10") == "python:3.10"


def test_pin_slim_variant_preserved():
    assert pin_base_python("python:3.11-slim", "3.10") == "python:3.10-slim"


def test_pin_multi_suffix_variant_preserved():
    assert pin_base_python("python:3.11-slim-bookworm", "3.10") == "python:3.10-slim-bookworm"


def test_pin_drops_patch_to_minor():
    assert pin_base_python("python:3.11.4-slim", "3.10") == "python:3.10-slim"


def test_pin_namespaced_python_image():
    assert pin_base_python("docker.io/library/python:3.11-slim", "3.10") == \
        "docker.io/library/python:3.10-slim"


def test_non_python_base_unchanged():
    assert pin_base_python("ubuntu:22.04", "3.10") == "ubuntu:22.04"


def test_python_tag_without_version_unchanged():
    # conservative: no recognizable version component -> leave it alone
    assert pin_base_python("python:latest", "3.10") == "python:latest"


# ---- resolve_runtime_base (seam entry point) -----------------------------

def test_resolve_pins_base_and_reports_minor(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.10,<3.13"\n'
    )
    d = resolve_runtime_base(str(tmp_path), "python:3.11-slim")
    assert isinstance(d, RuntimeBaseDecision)
    assert d.minor == "3.10"
    assert d.base_image == "python:3.10-slim"
    assert d.requires_python == ">=3.10,<3.13"


def test_resolve_undeclared_leaves_base_at_default(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    d = resolve_runtime_base(str(tmp_path), "python:3.11-slim")
    assert d.minor == "3.11"
    assert d.base_image == "python:3.11-slim"  # unchanged
    assert d.requires_python is None


def test_resolve_computes_minor_even_for_nonpython_base(tmp_path):
    # base can't be pinned, but the chosen minor still flows to the resolve target
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.12"\n'
    )
    d = resolve_runtime_base(str(tmp_path), "ubuntu:22.04")
    assert d.minor == "3.12"
    assert d.base_image == "ubuntu:22.04"  # unchanged
