from graph.service_evidence import Mount, Port
from graph.service_parse import (
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


def test_malformed_url_never_raises():
    """`urlparse("redis://[db:6379")` raises ValueError: Invalid IPv6 URL.
    derive_port must degrade the field, never explode."""
    assert derive_port((), (), {}, "db", ("redis://[db:6379",)) == (None, "none")


def test_malformed_url_does_not_fall_back_to_the_regex():
    """A `://` value is decided by urlparse ALONE. Falling back to the token regex
    when urlparse fails would re-open the userinfo hole this rung exists to close."""
    assert derive_port((), (), {}, "db", ("postgres://[db:5432@other/app",)) == (None, "none")


def test_port_ladder_gives_up_cleanly():
    assert derive_port((), (), {}, "svc", ()) == (None, "none")


from graph.service_parse import (
    ci_healthcheck, compose_healthcheck, derive_check, tcp_check,
)


def test_compose_healthcheck_strips_CMD_and_CMD_SHELL():
    assert compose_healthcheck({"healthcheck": {"test": ["CMD", "pg_isready", "-U", "u"]}})[0] \
        == "pg_isready -U u"
    assert compose_healthcheck({"healthcheck": {"test": ["CMD-SHELL", "redis-cli ping"]}})[0] \
        == "redis-cli ping"
    assert compose_healthcheck({"healthcheck": {"test": "curl -f http://x"}})[0] == "curl -f http://x"


def test_compose_healthcheck_NONE_disables_and_timing_is_captured():
    assert compose_healthcheck({"healthcheck": {"test": ["NONE"]}}) == (None, {})
    _cmd, timing = compose_healthcheck(
        {"healthcheck": {"test": ["CMD", "x"], "interval": "10s", "retries": 5}})
    assert timing == {"interval": "10s", "retries": 5}


def test_ci_healthcheck_parses_health_cmd_from_options():
    # rq/rq's real workflow, folded-block `options:`
    entry = {"options": '--health-cmd "valkey-cli ping" --health-interval 10s '
                        '--health-timeout 5s --health-retries 5'}
    cmd, timing = ci_healthcheck(entry)
    assert cmd == "valkey-cli ping"
    assert timing == {"interval": "10s", "timeout": "5s", "retries": "5"}


def test_ci_healthcheck_parses_the_equals_form():
    """`--health-cmd=X` (and `--health-interval=10s`, etc.) are valid docker flags that
    appear in real workflows; shlex yields `--health-cmd="valkey-cli ping"` as ONE token.
    Both the space-separated and the equals form must be recovered."""
    entry = {"options": '--health-cmd="valkey-cli ping" --health-interval=10s '
                        '--health-timeout=5s --health-retries=5'}
    cmd, timing = ci_healthcheck(entry)
    assert cmd == "valkey-cli ping"
    assert timing == {"interval": "10s", "timeout": "5s", "retries": "5"}


def test_ci_healthcheck_absent_options():
    assert ci_healthcheck({"image": "redis"}) == (None, {})


def test_tcp_check_is_the_portable_python_one_liner():
    cmd = tcp_check(5432)
    assert cmd.startswith("python3 -c")     # `python` is absent from python3-only images
    assert "socket.create_connection" in cmd and "5432" in cmd
    assert "nc " not in cmd and "/dev/tcp" not in cmd


def test_check_ladder_precedence():
    declared = derive_check("pg_isready", {"interval": "10s"}, 5432)
    assert declared.source == "declared_healthcheck" and declared.command == "pg_isready"
    assert declared.interval_s == "10s"

    derived = derive_check(None, {}, 6379)
    assert derived.source == "tcp_port" and "6379" in derived.command

    nothing = derive_check(None, {}, None)
    assert nothing.source == "none" and nothing.command is None


def test_a_non_read_only_healthcheck_falls_through_to_tcp():
    """The check runs inside certification, so it must not mutate. `curl -f ...`
    fails patch_gate.is_read_only -> fall down the ladder, do NOT drop the node.
    Real: PostHog elasticsearch, mlflow storage, gitingest minio (11/54 corpus)."""
    c = derive_check("curl -f http://localhost:9200/_cluster/health", {}, 9200)
    assert c.source == "tcp_port" and "9200" in c.command


def test_a_non_read_only_healthcheck_with_no_port_becomes_none():
    c = derive_check("wget -q --spider http://localhost:8123/ping", {}, None)
    assert c.source == "none" and c.command is None


def test_the_tcp_check_itself_is_read_only():
    from graph.patch.gate import is_read_only
    assert is_read_only(tcp_check(5432))


def test_a_whitespace_only_declared_healthcheck_falls_through_to_tcp():
    """`healthcheck: {test: "   "}` carries no real command — the host would execute
    whitespace. It must NOT be admitted as a declared check; with a port it falls through
    to the TCP rung, with none it degrades to `none` — but the node is still produced."""
    with_port = derive_check("   ", {}, 6379)
    assert with_port.source == "tcp_port" and "6379" in with_port.command

    without_port = derive_check("   ", {}, None)
    assert without_port.source == "none" and without_port.command is None


from graph.service_parse import _OPAQUE, expand_declared_defaults


def test_expand_declared_defaults_reads_the_literal_the_file_declares():
    assert expand_declared_defaults("${MINIO_IMAGE:-minio/minio}") == "minio/minio"
    assert expand_declared_defaults("${A:-${B:-c}}") == "c"            # nested
    assert expand_declared_defaults("${REGISTRY:-}") == ""             # empty default
    assert expand_declared_defaults("${VAR-x}") == "x"                 # unset-only form
    assert expand_declared_defaults("${VAR:?boom}") == _OPAQUE         # no declared value
    assert expand_declared_defaults("${VAR}") == _OPAQUE
    assert expand_declared_defaults("$VAR") == _OPAQUE
    assert expand_declared_defaults("${{ matrix.v }}") == _OPAQUE      # GH Actions
    assert expand_declared_defaults("${unterminated") is None


def test_parse_image_interpolates_before_it_parses():
    # `:` and `/` inside a ${...} span are template syntax, not reference delimiters.
    assert parse_image("${MINIO_IMAGE:-minio/minio}") == ("minio/minio", None)
    assert parse_image("${REDIS_IMAGE:-redis:latest}") == ("redis", "latest")
    assert parse_image("${ES_IMAGE:-docker.elastic.co/elasticsearch/elasticsearch:8.13.4}") \
        == ("docker.elastic.co/elasticsearch/elasticsearch", "8.13.4")
    assert parse_image("${REGISTRY:-}${IMG:-aiidateam/aiida-core-base}${TAG:-}") \
        == ("aiidateam/aiida-core-base", None)
    assert parse_image("pgvector/pgvector:pg${POSTGRES_IMAGE_VERSION:-14}") == ("pgvector/pgvector", "pg14")


def test_a_templated_registry_prefix_is_not_a_usable_name():
    # `head` was never checked before: `${REG}/img` is not a pullable reference.
    assert parse_image("${REG}/img:1.2") == ("", None)


def test_the_spec_invariants_survive_interpolation():
    assert parse_image("valkey/valkey:${{ matrix.valkey-version }}") == ("valkey/valkey", None)
    assert parse_image("$REGISTRY_URL:$POSTHOG_APP_TAG") == ("", None)
    assert parse_image("${REGISTRY_URL}-node:${POSTHOG_NODE_TAG:-latest}") == ("", None)
    assert parse_image("img@sha256:abc") == ("img", None)
    assert parse_image("postgres:16") == ("postgres", "16")
