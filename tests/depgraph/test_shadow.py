# tests/depgraph/test_shadow.py
from graph.python.shadow import run_shadow_config_lane, ShadowRecord


class _StubExec:
    repo_mount_dir = "/workspace/repo"
    def run(self, cmd, *, timeout=300):
        from dataclasses import dataclass
        @dataclass
        class R:
            returncode: int = 0; stdout: str = "[]"; stderr: str = ""
            @property
            def ok(self): return self.returncode == 0
        return R()


class _ProbeNotOk:
    """Fails EVERY command (so the stdlib probe returns None -> unavailable)."""
    repo_mount_dir = "/workspace/repo"
    def run(self, cmd, *, timeout=300):
        from dataclasses import dataclass
        @dataclass
        class R:
            returncode: int = 1; stdout: str = ""; stderr: str = "boom"
            @property
            def ok(self): return False
        return R()


def test_shadow_emits_record_without_mutating_graph(tmp_path):
    (tmp_path / "mypkg").mkdir(); (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "a.py").write_text("import requests\n")
    from graph.python.read.scan import scan_to_nodes
    graph = scan_to_nodes(str(tmp_path))
    before = {n.id for n in graph.nodes}
    rec = run_shadow_config_lane(graph, str(tmp_path), _StubExec(), declared=frozenset())
    assert isinstance(rec, ShadowRecord)
    assert {n.id for n in graph.nodes} == before          # graph UNCHANGED (immutability + discard)
    assert rec.n_external >= 1
    assert rec.probe_unavailable is False                  # the stub answered the probe


def test_shadow_short_circuits_on_unavailable_probe(tmp_path):
    """When the TARGET stdlib probe is UNAVAILABLE (None), the shadow pass must NOT
    classify against an empty set — it short-circuits with a ``probe_unavailable``
    record whose zeroed partitions the Gate B aggregator excludes (garbage-in guard)."""
    (tmp_path / "mypkg").mkdir(); (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "a.py").write_text("import requests\nimport items\n")
    from graph.python.read.scan import scan_to_nodes
    graph = scan_to_nodes(str(tmp_path))
    rec = run_shadow_config_lane(graph, str(tmp_path), _ProbeNotOk(), declared=frozenset())
    assert rec.probe_unavailable is True
    assert (rec.n_internal, rec.n_external, rec.n_deferred) == (0, 0, 0)  # not classified
