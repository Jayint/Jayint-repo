import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.history import Step
from src.react_repair.history_view import extract_blocker, render_history


# ───────────────────────── extract_blocker (structured signature) ─────────────────────────

def test_extract_blocker_build_failure_uses_command_and_fatal_error():
    obs = ("BUILD FAILED at `pip install lxml`:\nCollecting lxml\n"
           "src/lxml/etree.c:96: fatal error: libxml/xmlversion.h: No such file or directory\n")
    sig = extract_blocker(obs)
    assert "pip install lxml" in sig and "libxml/xmlversion.h" in sig

def test_extract_blocker_test_failure_uses_missing_module():
    obs = "BUILD OK. TESTS 1/2 passed:\n.F\nModuleNotFoundError: No module named 'toml'\n"
    assert extract_blocker(obs) == "tests: No module named 'toml'"

def test_extract_blocker_all_executed_pass_is_none():
    assert extract_blocker("BUILD OK. TESTS 6/6 passed:\n6 passed") is None

def test_extract_blocker_nonbuild_output_is_none():
    assert extract_blocker("probe-output\nsome dir listing") is None

def test_extract_blocker_distinguishes_six_from_toml():
    a = extract_blocker("BUILD OK. TESTS 1/2 passed:\nNo module named 'six'")
    b = extract_blocker("BUILD OK. TESTS 1/2 passed:\nNo module named 'toml'")
    assert a != b


# ───────────────────────── render_history (grouped, chronological) ─────────────────────────

def _base(score, obs): return Step(0, "", f"baseline → {score}", obs, obs)
def _patch(v, change, score, obs):
    summ = f"patch v{v} ({change}) → {score}" if change else f"patch v{v} → {score}"
    return Step(v, "", summ, obs, obs)
def _explore(cmd, out): return Step(99, "", f"explore: {cmd}", out, out)

_SIX  = "BUILD OK. TESTS 1/2 passed:\n.F\nModuleNotFoundError: No module named 'six'"
_TOML = "BUILD OK. TESTS 1/2 passed:\n.F\nModuleNotFoundError: No module named 'toml'"
_PASS = "BUILD OK. TESTS 2/2 passed:\n2 passed"
_LXML_HDR = "BUILD FAILED at `pip install lxml`:\nfatal error: libxml/xmlversion.h: No such file"


def test_render_groups_hidden_import_chain_with_headers_at_transitions():
    steps = [_base("1/2", _SIX),
             _patch(1, "+pip install six", "1/2", _TOML),
             _patch(2, "+pip install toml", "2/2", _PASS)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 2                       # two distinct blockers
    assert "No module named 'six'" in out and "No module named 'toml'" in out
    assert out.index("v1 ") < out.index("toml")             # chronological: fix precedes revealed blocker
    assert "CLEARED" not in out                             # no causal overclaim
    assert "no longer present" in out.lower()               # hedged language instead

def test_render_stubborn_blocker_lists_do_not_retry_on_open_block():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _patch(1, "+pip install lxml2", "BUILD FAILED", _LXML_HDR),
             _patch(2, "+apt-get install libxml", "BUILD FAILED", _LXML_HDR)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1                       # same blocker throughout → one block
    assert "already tried" in out.lower()
    assert "+pip install lxml2" in out and "+apt-get install libxml" in out

def test_render_resolved_run_has_no_do_not_retry_line():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _patch(1, "+apt-get install -y libxml2-dev", "6/6", _PASS)]
    out = render_history(steps)
    assert "already tried" not in out.lower()               # resolved → no dangling do-not-retry

def test_render_uncertain_transition_does_not_split():
    # specific module blocker -> a build failure with only a command (weak) signature: don't split.
    weak = "BUILD FAILED at `bash setup.sh`:\nunexpected end of file near line 12"
    steps = [_base("1/3", _SIX),
             _patch(1, "+pip install six", "0/3", weak)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1                       # conservative: no confident split
    assert "uncertain" in out.lower()

def test_render_explore_nests_without_changing_blocker():
    steps = [_base("BUILD FAILED", _LXML_HDR),
             _explore("apt-cache search libxml2", "libxml2-dev - ..."),
             _patch(1, "+apt-get install -y libxml2-dev", "6/6", _PASS)]
    out = render_history(steps)
    assert out.count("BLOCKER:") == 1
    assert "explore: apt-cache search libxml2" in out

def test_render_empty_is_safe():
    assert isinstance(render_history([]), str)
