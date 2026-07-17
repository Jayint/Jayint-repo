from types import SimpleNamespace

from src.sandbox import Sandbox


class _Container:
    def __init__(self, short_id):
        self.short_id = short_id
        self.calls = []
        self.stopped = False
        self.removed = False

    def exec_run(self, command, workdir=None):
        self.calls.append((command, workdir))
        return SimpleNamespace(exit_code=0, output=b"ok")

    def stop(self):
        self.stopped = True

    def remove(self):
        self.removed = True


class _Containers:
    def __init__(self, *containers):
        self.queue = list(containers)
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.queue.pop(0)


def _sandbox(*candidate_containers):
    sandbox = Sandbox.__new__(Sandbox)
    sandbox.client = SimpleNamespace(containers=_Containers(*candidate_containers))
    sandbox.workdir = "/app"
    sandbox.volumes = None
    sandbox.platform = None
    sandbox.container = _Container("working")
    sandbox.current_image = "base-image"
    sandbox.last_success_image = "base-image"
    sandbox.named_checkpoints = {"base": "image-base", "exec-1-good": "image-prefix"}
    sandbox.candidate_containers = {}
    return sandbox


def test_candidate_is_isolated_until_promoted():
    candidate = _Container("candidate")
    sandbox = _sandbox(candidate)
    original = sandbox.container

    handle = sandbox.create_candidate_container("txn-1", "exec-1-good")

    assert sandbox.container is original
    assert not original.stopped and not original.removed
    assert sandbox.client.containers.calls[0][0][0] == "image-prefix"
    assert sandbox.client.containers.calls[0][1]["volumes"] is None
    assert candidate.calls == [], "forking a checkpoint must not replay prefix commands"

    sandbox.promote_candidate(handle)

    assert sandbox.container is candidate
    assert original.stopped and original.removed
    assert sandbox.candidate_containers == {}


def test_aborted_candidate_leaves_working_container_untouched():
    candidate = _Container("candidate")
    sandbox = _sandbox(candidate)
    original = sandbox.container
    handle = sandbox.create_candidate_container("txn-2", None)

    sandbox.abort_candidate(handle)

    assert sandbox.container is original
    assert not original.stopped and not original.removed
    assert candidate.stopped and candidate.removed
    assert sandbox.candidate_containers == {}
