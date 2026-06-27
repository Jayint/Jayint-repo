import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_repo2run_benchmark as R  # noqa: E402


def test_compose_emits_export_when_var_present():
    rs = {"confirmed_in_image_services": [{
        "kind": "postgres", "start": "S", "wait": "W", "createdb": "C",
        "var": "DB_STRING", "url": "postgresql://postgres:postgres@127.0.0.1:5432/appdb"}]}
    cmds = R.compose_in_image_service_commands(rs)
    assert any(c.startswith("export DB_STRING=") for c in cmds)
    # export comes after createdb
    assert cmds.index("C") < next(i for i, c in enumerate(cmds) if c.startswith("export DB_STRING="))


def test_compose_no_export_without_var():
    rs = {"confirmed_in_image_services": [{"kind": "postgres", "start": "S", "wait": "W"}]}
    cmds = R.compose_in_image_service_commands(rs)
    assert not any(c.startswith("export ") for c in cmds)
