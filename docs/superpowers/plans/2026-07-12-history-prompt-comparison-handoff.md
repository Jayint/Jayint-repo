# Handoff: compare our per-turn agent prompt vs SWE-agent / mini-swe-agent

**Status:** ready to execute in a fresh session. Self-contained; assumes no prior conversation.
**Repo:** `/Users/john/john-v3-multi-lang` (branch `john-v3-multi-lang`).
**Type:** investigation only — do NOT modify production code. Write scratch render scripts + one comparison doc.

---

## Goal

Investigate and write a comparison of the **per-turn user prompt** our react repair agent receives each
step versus what **SWE-agent** and **mini-swe-agent** give their agents. Focus on the *user turn*: how
the conversation/history is structured, how observations are presented, and how old output is managed.
Deliver a doc with rendered examples side by side and a recommendation on whether/how to move ours
toward the SWE-agent shape.

## Established facts (don't re-derive; verify only if you doubt them)

- **The fundamental fork.** SWE-agent/mini-swe-agent keep a **growing message list** (system, task,
  then alternating `assistant[thought+action]` / `user[observation]` pairs). Our agent sends **exactly
  two messages every turn** — a stable system prompt + ONE re-rendered user blob — and the "history" in
  that blob is a *reconstructed third-person summary*, not the model's own prior messages.
- **Reset-each-turn.** Our agent edits a build script (`setup.sh`); every turn resets to a clean base
  image and re-runs the WHOLE script. Only the script text carries over. (SWE-agent edits a *persistent*
  repo that accumulates state.)
- **Our observations are now "honest".** The per-step `observe` is the REAL stdout/stderr
  (safety-compressed for build/test, full for explores), not a paraphrase. There is a
  `REACT_HISTORY=grouped|flat` lever — `flat` is a SWE-agent-style chronological view with no blocker
  grouping/ledger/STUCK. `flat` is the closest analog to SWE-agent; compare all three (ours-grouped,
  ours-flat, SWE-agent).

## Part A — map OUR per-turn prompt

Read:
- `src/react_repair/planner.py` — `build_system_prompt()` (the system prompt), and
  `ReactPlanner._render()` (~lines 140-158) which assembles the per-turn USER message from: numbered
  `CURRENT setup.sh` (+ `← BUILD HALTED HERE`), `LAST RUN OBSERVATION`, `render_history(...)`, optional
  `GRAPH CONTEXT`, and a closing turn-budget line. `plan()` (~line 160) shows it's system + ONE user
  message, native tool-calling (`explore`/`edit`).
- `src/react_repair/history_view.py` — `render_history()`, the history section. Levers:
  `REACT_HISTORY` (grouped|flat), `REACT_STUCK_MODE` (neutral|off|directive), `REACT_OBS_MODE`
  (histogram|raw). Grouped = `### BLOCKER n` cards + do-not-retry ledger + STUCK; flat = plain
  chronological `think → action → observe` cards.
- `src/react_repair/loop.py` — `_observation()` builds the `observation_raw`: `BUILD FAILED at cmd
  (line N)` header, or `BUILD OK. TESTS p/e` + the `[collect]/[run]` histogram (`summarize` /
  `format_breakdown`). Also `build_and_test()` and the loop (reset → run whole script → certify → one
  `pytest -q --continue-on-collection-errors`).
- `src/react_repair/observation.py` (`safety_compress_observation`) and `history.py`
  (`safety_truncate`, `Step`/`History`) — the two compressors.

**Render a real example** (pure/offline — `_render` needs no LLM or docker; `client=None` is fine):
```python
import sys
sys.path[:0] = ["/Users/john/john-v3-multi-lang", "/Users/john/john-v3-multi-lang/src"]
from src.react_repair.planner import build_system_prompt, ReactPlanner
from src.react_repair.history import History

ENV = "  base image : python:3.11-slim\n  repo dir : /app\n  test runner : pytest"
SCRIPT = "#!/usr/bin/env bash\nset -euo pipefail\napt-get update\npip install -r requirements.txt\n"
h = History()
h.record(0, "", "baseline → BUILD FAILED",
    "BUILD FAILED at `pip install -r requirements.txt` (line 4):\n"
    "  psycopg/psycopgmodule.c:36:10: fatal error: libpq-fe.h: No such file or directory\n"
    "  ERROR: Failed building wheel for psycopg2\n")
h.record(1, "install the -dev headers, not the runtime lib",
    "edit v1 (insert@3 +apt-get install -y libpq-dev) → 41/50",
    "BUILD OK. TESTS 41/50 passed.\nTop failure causes (by tests affected):\n"
    "  6 × [run] redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused.\n"
    "--- pytest output (tail) ---\nE   redis.exceptions.ConnectionError: ...\n")
p = ReactPlanner(client=None, model="(sample)", graph_context=None, env_info=ENV)
print(build_system_prompt(ENV)); print("=" * 90)
print(p._render(h, SCRIPT, "<LAST RUN OBSERVATION goes here>", graph=None, turn=3, max_turns=25))
```
Run it once with `REACT_HISTORY=grouped` and once with `REACT_HISTORY=flat` (set the env var before
running) so you have both of ours to compare.

