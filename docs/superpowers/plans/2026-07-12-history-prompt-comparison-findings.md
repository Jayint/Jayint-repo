# Findings: our per-turn prompt vs SWE-agent / mini-swe-agent — with focus on the IMMEDIATE turn's output

**Date:** 2026-07-12 · investigation only, no `src/` changes.
**Repos read:** ours `/Users/john/john-v3-multi-lang` · `mini-swe-agent@e187bcb` (v2.4.5) · `SWE-agent@1132b3e`, both cloned to `scratchpad/ext/`.

---

## TL;DR — does SWE-agent / mini even keep the immediate turn's output? Yes. All three keep it.

The three agents differ not on *whether* the latest observation survives (it always does) but on *where it lives* and *how much special-casing it takes*:

| | where the IMMEDIATE observation lives | special treatment | cap on it |
|---|---|---|---|
| **ours** | a dedicated `LAST RUN OBSERVATION:` slot at the **top** of the user blob (`planner.py:146`), fed the current build's `_observation()` (`loop.py:218`) | its duplicate in HISTORY is **withheld** → `observe: (current run — full output above under LAST RUN OBSERVATION)` (`history_view.py:293-294`) | `_OBS_MAX_CHARS=8000`, keep-both-ends (`loop.py:44`, `history.py:19-29`) |
| **mini** | just `self.messages[-1]` — the last user message, appended after the action (`default.py:152-155`) | **none** — identical to every other observation | 10 000-char threshold → head 5000 + tail 5000 + `<warning>` (`config/default.yaml:114-141`) |
| **SWE-agent** | the last `message_type:"observation"` item (`agents.py:702-712`) | **none at read time** — `LastNObservations` excludes the last N from elision by construction (`history_processors.py:153-155`) | 100 000-char cap → `observation[:max]` + `<response clipped>` (`agents.py:69-74,79`) |

**The one real asymmetry:** in our design the current turn's output appears **twice structurally** — once at the top (`LAST RUN OBSERVATION`) and once as the current-turn history card — so we spend code *de-duplicating* it (the withhold at `history_view.py:293-294`, plus byte-identical dedup at `:245-246`). In both reference agents it appears **once**, naturally at the recency end of the message list, no dedup needed. That double-representation is a symptom of our narrator-summary architecture, not a feature. More on that in the recommendation.

**Two corrections to earlier priors** (verified in source this run):
1. mini v2.4.5 **truncate-and-keeps** big output (head 5000 + tail 5000 over a 10k threshold), it does **not** discard-and-warn (`config/default.yaml:114-141`).
2. SWE-agent's *current* `config/default.yaml` default is `cache_control` = **keep everything** (`config/default.yaml:67-69`); the code-level default processor is the identity `DefaultHistoryProcessor` (`history_processors.py:74-82`). The `LastNObservations n=5` elision is the **legacy paper** (`sweagent_0_7/*`) config (`config/sweagent_0_7/07.yaml:100-101`), not the shipped default.

---

## The fundamental fork (established, verified)

- **They keep a growing message list.** mini: one flat append-only `self.messages` (`default.py:42,69-72`); `query()` sends the ENTIRE list every step (`default.py:147`). SWE-agent: append-only raw `self.history` (`agents.py:556-559`) + a `messages` **property** that re-runs the history processors on every query (`agents.py:539-551`). The model reads its **own prior assistant messages verbatim**.
- **We send two messages per turn.** `plan()` = `[system, user]` (`planner.py:158-165`). The system prompt is stable; the single user blob is re-rendered each turn from `CURRENT setup.sh` (numbered) + `LAST RUN OBSERVATION` + `render_history(...)` + optional `GRAPH CONTEXT` + a closing turn-budget line (`planner.py:138-156`). The "history" is a **third-person reconstruction**, not the model's own messages — even in `flat` mode.
- **Reset-each-turn.** We edit `setup.sh`; every turn resets to a clean base and re-runs the WHOLE script (`loop.py:170-187`). So our "immediate observation" is *the whole script re-run*, which under `set -e` surfaces the next latent failure — it is not the causal result of just the last edit. SWE-agent/mini edit a **persistent** repo, so message N's observation IS the direct result of message N's action.

---

## Rendered side-by-side

