# Handoff: discuss the react agent's per-turn prompt

**Status:** ready for a fresh session. Self-contained — assumes no prior conversation.
**Repo:** `/Users/john/john-v3-multi-lang` (branch `john-v3-multi-lang`).
**Purpose:** DISCUSSION, not a build task. Read the real prompt, then argue about what to change.

---

## Read this first — a REAL prompt from a REAL run

`docs/superpowers/artifacts/2026-07-13-react-prompt-live-sample.txt` — the verbatim bytes sent to the
model on turn 3 of a live e2e (real Docker container, real LLM: `deepseek/deepseek-chat`, repo:
`pallets/itsdangerous`). Not a mockup. ~15.4k chars ≈ 3.9k tokens.

Full traces (every turn's prompt is in the `plan` records, field `prompt`):
- `docs/superpowers/artifacts/2026-07-13-react-live-trace-itsdangerous.jsonl` — 3 turns (explore → edit → edit), DONE 297/297.
- `docs/superpowers/artifacts/2026-07-13-react-live-trace-FALSE-GREEN.jsonl` — the run where the agent
  cheated (see "Known live findings" below).

Render any turn with:
```bash
python3 -c "
import json
p=[json.loads(l) for l in open('docs/superpowers/artifacts/2026-07-13-react-live-trace-itsdangerous.jsonl')
   if json.loads(l)['phase']=='plan'][-1]      # [-1]=last turn; [0]=first
for m in p['prompt']:
    print('=== %s ===' % m['role'].upper()); print(m['content']); print()
"
```

## The architecture, in one paragraph

The agent repairs ONE build script (`setup.sh`). **Reset-each-turn:** every turn resets to a clean base
image and re-runs the WHOLE script; only the script text carries over. The prompt is a **growing
message list** (SWE-agent shape, now the DEFAULT): `system` → `user`(baseline observation) →
`assistant`(the model's own thought + byte-exact tool call) → `user`(observation) → … with the live
"workbench" (numbered `CURRENT setup.sh` + do-not-retry ledger + turn-budget line) merged onto the LAST
user message. The immediate action+observation sit at the END (the recency slot the model attends
hardest), NOT hoisted to the top.

**Recency gradient on observation detail** — all re-rendered each turn from the ONE stored
`Step.observation_raw` (no second copy stored; an observation ages by position):
| tier | what | budget |
|---|---|---|
| immediate (last run) | `── LAST RUN (full — the state you are acting on) ──` | `safety_compress` @ 8000 |
| recent (within last-N) | plain | `safety_compress` @ 1500 |
| older (beyond last-N) | `Old run output: (K lines elided)` | elided |
| explore/cat results | always FULL (reading the file IS the point) | head+tail cap 6000 |

Observations are the **real** pytest/pip stdout+stderr, `safety_compress`'d (noise dropped, error blocks
kept). There is **no synthesized histogram** in the default path.

## Where it lives

- `src/react_repair/planner.py` — `build_system_prompt()` (GOAL / APPROACH / INTEGRITY / ENVIRONMENT /
  TOOLS); `ReactPlanner._messages()` dispatches on the prompt-style lever; `_render()` is the legacy blob.
- `src/react_repair/message_view.py` — **`build_messages()`**: the growing message list, recency tiers,
  last-N elision, byte-exact assistant turns, the workbench scaffold. *This is the file to read.*
- `src/react_repair/loop.py` — `_observation()` (the `BUILD FAILED at cmd (line N)` /
  `BUILD OK. TESTS p/e passed (C collected)` header + `_obs_body` = safety_compress'd real output);
  `_cheat_reject()` (the two host gates).
- `src/react_repair/observation.py` — `safety_compress_observation()`: **pass 1** = always-on noise strip,
  **pass 2** = size-gated head/tail+error-block selection, then a line-boundary cap.
- `src/react_repair/history.py` — `Step` (holds `observation_raw` + the structured `action`).
- `src/react_repair/anti_cheat.py` — collection-narrowing gate + self-install gate.

## Levers (all env vars, read per-call)

| lever | default | effect |
|---|---|---|
| `REACT_PROMPT_STYLE` | `messages` | `blob` = the legacy single re-rendered user blob |
| `REACT_HISTORY` | `flat` | `grouped` = blob-only: `### BLOCKER n` headers + ledger + STUCK |
| `REACT_OBS_MODE` | `compress` | `histogram` = prepend the synthesized ranked-cause breakdown |
| `REACT_MSG_KEEP_LAST_OBS` | `3` | how many recent observations stay verbatim before elision |
| `REACT_MSG_IMMEDIATE_CAP` | `8000` | char budget for the immediate (full) run output |
| `REACT_OBS_BODY_CAP` | `1500` | char budget for recent-tier observations |
| `REACT_EXPLORE_FULL_CAP` | `6000` | cap on explore/cat output |
| `REACT_STUCK_MODE` | `neutral` | `off` \| `directive` (blob/grouped only) |

Render any variant OFFLINE (no LLM, no Docker):
```python
import sys; sys.path[:0] = ["/Users/john/john-v3-multi-lang", "/Users/john/john-v3-multi-lang/src"]
from src.react_repair.planner import ReactPlanner
from src.react_repair.history import History
h = History()
h.record(0, "", "baseline → BUILD FAILED", "BUILD FAILED at `pip install -e .` (line 5):\nboom\n")
h.record(1, "install the headers", "edit v1 (insert@3 +apt-get install -y libpq-dev) → 41/50",
         "BUILD OK. TESTS 41/50 passed.\n… real pytest output …\n",
         action={"kind": "edit", "verb": "insert", "start": 3, "end": 3,
                 "content": "apt-get install -y libpq-dev"})
p = ReactPlanner(client=None, model="x", graph_context=None, env_info="  base: python:3.11-slim")
for m in p._messages(h, "#!/usr/bin/env bash\nset -e\napt-get update\n", "", None, None, 3, 25, None):
    print("===", m["role"], "==="); print(m["content"])
```

## Known live findings (already fixed — context, not tasks)

- **The compressor was leaking meta-chatter** into the prompt (`[Safety Compression Applied]`,
  `Original observation length: …`, `(repetitive output omitted…)`) and the sandbox's `__INSTALL_FAIL__`
  ERR-trap sentinel, and it spliced text **mid-word**. All fixed; cuts are line-boundary now.
- **`safety_compress` was size-gated** — under the cap it returned output verbatim, so small build logs
  reached the model with every noise line intact. The noise strip is now **always on**; only the
  content-dropping *selection* pass is size-gated.
- 🔴 **A FALSE GREEN**: the agent shipped `pip install itsdangerous` (the PUBLISHED PyPI package) instead
  of `pip install -e .`, went **297/297 green**, and the host **certified DONE** — but the repo's own
  checkout was never installed, so the tests ran against code that isn't in the repo. Collection *grew*
  (68→297) so the anti-gaming gate was blind. Now blocked by `anti_cheat.self_install_reason` + an
  INTEGRITY rule. **Any PyPI-published repo could be "solved" this way — prior benchmark numbers may be
  inflated.** Trace: the `-FALSE-GREEN.jsonl` artifact.

## Open questions to actually discuss

1. **The explore result dominates the prompt.** In the sample, `cat pyproject.toml` is ~4.5k of ~15.4k
   chars (~29%) — and most of it (tox/ruff/mypy/coverage config) is useless to the agent. Explores are
   FULL by design ("reading the file IS the point"). Cap them? Summarize? Let the agent request ranges?
   It *did* pay off here (the agent found the `tests` group and the flit module name).
2. **Pip noise is deliberately kept.** `Collecting`/`Downloading`/`━━━` bars/`Requirement already
   satisfied`/the root-user WARNING all reach the model (the noise vocabulary is tuned for apt/maven/git,
   not pip — and `Requirement already satisfied` is explicitly in the STATUS keep-list). This was a
   deliberate call. Revisit?
3. **Assistant turns are TEXT, not native `tool_calls`.** We render `→ edit(insert @7): …` as prose
   rather than real `tool_calls`/`tool`-role message pairs. Does a provider reason better with the real
   protocol?
4. **`thought` is sometimes empty** — the model returns a bare tool call with no prose, so the assistant
   card degrades to action-only. "Whose-words" fidelity is therefore model-dependent.
5. **The histogram is now opt-in.** It dedups+ranks ALL causes (surfacing a buried cause that
   `safety_compress`'s 12-error-block cap drops) and carries counts + `[collect]`/`[run]` tags — all of
   which the default path loses. Should it come back, or come back as a 1-line "top cause"?
6. **The turn-budget line misfires at small `--max-steps`** — with `--max-steps 4`, turn 1 already says
   "the turn budget is almost gone" (`_LOW_BUDGET_TURNS=5`). Harmless at the default 30.
7. **The A/B was never run.** `REACT_PROMPT_STYLE=messages` (default) vs `blob` — tooling is built
   (`scripts/react_ab_compare.py`, runbook `docs/superpowers/plans/2026-07-12-react-prompt-style-ab-runbook.md`)
   but it needs the VM. So the current default is **unvalidated**.

## Constraints

- 326 tests green (`python3 -m pytest tests/react_repair/ -q`). Keep them green.
- The graph-ablation seam must stay ONE field: the graph variant differs only by `graph_context`.
- Prompt changes are render-layer and arm-independent — don't entangle them with the graph axis.
