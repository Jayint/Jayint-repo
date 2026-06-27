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


# I2 regression lock: the field path in should_add_postgres_host_alias fires on
# confirmed_in_image_services even when NO regex tokens appear in any command.
def test_should_add_postgres_host_alias_field_path_fires_without_regex_tokens():
    from run_repo2run_benchmark import should_add_postgres_host_alias

    # run_summary carries confirmed_in_image_services; commands contain NONE of
    # pg_ctlcluster|postgres|psql — so this MUST be True via the field path only.
    result = should_add_postgres_host_alias(
        None, [], [], {"confirmed_in_image_services": [{"kind": "postgres"}]}
    )
    assert result is True, (
        "Field path did not fire: should_add_postgres_host_alias must return True "
        "when run_summary['confirmed_in_image_services'] is non-empty, "
        "even with no regex tokens in commands."
    )


# I2 threading test: evaluate_built_image must accept and thread run_summary so the
# field-driven alias path can fire at the real call site.
def test_evaluate_built_image_accepts_run_summary_kwarg():
    """evaluate_built_image must have a run_summary parameter (I2 threading fix)."""
    import inspect
    from run_repo2run_benchmark import evaluate_built_image

    sig = inspect.signature(evaluate_built_image)
    assert "run_summary" in sig.parameters, (
        "evaluate_built_image is missing 'run_summary' parameter — I2 threading fix not applied"
    )
