"""Bug #2 — exclude_newer from pinned roots (pins.py). Injected fetch, no network."""

from __future__ import annotations

from python_deps.depgraph.pins import (
    compute_exclude_newer,
    incompatible_python_pins,
    parse_pinned_roots,
    pypi_requires_python,
    pypi_upload_date,
)


def test_parse_pinned_roots_keeps_only_exact_pins():
    roots = [
        (None, "opencv-python==4.9.0.80"),
        ("import:flask", "flask>=2.0"),  # range, not a pin
        (None, "numpy"),  # unpinned
        (None, "Pillow==10.3.0"),
    ]
    assert parse_pinned_roots(roots) == [
        ("opencv-python", "4.9.0.80"),
        ("Pillow", "10.3.0"),
    ]


def _fetch_factory(dates: dict[tuple[str, str], str]):
    def fetch(name, version):
        ts = dates.get((name, version))
        if ts is None:
            raise RuntimeError("404")
        return {"urls": [{"upload_time": ts}]}

    return fetch


def test_pypi_upload_date_extracts_date():
    fetch = _fetch_factory({("opencv-python", "4.9.0.80"): "2023-12-31T10:11:12"})
    assert pypi_upload_date("opencv-python", "4.9.0.80", fetch) == "2023-12-31"


def test_pypi_upload_date_none_on_failure():
    fetch = _fetch_factory({})  # raises -> None
    assert pypi_upload_date("nope", "9.9.9", fetch) is None


def _python_fetch_factory(specs: dict[tuple[str, str], str | None]):
    def fetch(name, version):
        if (name, version) not in specs:
            raise RuntimeError("404")
        return {"info": {"requires_python": specs[(name, version)]}}

    return fetch


def test_pypi_requires_python_extracts_release_constraint():
    fetch = _python_fetch_factory({("lazyllm", "1.1.1"): ">=3.10,<3.13"})
    assert pypi_requires_python("lazyllm", "1.1.1", fetch) == ">=3.10,<3.13"


def test_incompatible_python_pins_returns_only_explicit_exclusions():
    fetch = _python_fetch_factory(
        {
            ("lazyllm", "1.1.1"): ">=3.10,<3.13",
            ("memu-py", "1.5.1"): ">=3.13",
            ("unknown", "1.0"): None,
        }
    )
    bad = incompatible_python_pins(
        [("lazyllm", "1.1.1"), ("memu-py", "1.5.1"), ("unknown", "1.0")],
        "3.13",
        fetch=fetch,
    )
    assert [(item.name, item.version, item.requires_python) for item in bad] == [
        ("lazyllm", "1.1.1", ">=3.10,<3.13")
    ]


def test_incompatible_python_pins_keeps_on_bad_or_missing_metadata():
    fetch = _python_fetch_factory({("broken", "1.0"): "not a specifier"})
    assert incompatible_python_pins(
        [("broken", "1.0"), ("missing", "1.0")], "3.13", fetch=fetch
    ) == []


def test_incompatible_python_pins_uses_full_specifier_and_prerelease_semantics():
    fetch = _python_fetch_factory(
        {
            ("excluded-minor", "1.0"): ">=3.12,<3.14,!=3.13.*",
            ("preview-ok", "1.0rc1"): ">=3.13.0rc1,<3.14",
        }
    )
    bad = incompatible_python_pins(
        [("excluded-minor", "1.0"), ("preview-ok", "1.0rc1")],
        "3.13.0rc2",
        fetch=fetch,
    )
    assert [(item.name, item.version) for item in bad] == [
        ("excluded-minor", "1.0")
    ]


def test_compute_exclude_newer_is_newest_pin_plus_one_day():
    roots = [(None, "opencv-python==4.9.0.80"), (None, "Pillow==10.3.0")]
    fetch = _fetch_factory({
        ("opencv-python", "4.9.0.80"): "2023-12-31T00:00:00",
        ("Pillow", "10.3.0"): "2024-04-01T00:00:00",  # newer
    })
    # newest pin (2024-04-01) + 1 day, so the pinned release itself is included
    assert compute_exclude_newer(roots, fetch) == "2024-04-02"


def test_compute_exclude_newer_none_without_pins():
    roots = [(None, "opencv-python"), ("import:flask", "flask>=2")]
    assert compute_exclude_newer(roots, _fetch_factory({})) is None


def test_compute_exclude_newer_none_when_dates_unresolved():
    roots = [(None, "ghost==1.2.3")]
    assert compute_exclude_newer(roots, _fetch_factory({})) is None  # fetch raises
