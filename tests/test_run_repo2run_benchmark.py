"""Tests for compose_in_image_service_commands and related helpers.

These tests exercise the eval-side composer that reads
run_summary["confirmed_in_image_services"] and prepends start/wait/createdb
commands to the runtime_commands that flow into the test wrapper.
"""


def test_compose_in_image_service_commands():
    from run_repo2run_benchmark import compose_in_image_service_commands

    rs = {
        "confirmed_in_image_services": [
            {
                "kind": "postgres",
                "port": 5432,
                "db": "appdb",
                "start": "runuser -u postgres -- pg_ctlcluster 15 main start",
                "wait": "for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p 5432 && break; sleep 1; done",
                "createdb": "runuser -u postgres -- createdb appdb",
            }
        ]
    }
    cmds = compose_in_image_service_commands(rs)
    assert cmds[0].startswith("runuser -u postgres -- pg_ctlcluster")
    assert any("pg_isready" in c for c in cmds)
    assert cmds[-1] == "runuser -u postgres -- createdb appdb"
    assert all("|| true" not in c for c in cmds)  # createdb FATAL
    assert compose_in_image_service_commands({}) == []
    assert compose_in_image_service_commands(None) == []


def test_no_createdb_line_when_db_absent():
    from run_repo2run_benchmark import compose_in_image_service_commands

    rs = {
        "confirmed_in_image_services": [
            {"kind": "postgres", "port": 5432, "db": None, "start": "S", "wait": "W", "createdb": None}
        ]
    }
    cmds = compose_in_image_service_commands(rs)
    assert cmds == ["S", "W"]
