import dataclasses

import pytest

from graph.python.services.service_evidence import (
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


def _make_node(env=None, raw=None):
    return ServiceNode(
        id="service:db", name="db", image="postgres:16",
        image_repo="postgres", image_tag="16",
        ports=(Port(container=5432, host=5432),), port=5432, port_source="ports",
        endpoint="localhost:5432",
        env={"POSTGRES_DB": "app"} if env is None else env,
        command=None, entrypoint=None, volumes=(), seed=(),
        check=Check(command="pg_isready", source="declared_healthcheck"),
        depends_on=(), relevance="ci_service",
        provenance=(Source(file="ci.yml", locator="jobs.t.services.db", kind="ci"),),
        raw={"ci": {"image": "postgres:16"}} if raw is None else raw,
        state="certifiable_obligation", unresolved=(),
    )


def test_env_is_defensively_copied_from_caller():
    # env values are immutable strings, so an outer-dict copy is total isolation.
    env = {"POSTGRES_DB": "app"}
    node = _make_node(env=env)

    env["POSTGRES_DB"] = "MUTATED"
    env["INJECTED"] = "x"

    assert node.env == {"POSTGRES_DB": "app"}


def test_raw_is_deep_copied_so_nested_yaml_is_not_aliased():
    # raw values are nested mutable YAML dicts. A shallow outer copy still shares
    # the inner dicts with the caller, so mutating a nested value would leak in.
    caller_raw = {"ci": {"image": "postgres:16"}}
    node = _make_node(raw=caller_raw)

    caller_raw["ci"]["image"] = "MUTATED"

    assert node.raw["ci"]["image"] == "postgres:16"


def test_service_node_is_unhashable_because_it_holds_dict_fields():
    # env/raw are dicts, so ServiceNode cannot be hashable. This is an
    # intentional contract: do NOT "fix" it with unsafe_hash=True.
    node = _make_node()
    with pytest.raises(TypeError):
        hash(node)
