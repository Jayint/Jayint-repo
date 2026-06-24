import inspect
from src.envstate.orchestrator import run_v1


def test_run_v1_exposes_enable_dep_emit():
    sig = inspect.signature(run_v1)
    assert "enable_dep_emit" in sig.parameters
    assert sig.parameters["enable_dep_emit"].default is False


def test_env_var_bridge_present():
    import multi_docker_eval_adapter as ad
    src = inspect.getsource(ad)
    assert "DOCKERAGENT_ENABLE_DEP_EMIT" in src
