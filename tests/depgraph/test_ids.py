"""Tests for ids.py helpers — currently covers data_asset_id."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from python_deps.depgraph.ids import data_asset_id


def test_data_asset_id_prefix():
    assert data_asset_id("fixtures.db") == "data:fixtures.db"


def test_data_asset_id_plain_name():
    assert data_asset_id("seed") == "data:seed"