### Ours — `REACT_HISTORY=grouped` (default), the current turn
```
CURRENT setup.sh (line numbers are for Edit refs and match the build failure's "line N" …):
1| #!/usr/bin/env bash
2| set -euo pipefail
3| apt-get update
4| apt-get install -y libpq-dev
5| pip install --upgrade pip
6| pip install -e .
7| apt-get install -y redis-server
8| redis-server --daemonize yes

LAST RUN OBSERVATION:
BUILD OK. TESTS 41/50 passed.
Top failure causes (by tests affected):
  6 × [run] redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused.
--- pytest output (tail) ---
tests/test_cache.py::test_set_and_get FAILED
E   redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused.
=================== 9 failed, 41 passed in 18.10s ===================

HISTORY — chronological; grouped by BLOCKER … Each attempt is a card: think → action → observe.
### BLOCKER 1 — pip install -e . → fatal error: libpq-fe.h: No such file or directory   (baseline: BUILD FAILED)
    think:   which apt package actually ships libpq-fe.h?
- explored `apt-file search libpq-fe.h` →
        libpq-dev: /usr/include/postgresql/libpq-fe.h
- v1 · insert@3 +apt-get install -y libpq-dev
    think:   install the -dev headers, not the runtime lib
    observe:
      Top failure causes (by tests affected):
        6 × [run] redis.exceptions.ConnectionError: … Connection refused.
        3 × [run] AssertionError
      =================== 9 failed, 41 passed in 18.31s ===================
### BLOCKER 2 — tests: connection refused: localhost:6379   (surfaced after v1)   ← current turn
- v2 · insert@6 +apt-get install -y redis-server
    think:   tests need a redis on 6379 — install the server
    observe:
      … 9 failed, 41 passed in 17.88s …
- v3 · insert@7 +redis-server --daemonize yes
    think:   no init system in the container — launch redis directly
    observe: (current run — full output above under LAST RUN OBSERVATION)
    ↳ already tried for this blocker (didn't help): insert@6 +apt-get install -y redis-server, insert@7 +redis-server --daemonize yes

Turn 6/25 (19 left). Reason briefly, then call one tool — explore or edit.
```

### Ours — `REACT_HISTORY=flat` (SWE-agent-shaped: no headers/ledger/STUCK)
Same top (`CURRENT setup.sh` + `LAST RUN OBSERVATION`), then:
```
HISTORY — chronological; each attempt is a card: think → action → observe (observe = real stdout/stderr).
    think:   which apt package actually ships libpq-fe.h?
- explored `apt-file search libpq-fe.h` →
        libpq-dev: /usr/include/postgresql/libpq-fe.h
- v1 · insert@3 +apt-get install -y libpq-dev
    think:   install the -dev headers, not the runtime lib
    observe:
      … 9 failed, 41 passed in 18.31s …
- v2 · insert@6 +apt-get install -y redis-server
    think:   tests need a redis on 6379 — install the server
    observe:
      … 9 failed, 41 passed in 17.88s …
- v3 · insert@7 +redis-server --daemonize yes
    think:   no init system in the container — launch redis directly
    observe: (current run — full output above under LAST RUN OBSERVATION)

Turn 6/25 (19 left). Reason briefly, then call one tool — explore or edit.
```
Note: `flat` drops the `### BLOCKER` headers, the `↳ already tried` ledger, and STUCK — but it is **still a narrator reconstruction** ("- v1 · insert@3 …", "think: …"), NOT the model's own assistant messages. It removes grouping, not the whose-words gap.

### mini-swe-agent — the message list (verbatim templates)
`observation_template`, `config/default.yaml:114-141`:
```
<returncode>{{output.returncode}}</returncode>
{% if output.output | length < 10000 -%}
<output>
{{ output.output -}}
</output>
{%- else -%}
<warning>The output of your last command was too long. Please try a different command …</warning>
<output_head>{{ output.output[:5000] }}</output_head>
<elided_chars>{{ elided_chars }} characters elided</elided_chars>
<output_tail>{{ output.output[-5000:] }}</output_tail>
{%- endif -%}
```
Actual conversation the model sees (roles are real messages, not a rendered blob):
```
[system]     You are a helpful assistant that can interact with a computer. … (system_template)
[user]       <task>…</task>  Please solve this issue … (instance_template)
[assistant]  THOUGHT: install the -dev headers, not the runtime lib
             ```bash
             apt-get install -y libpq-dev
             ```
[user]       <returncode>0</returncode>
             <output> … 9 failed, 41 passed … </output>
[assistant]  THOUGHT: tests need a redis on 6379 — install the server
             ```bash
             apt-get install -y redis-server && redis-server --daemonize yes
             ```
[user]       <returncode>0</returncode>            ← THIS is the immediate observation: just messages[-1]
             <output> … 9 failed, 41 passed … Connection refused … </output>
```
No history section, no summary, no dedup. The immediate observation is simply the last `user` message.

