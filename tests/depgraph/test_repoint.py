"""Pure unit tests for repoint (generic compose-host -> loopback DSN rewrite).

Evidence-only construction: a config DSN binds to a service by its DECLARED
HOSTNAME (the service's compose/CI key), never by a service ``kind``. Matching by
hostname is strictly more precise -- it disambiguates two services of the same kind,
which a kind-match cannot -- and it does not silently drop an exotic-scheme DSN.
"""

from python_deps.depgraph.repoint import render_bind_steps


def test_bind_matches_by_declared_hostname_not_kind():
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("DATABASE_URL", "postgres://u:p@db:5432/app")],
    )
    assert steps == ["export DATABASE_URL=postgres://u:p@127.0.0.1:5432/app"]


def test_bind_skips_a_dsn_whose_host_is_not_a_declared_service():
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("EXTERNAL", "postgres://u:p@rds.amazonaws.com:5432/app")],
    )
    assert steps == []


def test_bind_disambiguates_two_services_of_the_same_kind():
    steps = render_bind_steps(
        service_names=("primary", "replica"),
        configs=[("A", "postgres://u@primary:5432/x"), ("B", "postgres://u@replica:5433/x")],
    )
    assert len(steps) == 2 and "5432" in steps[0] and "5433" in steps[1]


def test_bind_skips_non_dsn_values():
    assert render_bind_steps(service_names=("db",), configs=[("X", "hello")]) == []


def test_bind_matches_an_exotic_scheme_dsn_by_hostname():
    # urlsplit-only host match: a scheme ``service_from_url`` does not know (e.g.
    # clickhouse) still binds when its host is a declared service -- the exotic DSN is
    # NOT silently dropped.
    steps = render_bind_steps(
        service_names=("analytics",),
        configs=[("CH_URL", "clickhouse://u@analytics:9000/db")],
    )
    assert steps == ["export CH_URL=clickhouse://u@127.0.0.1:9000/db"]


def test_bind_preserves_query_and_omits_absent_port():
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("DATABASE_URL", "postgresql://app@db/app?sslmode=require")],
    )
    assert steps == ["export DATABASE_URL=postgresql://app@127.0.0.1/app?sslmode=require"]


def test_bind_ignores_empty_service_names():
    steps = render_bind_steps(
        service_names=("", None),
        configs=[("CACHE_URL", "redis://redis:6379/0")],
    )
    assert steps == []


def test_bind_skips_a_dsn_with_an_unparseable_port():
    # C2 regression: `urlsplit` parses this happily, but `.port` raises
    # ("bad" is not an int). render_bind_steps must SKIP it, never raise -- a broad
    # `except` upstream would otherwise wipe the whole service tier.
    steps = render_bind_steps(
        service_names=("db",),
        configs=[("URL", "postgres://db:bad/app")],
    )
    assert steps == []
