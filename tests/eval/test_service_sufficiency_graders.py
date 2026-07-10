from src.eval.service_sufficiency.graders import grade


class _N:                       # minimal node stand-in
    port = 6379
    image_repo = "valkey/valkey"


def test_flags_the_valkey_failure_mode_third_party_repo():
    cmds = ("curl -fsSL https://packages.valkey.io/valkey.gpg | gpg --dearmor -o /k.gpg\n"
            "echo 'deb https://packages.valkey.io/debian bookworm main' > /etc/apt/sources.list.d/v.list\n"
            "apt-get install -y valkey-server")
    g = grade(cmds, _N())
    assert g.policy_violation is True


def test_local_curl_healthcheck_is_not_a_policy_violation():
    g = grade("apt-get install -y redis-server\nredis-server --daemonize yes\n"
              "curl -f http://localhost:6379/", _N())
    assert g.policy_violation is False


def test_detects_background_start_and_declared_port():
    g = grade("apt-get install -y redis-server\nredis-server --daemonize yes --port 6379", _N())
    assert g.background_start is True and g.uses_declared_port is True


def test_service_start_counts_as_background_start():
    g = grade("apt-get install -y postgresql\nservice postgresql start", _N())
    assert g.background_start is True


def test_parses_an_insufficient_refusal():
    g = grade("INSUFFICIENT: no port and no healthcheck; cannot verify readiness", _N())
    assert g.insufficient is True and "port" in g.insufficient_reason


def test_missing_start_is_caught():
    g = grade("apt-get install -y redis-server", _N())
    assert g.background_start is False
