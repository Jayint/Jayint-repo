import sys, pathlib, json
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder.types import CollectionResult
from src.manifest_builder.gate import accept
from src.manifest_builder import certificate as C


def _make():
    r = CollectionResult(exit_code=0, collected=("t.py::a", "t.py::b"),
                         skipped_modules=("tests/test_opt.py",), deselected=("t.py::slow",))
    v = accept(r, r, protected_ok=True)
    cert = C.build_certificate(
        v, r, r, repo_url="https://x/y", commit_sha="deadbeef", base_image="python:3.11-slim",
        base_image_digest="sha256:abc", collect_command="pytest --collect-only -q",
        source_tree_sha256="sha256:tree", protected_file_hashes={"conftest.py": "sha256:cf"},
        dockerfile_text="FROM python:3.11-slim\n", image_id="sha256:img",
        agent_meta={"runner": "grok build", "model": "grok-4.5"})
    return v, r, cert


def test_status_certified_and_completeness_records_skips_and_deselects():
    _, _, cert = _make()
    assert cert["status"] == "CERTIFIED"
    comp = cert["completeness"]
    assert comp["skipped_modules"] == ["tests/test_opt.py"] and comp["n_skipped_modules"] == 1
    assert comp["deselected"] == ["t.py::slow"] and comp["n_deselected"] == 1


def test_hashes_present_and_manifest_hash_matches():
    v, _, cert = _make()
    h = cert["hashes"]
    assert h["source_tree_sha256"] == "sha256:tree"
    assert h["dockerfile_sha256"].startswith("sha256:")
    assert cert["manifest_size"] == 2
    assert h["manifest_sha256"] == C._sha256_json(list(v.manifest))


def test_rejected_status_when_not_accepted():
    r = CollectionResult(exit_code=2, collected=("t.py::a",))
    v = accept(r, r, protected_ok=True)
    cert = C.build_certificate(v, r, r, repo_url="u", commit_sha="s", base_image="b",
        base_image_digest="d", collect_command="c", source_tree_sha256="t",
        protected_file_hashes={}, dockerfile_text="", image_id="i", agent_meta={})
    assert cert["status"] == "REJECTED" and cert["accepted"] is False and cert["reject_reasons"]


def test_certificate_is_deterministic():
    _, _, c1 = _make()
    _, _, c2 = _make()
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)


def test_write_artifacts_emits_all_files(tmp_path):
    v, r, cert = _make()
    C.write_artifacts(str(tmp_path), v, cert, r, r, build_log="hello")
    for name in ("collected-nodeids.json", "collection-certificate.json", "build.log",
                 "collect-run1.json", "collect-run2.json"):
        assert (tmp_path / name).exists()
    assert json.load(open(tmp_path / "collected-nodeids.json")) == ["t.py::a", "t.py::b"]