## Part B — map SWE-agent / mini-swe-agent's per-turn prompt

Clone fresh into a scratch dir (the previous session's clones do not persist):
```bash
git clone --depth 1 https://github.com/SWE-agent/mini-swe-agent
git clone --depth 1 https://github.com/SWE-agent/SWE-agent
```

**mini-swe-agent** — read:
- `src/minisweagent/agents/default.py` — the whole loop (~188 lines): `self.messages` is a growing
  list; `run()` seeds `system` + `instance`; `query()` sends the ENTIRE `self.messages`;
  `execute_actions()` appends the observation. No summarization, no history processing.
- `src/minisweagent/config/default.yaml` — `system_template`, `instance_template`, and
  `observation_template` (`<returncode>` + `<output>`; if output > 10k chars it is DISCARDED and
  replaced with a "run a narrower command" `<warning>` — NOT truncated-and-kept).

**SWE-agent** — read:
- `sweagent/agent/history_processors.py` — how it manages old output: `DefaultHistoryProcessor`
  (identity — the current default), `LastNObservations` (keep the last N observations verbatim, elide
  older ones to `"Old environment output: (N lines omitted)"` while KEEPING the assistant's reasoning),
  `ClosedWindowHistoryProcessor` (collapse stale file windows to the latest one), `TagToolCallObservations`.
- `sweagent/agent/agents.py` — where the message list is built and the observation/next-step template
  is applied. Grep the `config/*.yaml` for `observation`, `<output>`, `open`, `WINDOW` to find the
  actual per-turn template + the ACI file-viewer window that's re-rendered each turn.

## Part C — the comparison (table + prose, with a rendered snippet per side)

Compare along these dimensions:
1. **Message structure** — growing message list (SWE/mini) vs single re-rendered user blob (ours).
2. **Whose words** — does the model see its own prior thoughts/actions verbatim, or a narrator's
   summary? (Note: ours now threads `Step.thought` into the cards — check how faithfully.)
3. **Observation format** — raw `<returncode>/<output>` vs our `BUILD FAILED at… (line N)` /
   `[collect]/[run]` histogram / safety-compressed body.
4. **Old-output management** — SWE-agent's `LastNObservations` elision + `ClosedWindow` vs our recency
   window + dedup + (grouped) blocker grouping.
5. **Structure imposed** — none (flat transcript) vs our blocker grouping / do-not-retry ledger / STUCK
   (grouped mode). Compare ours-`flat` directly against SWE-agent here.
6. **Big-output handling** — mini discards >10k; SWE-agent elides to "N lines omitted"; ours
   safety-compresses (keeps errors/status, drops download noise) + hard-caps.
7. **Reproducibility framing** — our per-turn numbered `CURRENT setup.sh` + reset-each-turn vs their
   persistent-repo stateful file viewer.

## Deliverable

A markdown doc under `docs/superpowers/` containing: side-by-side rendered examples (ours-grouped,
ours-flat, mini, SWE-agent), the comparison table, and a **recommendation** — should our per-turn
prompt move toward the SWE-agent shape? Is `REACT_HISTORY=flat` enough, or would we need the actual
message-list structure (assistant/user turns) to match? What, concretely, would we adopt or reject, and
why (keep the reset-each-turn architecture and the ablation-cleanliness need in mind).

## Constraints

- Investigation only — do NOT modify `src/`. Scratch render scripts + the comparison doc only.
- Use the existing levers (`REACT_HISTORY`, `REACT_STUCK_MODE`, `REACT_OBS_MODE`) to render variants;
  do not add new ones.
- Ground every claim in `file:line`. Quote real prompt/template text, don't paraphrase it.
