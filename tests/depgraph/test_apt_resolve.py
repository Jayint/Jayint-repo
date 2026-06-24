"""Unit tests for dynamic soname->apt resolution (no Docker/network)."""

from __future__ import annotations

import collections

from python_deps.depgraph.apt_resolve import (
    ensure_apt_file,
    multiarch_triplet,
    parse_apt_file_search,
    resolve_soname_apt,
)
from python_deps.depgraph.executor import CommandResult


# ---------------------------------------------------------------------------
# QueuedFakeExecutor — state-transition variant of FakeExecutor.
# ---------------------------------------------------------------------------

class QueuedFakeExecutor:
    """FakeExecutor variant where each key maps to a FIFO of results.

    Enables state-transition tests (e.g. command -v apt-file: absent then present).
    Longest matching key wins (mirrors FakeExecutor). Falls back to rc=127 when
    the queue for a matching key is exhausted or no key matches.
    """

    def __init__(self, queues: dict[str, list[CommandResult]]) -> None:
        self.queues: dict[str, collections.deque[CommandResult]] = {
            k: collections.deque(v) for k, v in queues.items()
        }
        self.calls: list[str] = []
        self.timeouts: list[int] = []

    def run(self, command: str, *, timeout: int = 300) -> CommandResult:
        self.calls.append(command)
        self.timeouts.append(timeout)
        matches = [k for k in self.queues if k in command and self.queues[k]]
        if matches:
            best = max(matches, key=len)
            return self.queues[best].popleft()
        return CommandResult(command=command, returncode=127, stdout="", stderr="no fake response")


# ---------------------------------------------------------------------------
# parse_apt_file_search tests (unchanged)
# ---------------------------------------------------------------------------

def test_parse_filters_to_exact_multiarch_path():
    stdout = (
        "libgl1: /usr/lib/x86_64-linux-gnu/libGL.so.1\n"
        "primus-libs: /usr/lib/primus/libGL.so.1\n"
        "libgl1-mesa-dev: /usr/lib/x86_64-linux-gnu/libGL.so\n"
    )
    assert parse_apt_file_search(stdout, "libGL.so.1", "x86_64-linux-gnu") == "libgl1"


def test_parse_rejects_cross_compile_and_picks_runtime_over_dev():
    stdout = (
        "libgomp1: /usr/lib/x86_64-linux-gnu/libgomp.so.1\n"
        "libgomp1-amd64-cross: /usr/x86_64-linux-gnu/lib/libgomp.so.1\n"
        "libgomp1-dev: /usr/lib/x86_64-linux-gnu/libgomp.so.1\n"
    )
    assert parse_apt_file_search(stdout, "libgomp.so.1", "x86_64-linux-gnu") == "libgomp1"


def test_parse_no_triplet_accepts_single_multiarch_dir():
    stdout = "libpq5: /usr/lib/aarch64-linux-gnu/libpq.so.5\n"
    assert parse_apt_file_search(stdout, "libpq.so.5", None) == "libpq5"


def test_parse_returns_none_when_no_match():
    assert parse_apt_file_search("", "libGL.so.1", "x86_64-linux-gnu") is None


# ---------------------------------------------------------------------------
# ensure_apt_file tests
# ---------------------------------------------------------------------------


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(command="", returncode=0, stdout=stdout, stderr="")


def _fail() -> CommandResult:
    return CommandResult(command="", returncode=1, stdout="", stderr="fail")


def test_ensure_apt_file_already_present_returns_true_no_install():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [_ok()],
    })
    assert ensure_apt_file(ex) is True
    # Only the readiness check was issued — no apt-get call at all.
    assert not any("apt-get" in c for c in ex.calls)


def test_ensure_apt_file_absent_installs_and_returns_true():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [CommandResult(command="", returncode=127, stdout="", stderr="")],
        "apt-get update":      [_ok()],
        "apt-get install -y apt-file": [_ok()],
        "apt-file update":     [_ok()],
    })
    assert ensure_apt_file(ex) is True
    # All four commands issued in order.
    assert ex.calls == [
        "command -v apt-file",
        "apt-get update",
        "apt-get install -y apt-file",
        "apt-file update",
    ]
    # apt-file update must use exactly _APT_FILE_UPDATE_TIMEOUT (180 s).
    apt_file_update_idx = ex.calls.index("apt-file update")
    assert ex.timeouts[apt_file_update_idx] == 180


def test_ensure_apt_file_update_fails_returns_false():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [CommandResult(command="", returncode=127, stdout="", stderr="")],
        "apt-get update":      [_fail()],
    })
    assert ensure_apt_file(ex) is False
    # Short-circuits: no apt-get install issued.
    assert not any("apt-get install" in c for c in ex.calls)


def test_ensure_apt_file_install_fails_returns_false():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [CommandResult(command="", returncode=127, stdout="", stderr="")],
        "apt-get update":      [_ok()],
        "apt-get install -y apt-file": [_fail()],
    })
    assert ensure_apt_file(ex) is False
    # Short-circuits: no apt-file update issued.
    assert not any("apt-file update" in c for c in ex.calls)


