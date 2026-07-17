# tests/depgraph/test_shadow.py
from graph.shadow import run_shadow_config_lane, ShadowRecord


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


def test_shadow_emits_record_without_mutating_graph(tmp_path):
    (tmp_path / "mypkg").mkdir(); (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "a.py").write_text("import requests\n")
    from graph.scan import scan_to_nodes
    graph = scan_to_nodes(str(tmp_path))
    before = {n.id for n in graph.nodes}
    rec = run_shadow_config_lane(graph, str(tmp_path), _StubExec(), declared=frozenset())
    assert isinstance(rec, ShadowRecord)
    assert {n.id for n in graph.nodes} == before          # graph UNCHANGED (immutability + discard)
    assert rec.n_external >= 1
