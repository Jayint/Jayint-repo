"""Unit tests for certified import->package relink (no Docker/network)."""

from __future__ import annotations

from python_deps.depgraph.relink import (
    PACKAGES_DIST_CMD,
    parse_packages_distributions,
)


def test_parse_valid_map():
    stdout = '{"cv2": ["opencv-python"], "yaml": ["PyYAML"], "google": ["google-auth", "protobuf"]}'
    out = parse_packages_distributions(stdout)
    assert out["cv2"] == ["opencv-python"]
    assert out["google"] == ["google-auth", "protobuf"]


def test_parse_malformed_returns_empty():
    assert parse_packages_distributions("not json") == {}
    assert parse_packages_distributions("") == {}
    assert parse_packages_distributions("[1, 2, 3]") == {}


def test_command_is_stdlib_only():
    assert "packages_distributions" in PACKAGES_DIST_CMD
    assert "importlib.metadata" in PACKAGES_DIST_CMD
