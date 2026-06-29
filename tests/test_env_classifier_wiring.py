import inspect, re
import agent as agent_mod


def test_agent_builds_classifier_under_flag_and_client():
    src = inspect.getsource(agent_mod.DockerAgent)
    assert "make_construction_classifier" in src
    # gated on the flag AND a client; passed into build_advisory_for_repo as classify=
    assert "enable_llm_env_classifier" in src
    assert re.search(r"classify\s*=", src)


def test_disable_flag_exists():
    src = inspect.getsource(agent_mod)
    assert "--disable-llm-env-classifier" in src
