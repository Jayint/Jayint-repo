from python_deps.depgraph.service_evidence import Mount, Port
from python_deps.depgraph.service_parse import (
    is_templated, parse_command, parse_depends_on, parse_entrypoint, parse_env,
    parse_expose, parse_image, parse_ports, parse_volumes, seed_mounts,
)


def test_parse_image_lexical_only():
    assert parse_image("postgres:16") == ("postgres", "16")
    assert parse_image("ghcr.io/o/i:v1") == ("ghcr.io/o/i", "v1")
    assert parse_image("redis") == ("redis", None)
    assert parse_image("img@sha256:abc") == ("img", None)      # digest dropped


def test_templated_TAG_keeps_the_repo_and_nulls_the_tag():
    # rq/rq: the service must survive; only the tag is unknown.
    assert parse_image("valkey/valkey:${{ matrix.valkey-version }}") == ("valkey/valkey", None)


def test_templated_IMAGE_NAME_drops_the_node():
    # PostHog: "$REGISTRY_URL:$POSTHOG_APP_TAG" — nothing usable.
    assert parse_image("$REGISTRY_URL:$POSTHOG_APP_TAG") == ("", None)


def test_is_templated_catches_bare_dollar_var():
    assert is_templated("${{ matrix.x }}") and is_templated("$REGISTRY_URL")
    assert not is_templated("postgres")


def test_parse_ports_handles_ranges_and_long_syntax_and_templates():
    assert parse_ports({"ports": ["6379:6379"]}) == (Port(6379, 6379),)
    assert parse_ports({"ports": ["127.0.0.1:5432:5432"]}) == (Port(5432, 5432),)
    assert parse_ports({"ports": ["5432"]}) == (Port(5432, None),)
    assert parse_ports({"ports": ["8080:80/tcp"]}) == (Port(80, 8080),)
    # a published RANGE must not raise (real: baserow)
    assert parse_ports({"ports": [{"target": 80, "published": "5000-5999"}]}) == (Port(80, None),)
    # templated host port: keep the container port
    assert parse_ports({"ports": ["${PORT}:5432"]}) == (Port(5432, None),)
    assert parse_ports({"ports": "not-a-list"}) == ()


def test_short_syntax_port_range_is_skipped_not_fatal():
    # "5000-5999:5000-5999" (short-syntax RANGE) once crashed int(); it must be
    # skipped, not fatal — and a well-formed sibling in the same list survives.
    assert parse_ports({"ports": ["5000-5999:5000-5999", "6379:6379"]}) == (Port(6379, 6379),)


def test_parse_env_accepts_dict_list_and_ci_env_key():
    assert parse_env({"environment": {"A": "1"}}) == {"A": "1"}
    assert parse_env({"environment": ["A=1", "B=2"]}) == {"A": "1", "B": "2"}
    assert parse_env({"env": {"A": "1"}}) == {"A": "1"}      # GH Actions uses `env:`
    assert parse_env({}) == {}


def test_parse_command_and_entrypoint_join_lists():
    assert parse_command({"command": ["postgres", "-c", "x=1"]}) == "postgres -c x=1"
    assert parse_command({"command": "redis-server --appendonly yes"}) == "redis-server --appendonly yes"
    assert parse_command({}) is None
    assert parse_entrypoint({"entrypoint": ["/bin/sh", "-c"]}) == "/bin/sh -c"


def test_parse_volumes_and_seed_subset():
    vols = parse_volumes({"volumes": [
        "./init.sql:/docker-entrypoint-initdb.d/init.sql",
        "data:/var/lib/postgresql/data",
        {"source": "./x", "target": "/initdb/x"},
    ]})
    assert vols[0] == Mount("./init.sql", "/docker-entrypoint-initdb.d/init.sql")
    assert seed_mounts(vols) == (vols[0], vols[2])           # initdb.d + /initdb only
    assert parse_volumes({"volumes": "nope"}) == ()


def test_parse_depends_on_list_and_mapping():
    assert parse_depends_on({"depends_on": ["a", "b"]}) == ("a", "b")
    assert parse_depends_on({"depends_on": {"a": {"condition": "x"}}}) == ("a",)
    assert parse_depends_on({}) == ()


def test_parse_expose():
    assert parse_expose({"expose": [5432, "6379/tcp"]}) == (5432, 6379)
    assert parse_expose({}) == ()
