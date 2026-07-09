import dataclasses
import pytest

from python_deps.depgraph.service_evidence import (
    CHECK_SOURCES, PORT_SOURCES, RELEVANCES, STATES,
    Check, Mount, Port, ServiceNode, Source,
)


def test_enums_are_exactly_the_spec_values():
    assert PORT_SOURCES == ("ports", "expose", "env_dsn", "sibling_dsn", "none")
    assert CHECK_SOURCES == ("declared_healthcheck", "tcp_port", "none")
    assert RELEVANCES == ("ci_service", "ci_referenced_compose",
                          "root_compose", "unreferenced_compose")
    assert STATES == ("certifiable_obligation", "declared_unverifiable")


def test_service_node_is_frozen_and_carries_one_executable_string():
    node = ServiceNode(
        id="service:db", name="db", image="postgres:16",
        image_repo="postgres", image_tag="16",
        ports=(Port(container=5432, host=5432),), port=5432, port_source="ports",
        endpoint="localhost:5432", env={"POSTGRES_DB": "app"},
        command=None, entrypoint=None, volumes=(), seed=(),
        check=Check(command="pg_isready", source="declared_healthcheck"),
        depends_on=(), relevance="ci_service",
        provenance=(Source(file="ci.yml", locator="jobs.t.services.db", kind="ci"),),
        raw={"ci": {"image": "postgres:16"}},
        state="certifiable_obligation", unresolved=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.name = "other"          # type: ignore[misc]
    assert node.check.command == "pg_isready"


def test_check_defaults_are_none_shaped():
    c = Check(command=None, source="none")
    assert c.interval_s is None and c.retries is None and c.timeout_s is None