### SWE-agent — the message list + `LastNObservations` (paper config)
`next_step_template`, `config/default.yaml:28-32`: `OBSERVATION:\n{{observation}}`.
Elision string, `history_processors.py:172`: `Old environment output: ({num_text_lines} lines omitted)`.
```
[system]     … tools, ACI file-viewer instructions …
[user]       <task> … </task>
[assistant]  DISCUSSION: install the -dev headers …
             ```
             apt-get install -y libpq-dev
             ```
[user]       Old environment output: (214 lines omitted)          ← OLD obs elided (past the last-N window)
[assistant]  DISCUSSION: tests need a redis on 6379 …
             edit / bash tool call
[user]       OBSERVATION:                                          ← immediate obs kept VERBATIM
             … 9 failed, 41 passed … Connection refused …
```
`_get_omit_indices` returns `observation_indices[1:last_removed_idx]` with `last_removed_idx = max(0, (len//polling)*polling - n)` (`history_processors.py:153-155`) — the last `n` (=5) observations are **never** in the omit set, so the immediate one is always full; only older ones collapse. Assistant reasoning is **never** elided (`assert data.get("message_type") == "observation"` at `:171`).

---

## Comparison across the 7 dimensions

| # | dimension | ours (grouped) | ours (flat) | mini-swe-agent | SWE-agent |
|---|---|---|---|---|---|
| 1 | **message structure** | 2 msgs/turn: stable system + one re-rendered user blob (`planner.py:158-165`) | same | growing flat `messages` list (`default.py:42`) | growing raw `history` → processed `messages` property (`agents.py:539-559`) |
| 2 | **whose words** | narrator summary of thought+action (`- v1 · insert@3`, `think:`), threaded from `Step.thought` (`history_view.py:286-292`) | narrator summary (still not the model's msgs) | model's **own** assistant messages verbatim | model's **own** assistant messages verbatim (reasoning never elided) |
| 3 | **observation format** | `BUILD FAILED at cmd (line N)` / `BUILD OK. TESTS p/e` + `[collect]/[run]` histogram + tail (`loop.py:74-94`) | same | raw `<returncode>` + `<output>` | `OBSERVATION:\n{{observation}}` (raw) |
| 4 | **old-output mgmt** | blocker grouping + do-not-retry ledger + byte-identical dedup + withhold current (`history_view.py`) | recency list + dedup + withhold current | **none** (full list every query) | `LastNObservations` elides old→"N lines omitted", keeps last 5 + all reasoning; `ClosedWindow` collapses stale file views |
| 5 | **structure imposed** | high: `### BLOCKER n` grouping, ledger, STUCK | low: plain chronological cards | none (flat transcript) | none by default; optional elision processors |
| 6 | **big-output handling** | `safety_compress_observation` (keep errors/status, drop download noise) + hard cap; immediate capped 8000 keep-both-ends | same | truncate-keep head5000+tail5000 over 10k | truncate-keep `[:100000]` + `<response clipped>` |
| 7 | **reproducibility framing** | numbered `CURRENT setup.sh` re-rendered each turn + reset-each-turn; script is the only carry-over | same | persistent shell/repo, stateful | persistent repo + stateful ACI file-viewer window re-rendered each turn |

---

## What this says about the immediate turn specifically

1. **Our immediate-turn handling is a strength, not a gap.** A dedicated top slot (`LAST RUN OBSERVATION`) puts the freshest, most decision-relevant output where recency helps most, and it is the ONE observation we never compress away to a histogram-only view — it carries the full tail. Neither reference agent spotlights the latest observation; they rely on it just being last. Given reset-each-turn (the current run is *the whole environment's* state, not an incremental delta), spotlighting it is the right call. **Keep it.**

2. **The withhold/dedup machinery only exists because history is a separate rendered section.** Because the current turn is represented both at the top and inside `render_history`, we must withhold the duplicate (`:293-294`) and collapse byte-identical repeats (`:245-246`). In a real message list the current observation is simply the last message — the whole class of dedup/withhold code disappears. So the complexity is architectural debt from the narrator-summary shape, not essential.

3. **`REACT_HISTORY=flat` is NOT the SWE-agent structure.** It removes grouping/ledger/STUCK, but the cards are still a third-person reconstruction. It's a clean A/B baseline for *grouping* (dimension 5), but it does not test dimension 2 (whose-words). To actually test "does the model reason better seeing its own prior messages," we'd need the real message-list variant, which `flat` does not give.

---

## Recommendation

**Keep the immediate-turn design. Move history from narrator-summary toward the model's own messages. Port SWE-agent's elision instead of our ad-hoc dedup. Keep the do-not-retry ledger as our one justified deviation.**

Concretely, staged:

1. **Keep `LAST RUN OBSERVATION` at the top, as-is.** It's better than either reference for our reset-each-turn world. No change.

2. **Adopt a real growing message list for the think→action side** (the biggest honest win). Each turn: an `assistant` message = the model's real `thought` + the real `edit`/`explore` tool call (native tool-calling already gives us structured calls, `planner.py:170-171`); a `user` message = that turn's observation. This kills the whose-words drift (dimension 2) and **dissolves the withhold/dedup complexity** (point 2 above) — the current observation is just the last message. `render_history`'s narrator reconstruction goes away.
   - Reset-each-turn stays coherent under this shape: the `user` observation reads as "here's what running the whole current script does now," which is exactly true. We are not implying a false per-edit causality.

3. **Port `LastNObservations` (keep last N verbatim, elide older to "Old environment output: (N lines omitted)", never elide reasoning; `history_processors.py:147-176`).** This is the principled replacement for our dedup + `_OBS_MAX_CHARS` juggling, and it is *especially* suited to reset-each-turn: our observations are highly repetitive (the redis example is 3 near-identical `Connection refused` dumps), which is precisely what last-N elision is for. Keep `safety_compress_observation` as the per-observation noise filter underneath it.

4. **Keep the do-not-retry ledger — inject it as a templated note on the latest turn**, not as a rendered history section. SWE-agent doesn't need an anti-fixation nudge because its edits accumulate state; ours resets every turn, so the agent is structurally more prone to re-try an edit that "didn't stick." That makes the `↳ already tried for this blocker` ledger (`history_view.py:319-320`) a real, architecture-specific value-add. Keep it (neutral default), drop `### BLOCKER` grouping and STUCK unless an A/B shows grouping helps.

5. **Preserve the graph-ablation seam.** The graph variant injects `graph_context` per turn (`planner.py:149-152`); in a message-list refactor, inject it as the same per-turn appended note so the no-graph vs graph delta stays exactly one field (spec §14). This is the main constraint on the refactor — do not let the message-list rewrite entangle the ablation axis.

**Net:** the immediate turn already does the right thing; the payoff is in the *history*. The move is message-list (fidelity + simplicity) + ported last-N elision (compaction) + kept ledger (our justified anti-loop deviation). `REACT_HISTORY=flat` remains useful as a grouping A/B, but it is not the endpoint — it doesn't test whose-words.

---

## Evidence index (file:line)

- **Ours:** `planner.py:138-156` (`_render`), `:146` (LAST RUN slot), `:158-165` (`plan` = 2 msgs), `:170-171` (native tool call). `loop.py:74-94` (`_observation`), `:44` (8000 cap), `:170-187` (reset→run-whole→certify→test), `:218` (immediate obs). `history_view.py:156` (`REACT_HISTORY`), `:245-246` (dedup), `:293-294` (withhold current), `:316-333` (ledger+STUCK, grouped-only). `history.py:19-29` (keep-both-ends truncate). `observation.py:210-235` (`safety_compress_observation`).
- **mini:** `default.py:42,69-72` (messages/add), `:147-149` (query sends all), `:152-155` (append obs); `config/default.yaml:114-141` (observation_template, 10k truncate-keep).
- **SWE-agent:** `agents.py:539-551` (messages property runs processors), `:556-559` (`_append_history`), `:702-746` (append obs + 3 templates by size), `:67-85` (templates, `max_observation_length=100_000`); `history_processors.py:74-82` (identity default), `:85-91` (LastNObservations docstring), `:147-176` (omit indices + elision, reasoning kept), `:215-258` (ClosedWindow); `config/default.yaml:28-32` (next_step_template), `:67-69` (cache_control default); `config/sweagent_0_7/07.yaml:100-101` (`last_n_observations n:5`).
