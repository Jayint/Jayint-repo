import dataclasses

import pytest

from src.bench_emit.types import EmittedEnv


def test_emitted_env_minimal_defaults():
    e = EmittedEnv(dockerfile=None)
    assert e.dockerfile is None
    assert e.scripts == {} and e.meta == {}


def test_emitted_env_frozen():
    e = EmittedEnv(dockerfile="FROM x", scripts={"setup.sh": "echo hi"}, meta={"agent": "v3"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.dockerfile = "FROM y"  # type: ignore[misc]


def test_emitted_env_holds_payload():
    e = EmittedEnv(dockerfile="FROM x", scripts={"setup.sh": "s"}, meta={"agent": "v3", "produce_s": 1.5})
    assert e.dockerfile == "FROM x"
    assert e.scripts["setup.sh"] == "s"
    assert e.meta["produce_s"] == 1.5
