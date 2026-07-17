# tests/depgraph/test_arbitrate.py
from dataclasses import dataclass
from python_deps.depgraph.arbitrate import arbitrate
from python_deps.depgraph.cure import CureResult


@dataclass
class _FakeResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    @property
    def ok(self): return self.returncode == 0


class _FakeExec:
    def __init__(self, table): self.table = table
    def run(self, cmd, *, timeout=300):
        for key, rc, err in self.table:
            if key in cmd:
                return _FakeResult(rc, stderr=err)
        return _FakeResult(1, stderr="ModuleNotFoundError: No module named 'zzz'")


def _plan(tmp_path):
    from python_deps.depgraph.invocation_resolver import resolve
    return resolve(str(tmp_path))


def test_cure_failure_leaves_all_deferred_unresolved(tmp_path):
    arb = arbitrate(_FakeExec([]), _plan(tmp_path), CureResult(False, "failed", False, ""),
                    frozenset({"items", "azure"}))
    assert arb.unresolved == frozenset({"items", "azure"})
    assert not arb.fallthrough and not arb.resolves_local


def test_exception_aware_verdict(tmp_path):
    ex = _FakeExec([
        ("import items", 0, ""),                                           # clean → local
        ("import azure", 1, "ModuleNotFoundError: No module named 'azure'"),# name error → fallthrough
        ("import broke", 1, "ImportError: cannot import name 'x'"),         # other error → broken_local
    ])
    arb = arbitrate(ex, _plan(tmp_path), CureResult(True, "isolated", True, ""),
                    frozenset({"items", "azure", "broke"}))
    assert "items" in arb.resolves_local
    assert "azure" in arb.fallthrough
    assert "broke" in arb.resolves_local          # present-but-broken is LOCAL, never fallthrough


def test_name_mismatch_module_not_found_is_not_a_fallthrough(tmp_path):
    # pandas IS present locally, but its own transitive import (numpy) is missing —
    # the classic "local module present, its dependency absent" case. The
    # ModuleNotFoundError names a DIFFERENT module than the one probed, so the
    # top-level-name discriminator must treat pandas as present-but-broken (local),
    # never a fallthrough. A regression that broadened the probe to "any
    # ModuleNotFoundError -> fallthrough" would install pandas from PyPI over the
    # broken local module — the false-green this guards against.
    ex = _FakeExec([
        ("import pandas", 1, "ModuleNotFoundError: No module named 'numpy'"),
    ])
    arb = arbitrate(ex, _plan(tmp_path), CureResult(True, "isolated", True, ""),
                    frozenset({"pandas"}))
    assert "pandas" not in arb.fallthrough        # different module missing → NOT external
    assert "pandas" in arb.resolves_local         # treated as present-but-broken/local
