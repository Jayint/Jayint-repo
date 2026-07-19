import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.eval.native_blindspot.oracle import load_oracle, load_triggers  # noqa: E402
from graph.model import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State, binary_id, syslib_id,
)
from src.eval.native_blindspot.oracle import RepoExpectation
from src.eval.native_blindspot.score import (
    aggregate, capability_key, extract_emitted_apt, score_repo,
)


def test_oracle_loads_and_covers_the_affected_set():
    oracle = load_oracle()
    assert oracle["mvt"].dlopen == ("libusb-1.0-0",)
    assert oracle["mvt"].part == "A"
    assert oracle["coderamp-labs-gitingest"].cli == ("git",)
    # residuals/out-of-scope are labeled honestly, not silently dropped
    assert oracle["karlicoss-promnesia"].in_scope is False
    assert oracle["microsoft-markitdown"].in_scope is False
    in_scope = [e for e in oracle.values() if e.in_scope]
    assert len(in_scope) >= 15   # the recall-bearing positive set


def test_triggers_present_for_dlopen_culprits():
    t = load_triggers()
    assert "python-magic" in t and "pyusb" in t


def _tool(name, apt, prov):
    return Node(id=binary_id(name), type=NodeType.TOOL, name=name, layer=Layer.TOOLCHAIN,
                discovered_by=DiscoveredBy.RESOLVER, state=State.UNKNOWN,
                fix_candidates=(f"apt:{apt}",), chosen_fix=f"apt:{apt}", provenance=prov)


def _syslib(soname, apt, prov):
    return Node(id=syslib_id(soname), type=NodeType.SYSTEM_LIB, name=soname, layer=Layer.SYSTEM,
                discovered_by=DiscoveredBy.STATIC_SCAN, state=State.UNKNOWN,
                fix_candidates=(f"apt:{apt}",), chosen_fix=f"apt:{apt}", provenance=prov)


def test_capability_key_collapses_dev_and_runtime_variants():
    assert capability_key("libmagic1") == capability_key("libmagic-dev")
    assert capability_key("libusb-1.0-0") == capability_key("libusb-1.0-0-dev")
    assert capability_key("git") == "git"


def test_extract_tags_provenance_and_type():
    g = DepGraph(nodes=(
        _tool("git", "git", "runtime-tool prior"),
        _syslib("libmagic.so", "libmagic1", "ctypes-scan (installed source)"),
    ))
    emitted = extract_emitted_apt(g)
    by_cap = {e.capability: e for e in emitted}
    assert by_cap[capability_key("git")].provenance == "runtime-tool prior"
    assert by_cap[capability_key("libmagic1")].node_type == "system_lib"


def test_score_repo_covered_and_missed_by_capability():
    exp = RepoExpectation("opencti", cli=("git",), dlopen=("libcairo2", "libpango-1.0-0"),
                          culprit="", in_scope=True, part="A+B")
    g = DepGraph(nodes=(
        _tool("git", "git", "runtime-tool prior"),
        _syslib("libcairo.so", "libcairo2", "ctypes-scan (installed source)"),
    ))
    s = score_repo("opencti", g, exp)
    assert capability_key("git") in s.covered
    assert capability_key("libcairo2") in s.covered
    assert capability_key("libpango-1.0-0") in s.missed   # honestly reported
    assert "runtime-tool prior" in s.by_provenance


def test_aggregate_splits_general_vs_curated_recall():
    exp_a = RepoExpectation("mvt", (), ("libusb-1.0-0",), "", True, "A")
    exp_b = RepoExpectation("gitingest", ("git",), (), "", True, "B")
    g_a = DepGraph(nodes=(_syslib("libusb-1.0.so", "libusb-1.0-0", "ctypes-scan (installed source)"),))
    g_b = DepGraph(nodes=(_tool("git", "git", "runtime-tool prior"),))
    report = aggregate([score_repo("mvt", g_a, exp_a), score_repo("gitingest", g_b, exp_b)])
    assert report["dlopen_recall"] == 1.0
    assert report["cli_recall"] == 1.0
    assert report["covered_by_general"] == 1   # ctypes-scan
    assert report["covered_by_curated"] == 1   # runtime-tool prior


def test_extract_scores_only_the_chosen_fix_not_all_candidates():
    # setup.sh installs only chosen_fix; a non-chosen candidate must NOT count.
    n = Node(id=binary_id("git"), type=NodeType.TOOL, name="git",
             layer=Layer.TOOLCHAIN, discovered_by=DiscoveredBy.RESOLVER,
             state=State.UNKNOWN, fix_candidates=("apt:oracle-hit", "apt:actual-choice"),
             chosen_fix="apt:actual-choice", provenance="runtime-tool prior")
    caps = {e.apt for e in extract_emitted_apt(DepGraph(nodes=(n,)))}
    assert caps == {"actual-choice"}      # NOT "oracle-hit"


def test_extract_ignores_node_with_no_apt_chosen_fix():
    n = Node(id=syslib_id("libx.so"), type=NodeType.SYSTEM_LIB, name="libx.so",
             layer=Layer.SYSTEM, discovered_by=DiscoveredBy.STATIC_SCAN,
             state=State.UNKNOWN, fix_candidates=(), chosen_fix=None,
             provenance="ctypes-scan (installed source)")
    assert extract_emitted_apt(DepGraph(nodes=(n,))) == []