def test_ensure_apt_file_index_fails_returns_false():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [CommandResult(command="", returncode=127, stdout="", stderr="")],
        "apt-get update":      [_ok()],
        "apt-get install -y apt-file": [_ok()],
        "apt-file update":     [_fail()],
    })
    assert ensure_apt_file(ex) is False


# ---------------------------------------------------------------------------
# resolve_soname_apt tests
# ---------------------------------------------------------------------------

def test_resolve_known_soname_uses_table_without_executor(fake_executor):
    # libGL.so.1 is in the curated table -> resolve must NOT touch the executor.
    pkg, source = resolve_soname_apt("libGL.so.1", fake_executor)
    assert (pkg, source) == ("libgl1", "table")
    assert fake_executor.calls == []


def test_resolve_unknown_soname_falls_back_to_apt_file(fake_executor, make_result_fixture):
    # apt-file already installed (rc=0) -> skips install, proceeds to search.
    fake_executor.responses = {
        "command -v apt-file": make_result_fixture(returncode=0),
        "sysconfig": make_result_fixture(stdout="x86_64-linux-gnu\n"),
        "apt-file search": make_result_fixture(
            stdout="libfoo7: /usr/lib/x86_64-linux-gnu/libfoo.so.7\n"
        ),
    }
    pkg, source = resolve_soname_apt("libfoo.so.7", fake_executor)
    assert (pkg, source) == ("libfoo7", "apt-file")


def test_resolve_unknown_soname_unresolved_when_apt_file_missing(fake_executor):
    # Empty FakeExecutor -> command -v apt-file rc=127 -> apt-get update rc=127
    # -> ensure_apt_file returns False -> unresolved.
    pkg, source = resolve_soname_apt("libbar.so.9", fake_executor)
    assert pkg is None
    assert source == "unresolved"


def test_multiarch_triplet_none_when_probe_fails(fake_executor):
    assert multiarch_triplet(fake_executor) is None


def test_resolve_unknown_soname_lazy_install_resolves():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [CommandResult(command="", returncode=127, stdout="", stderr="")],
        "apt-get update":      [_ok()],
        "apt-get install -y apt-file": [_ok()],
        "apt-file update":     [_ok()],
        "sysconfig":           [_ok("x86_64-linux-gnu")],
        "apt-file search":     [_ok("libfoo7: /usr/lib/x86_64-linux-gnu/libfoo.so.7\n")],
    })
    pkg, source = resolve_soname_apt("libfoo.so.7", ex)
    assert (pkg, source) == ("libfoo7", "apt-file")


def test_resolve_unknown_soname_install_failure_returns_unresolved():
    ex = QueuedFakeExecutor({
        "command -v apt-file": [CommandResult(command="", returncode=127, stdout="", stderr="")],
        "apt-get update":      [_fail()],
    })
    pkg, source = resolve_soname_apt("libfoo.so.7", ex)
    assert (pkg, source) == (None, "unresolved")


def test_resolve_table_hit_pays_zero_cost_no_apt_file_check():
    # Table hit for libGL.so.1 must not invoke ensure_apt_file at all.
    ex = QueuedFakeExecutor({})
    pkg, source = resolve_soname_apt("libGL.so.1", ex)
    assert (pkg, source) == ("libgl1", "table")
    assert ex.calls == []


def test_resolve_second_unknown_soname_skips_install():
    """After apt-file is installed for the first unknown soname, the second
    soname must not re-issue the apt-get install sequence."""
    ex = QueuedFakeExecutor({
        # First call: absent; second call: present (installed by then).
        "command -v apt-file": [
            CommandResult(command="", returncode=127, stdout="", stderr=""),
            _ok(),
        ],
        "apt-get update":      [_ok()],
        "apt-get install -y apt-file": [_ok()],
        "apt-file update":     [_ok()],
        # Two sysconfig probes, one per resolve_soname_apt call.
        "sysconfig": [_ok("x86_64-linux-gnu"), _ok("x86_64-linux-gnu")],
        # Two apt-file search calls.
        "apt-file search libfoo.so.7": [_ok("libfoo7: /usr/lib/x86_64-linux-gnu/libfoo.so.7\n")],
        "apt-file search libbar.so.3": [_ok("libbar3: /usr/lib/x86_64-linux-gnu/libbar.so.3\n")],
    })

    pkg1, src1 = resolve_soname_apt("libfoo.so.7", ex)
    pkg2, src2 = resolve_soname_apt("libbar.so.3", ex)

    assert (pkg1, src1) == ("libfoo7", "apt-file")
    assert (pkg2, src2) == ("libbar3", "apt-file")

    apt_get_calls = [c for c in ex.calls if "apt-get" in c]
    # update + install = 2, not 4 (not repeated for second soname).
    assert len(apt_get_calls) == 2
