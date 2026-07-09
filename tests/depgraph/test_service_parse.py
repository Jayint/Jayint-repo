from python_deps.depgraph.service_evidence import Mount, Port
from python_deps.depgraph.service_parse import (
    derive_port, is_templated, parse_command, parse_depends_on,
    parse_entrypoint, parse_env, parse_expose, parse_image, parse_ports,
    parse_volumes, seed_mounts,
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


def test_port_ladder_prefers_declared_ports():
    got = derive_port((Port(5432, 5432),), (6379,), {"URL": "x://h:1234"}, "db", ())
    assert got == (5432, "ports")


def test_port_ladder_falls_back_to_expose():
    assert derive_port((), (6379,), {}, "cache", ()) == (6379, "expose")


def test_expose_beats_own_env_dsn():
    """Pins the MIDDLE of the ladder, not just its ends."""
    env = {"DATABASE_URL": "postgres://u:p@db:5432/app"}
    assert derive_port((), (6379,), env, "db", ()) == (6379, "expose")


def test_port_ladder_falls_back_to_own_env_dsn():
    env = {"DATABASE_URL": "postgres://u:p@db:5432/app"}
    assert derive_port((), (), env, "db", ()) == (5432, "env_dsn")


def test_own_env_dsn_beats_sibling_dsn():
    """Pins the MIDDLE of the ladder: own evidence wins over a sibling's."""
    env = {"URL": "postgres://u:p@db:5432/app"}
    siblings = ("postgres://u:p@db:9999/app",)
    assert derive_port((), (), env, "db", siblings) == (5432, "env_dsn")


def test_port_ladder_rescues_from_sibling_url_dsn():
    # `db` declares nothing; the APP declares the DSN naming host `db`.
    siblings = ("postgres://u:p@db:5432/app", "redis://cache:6379/0")
    assert derive_port((), (), {}, "db", siblings) == (5432, "sibling_dsn")
    assert derive_port((), (), {}, "cache", siblings) == (6379, "sibling_dsn")


def test_port_ladder_rescues_from_sibling_bare_token():
    """8 of the PoC's 9 real rescues are bare `host:port` tokens, not URLs:
    KAFKA_HOSTS=kafka:9092, TEMPORAL_ADDRESS=temporal:7233, MEMCACHE_LOCATION=memcached:11211.
    The regex rung must survive."""
    assert derive_port((), (), {}, "kafka", ("kafka:9092",)) == (9092, "sibling_dsn")
    assert derive_port((), (), {}, "redis", ("local:redis:6379",)) == (6379, "sibling_dsn")


def test_sibling_url_must_match_the_HOST_not_the_userinfo():
    """THE CRITICAL CASE. In `postgres://db:5432@other/app`, `db` is the USERNAME and
    `5432` the PASSWORD; the real host is `other`. A bare `\bdb:5432\b` regex wrongly
    rescues 5432 for service `db`. A value containing `://` MUST be decided by urlparse."""
    assert derive_port((), (), {}, "db", ("postgres://db:5432@other/app",)) == (None, "none")


def test_sibling_url_attributes_the_port_to_the_real_host():
    siblings = ("postgres://db:5432@other:6543/app",)
    assert derive_port((), (), {}, "other", siblings) == (6543, "sibling_dsn")
    assert derive_port((), (), {}, "db", siblings) == (None, "none")


def test_sibling_rescue_requires_a_name_boundary():
    # must not match "mydb:5432" when looking for service "db"
    assert derive_port((), (), {}, "db", ("mydb:5432",)) == (None, "none")


def test_sibling_url_with_no_port_yields_nothing():
    assert derive_port((), (), {}, "db", ("postgres://u:p@db/app",)) == (None, "none")


def test_sibling_url_with_templated_port_does_not_raise():
    assert derive_port((), (), {}, "db", ("redis://db:$PORT",)) == (None, "none")


def test_sibling_url_with_templated_host_does_not_raise():
    assert derive_port((), (), {}, "db", ("redis://$HOST:6379",)) == (None, "none")


def test_port_ladder_gives_up_cleanly():
    assert derive_port((), (), {}, "svc", ()) == (None, "none")
