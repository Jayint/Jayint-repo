import sys, pathlib
import pytest
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.history import History
from src.agent.message_view import build_messages

SCRIPT = "1| set -e\n2| pip install -r requirements.txt\n3| pytest -q"
CLOSING = "Turn 3/10 (7 left). Reason briefly, then call one tool — explore or edit."


@pytest.fixture(autouse=True)
def _agentic(monkeypatch):
    monkeypatch.setenv("REACT_MSG_STYLE", "agentic")


def _build(h, **kw):
    return build_messages(h.steps, system_prompt="SYS", numbered_script=SCRIPT,
                          closing_line=CLOSING, **kw)


def _hist():
    h = History()
    h.record(0, "", "baseline → BUILD FAILED",
             "BUILD FAILED at `pip install psycopg2` (line 2):\nfatal error: libpq-fe.h: No such file\n",
             outcome={"build_ok": False, "failing_command": "pip install psycopg2", "lineno": 2,
                      "ran_tests": False})
    h.record(1, "I need the postgres headers.",
             "edit v1 (insert@2 +apt-get install -y libpq-dev) → 0/5",
             "BUILD OK. TESTS 0/5 passed.\nE   ModuleNotFoundError: No module named 'app'\n",
             action={"kind": "edit", "verb": "insert", "start": 2, "end": 2,
                     "content": "apt-get install -y libpq-dev"},
             outcome={"build_ok": True, "ran_tests": True, "test_command": "python -m pytest -q",
                      "passed": 0, "failed": 0, "errors": 5, "skipped": 0, "collected": 0})
    return h


# --- shape: command → result, with the model's own calls as assistant turns -----------------
def test_every_observation_is_preceded_by_its_command():
    msgs = _build(_hist())
    for m in msgs:
        if m["role"] == "user":
            assert m["content"].lstrip().startswith(("$ ", "setup.sh updated", "⚠")), m["content"][:60]

def test_assistant_turn_is_the_real_call_not_a_narrator_paraphrase():
    msgs = _build(_hist())
    a = [m for m in msgs if m["role"] == "assistant"][0]
    assert 'edit(verb="insert", start=2, end=2, content="apt-get install -y libpq-dev")' in a["content"]
    assert "→ edit(" not in a["content"]                     # the old arrow-prose form is gone
    assert "I need the postgres headers." in a["content"]    # the model's own words survive

def test_explore_call_is_rendered_with_kwargs():
    h = History()
    h.record(0, "", "explore: cat pyproject.toml", "[project]\nname='x'\n",
             action={"kind": "explore", "command": "cat pyproject.toml"})
    msgs = _build(h)
    assert 'explore(command="cat pyproject.toml")' in msgs[1]["content"]
    assert msgs[2]["content"].startswith("$ cat pyproject.toml\n")   # the command, then its stdout

def test_explore_output_stays_full():
    h = History()
    h.record(0, "", "explore: cat setup.cfg", "line1\nline2\nline3\n",
             action={"kind": "explore", "command": "cat setup.cfg"})
    body = _build(h)[2]["content"]
    assert "line1" in body and "line2" in body and "line3" in body


# --- the fake ratio is gone ------------------------------------------------------------------
def test_the_synthesized_verdict_header_never_reaches_the_model():
    msgs = _build(_hist())
    joined = "\n".join(m["content"] for m in msgs)
    assert "BUILD OK. TESTS" not in joined
    assert "BUILD FAILED at" not in joined
    assert "0/5" not in joined
    assert "0 passed, 0 failed, 5 collection errors — no tests ran" in joined

def test_an_edit_gets_a_real_tool_result():
    msgs = _build(_hist())
    last = msgs[-1]["content"]
    assert last.startswith("setup.sh updated:\n  2| apt-get install -y libpq-dev")


# --- the workbench is fenced as harness state, not stirred into the tool output --------------
def test_harness_state_is_fenced_and_last():
    msgs = _build(_hist())
    last = msgs[-1]["content"]
    assert "harness state" in last
    assert last.index("harness state") > last.index("$ bash setup.sh")   # data first, instruction last
    assert SCRIPT in last and CLOSING in last

def test_graph_context_rides_inside_the_fence():
    msgs = _build(_hist(), graph_context_text="Runtime: python3.11 CERTIFIED")
    assert "GRAPH CONTEXT (certified state):\nRuntime: python3.11 CERTIFIED" in msgs[-1]["content"]


# --- a refused call lands in ITS OWN result slot, not a footer -------------------------------
def test_rejection_is_the_result_of_the_rejected_call():
    rejected = {"thought": "I'll just install it from PyPI.",
                "action": {"kind": "edit", "verb": "insert", "start": 3, "end": 3,
                           "content": "pip install myproj"},
                "reason": "installs the project under test from a package index"}
    msgs = _build(_hist(), rejected=rejected)
    assert msgs[-2]["role"] == "assistant"
    assert 'content="pip install myproj"' in msgs[-2]["content"]      # the call that was refused …
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"].startswith("⚠ REJECTED by the host — installs the project under test")
    assert "That call did not run." in msgs[-1]["content"]            # … and its result, adjacent
    assert "harness state" in msgs[-1]["content"]                     # scaffold follows the refusal

def test_no_rejection_footer_when_nothing_was_rejected():
    assert "REJECTED" not in "\n".join(m["content"] for m in _build(_hist()))


# --- the classic style is untouched (it is the A/B's control arm) -----------------------------
def test_classic_remains_the_default_and_is_unchanged(monkeypatch):
    monkeypatch.delenv("REACT_MSG_STYLE", raising=False)
    msgs = _build(_hist())
    joined = "\n".join(m["content"] for m in msgs)
    assert "BUILD OK. TESTS 0/5 passed." in joined      # the classic verdict header still ships
    assert "── LAST RUN (full" in joined
    assert "harness state" not in joined

def test_lever_is_read_per_call(monkeypatch):
    monkeypatch.setenv("REACT_MSG_STYLE", "classic")
    assert "harness state" not in "\n".join(m["content"] for m in _build(_hist()))
    monkeypatch.setenv("REACT_MSG_STYLE", "agentic")
    assert "harness state" in "\n".join(m["content"] for m in _build(_hist()))


# --- the recency gradient and the ledger still work in the new shape --------------------------
def test_old_observations_still_elide(monkeypatch):
    h = _hist()
    for i in range(2, 8):
        h.record(i, "", f"edit v{i} (insert@2 +x{i}) → 0/5", "BUILD OK. TESTS 0/5 passed.\nsame\n",
                 action={"kind": "edit", "verb": "insert", "start": 2, "end": 2, "content": f"x{i}"},
                 outcome={"build_ok": True, "ran_tests": True, "passed": 0, "failed": 0, "errors": 5})
    joined = "\n".join(m["content"] for m in _build(h))
    assert "Old run output:" in joined

def test_do_not_retry_ledger_survives():
    h = History()
    h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `pip install x` (line 2):\nboom\n",
             outcome={"build_ok": False, "failing_command": "pip install x", "lineno": 2})
    for i, c in enumerate(("apt-get install -y a", "apt-get install -y b"), start=1):
        h.record(i, "", f"edit v{i} (insert@2 +{c}) → BUILD FAILED",
                 "BUILD FAILED at `pip install x` (line 2):\nboom\n",
                 action={"kind": "edit", "verb": "insert", "start": 2, "end": 2, "content": c},
                 outcome={"build_ok": False, "failing_command": "pip install x", "lineno": 2})
    assert "already tried against this failing state" in _build(h)[-1]["content"]
