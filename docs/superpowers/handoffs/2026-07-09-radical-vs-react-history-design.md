# react arm vs radical baseline — reasoning / history / observation design

**Date:** 2026-07-09. Compares my `--arm react` script-repair loop (`src/react_repair/`) against the
**radical** baseline agent (VM `/opt/agents/radical`, branch `radical` @ `0496a66`). Radical source
snapshotted to scratchpad for this read: `agent.py`, `src/planner.py`, `src/observation_compressor.py`,
`src/memory_manager.py`, `src/sandbox.py`.

Purpose: answer three design questions and confirm my arm does not deviate from the baseline where it
matters.

---

## Q1 — Is there a "reasoning" block, and is it the model's native thinking or prompt-instructed text?

**Radical: prompt-instructed `Thought:` text. The model's native thinking block is NOT used as the
reasoning; it is stripped, and only read as an emergency fallback when `content` is empty.**

- `planner.py:92-93` — the response protocol literally prompts: `"Thought: <your reasoning>"` followed by
  one `Action:`. Classic ReAct. The reasoning the agent "explains" is this prompted text line.
- `planner.py:228-238` — reads `response.choices[0].message.content`. If that is empty/None, it falls back
  to `getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)`. Comment names the exact
  cause: "Some OpenRouter providers (deepseek-v4-flash, minimax) return content=None and put the text in
  reasoning/reasoning_content."
- `planner.py:574-587` — `_extract_thought` prefers the explicit `Thought:` tag; only if absent does it pull
  a `<think>…</think>` block. So native thinking is a *fallback source for the Thought*, never the primary.

**My react arm: IDENTICAL design.**
- `planner.py:12-25,50-56` — SYSTEM_PROMPT prompts "Respond with a Thought and exactly ONE of: Action /
  Script"; `plan()` extracts `extract_thought(text)`. The reasoning is prompt-instructed text.
- `llm_response.py:155-164` (`response_text`) — prefers `choices[0].message.content`, **falls back to the
  `reasoning` field** (message attr or `model_extra`) exactly like radical. ✅ Already aligned — no gap.
- `llm_response.py:81-126` (`strip_reasoning_markup`) — strips `<think>…</think>` from content so the parser
  sees clean text; `strip_minimax_toolcall` removes MiniMax's `<minimax:tool_call>` XML.
- Under MiniMax thinking-OFF (our default, `apply_minimax_thinking`), there is no native thinking block at
  all, so the Thought is purely the prompted text in both agents.

**Verdict:** same model of reasoning. Prompted `Thought:` text is the explanation; native thinking is
stripped from content and used only as a fallback when content is empty. No deviation.

---

## Q2/Q3 — How does radical record last-command / compacted history with its in-container probe, and how
should my script-patching agent record history + observation?

### Radical = command-primary over a STATEFUL container

- **Execution model:** one bash command per turn (`Action: <bash command>`), executed in a **live,
  persistent** container (`sandbox.py execute → container.exec_run`). State **accumulates** across commands.
- **Rollback:** container image **snapshots** (`_register_snapshot`, `last_success_image`, baseline snapshot
  at boot). `Action: __ROLLBACK__` restores the last good snapshot when a mutation left "partial or uncertain
  state" (`planner.py:120-125`). Needed *because* state accumulates.
- **History:** a genuine growing ReAct **chat message list** (`planner.py:188-267`): system + [seed,
  `Observation: …`, assistant `Thought:+Action:`, `Observation: …`, …]. Because assistant turns accumulate,
  radical must **sanitize** them (`sanitize_assistant_content`, `_strip_generated_future_trajectory`,
  `planner.py:538-572`) to strip hallucinated future `Observation:`/`Action:` the model overgenerates past
  the stop token. Trimmed to a **token budget** (`_trim_messages_to_budget`, `history_token_budget`).
- **Observation:** the output of the ONE command just run, compressed two ways
  (`observation_compressor.py`): (1) **deterministic safety compression** — regex-keyed keep/drop that
  preserves test-summary/error/status blocks (`SAFETY_STATUS_PATTERNS`, `SAFETY_ERROR_PATTERNS`,
  `SAFETY_BLOCK_PATTERNS`) and collapses apt/git progress noise; fires above
  `safety_compression_threshold_chars=200_000` → target `20_000`; then (2) **LLM unified compression** of a
  single step, delayed `compression_delay=2` steps, only above `compression_threshold_chars=1500`.
- **"Last command":** the most recent Action; its raw Observation is appended as the newest user message and
  compressed after the 2-step delay (recent stays raw, older gets summarized).
- **Deliverable:** not the container — a synthesized **Verification Bundle** (`runtime_preparation_commands`
  + `test_commands`) recording the successful commands, later turned into a Dockerfile.

### My react arm = script-primary over a STATELESS-per-turn container

- **Execution model:** the agent edits ONE whole build script (`Script:` fenced block) or runs a read-only
  `Action:` probe. Each PATCH does `reset() → run_script(whole script) → certify → run_tests`
  (`loop.py:51-67`). State does **not** accumulate in the container; it accumulates in the **script**.
- **Rollback:** **free and implicit** — every turn resets to base, so there is never partial/uncertain
  state. I need **no snapshots and no `__ROLLBACK__`** (radical's entire snapshot/rollback machinery is
  unnecessary here). This is a deliberate simplification, not a missing feature.
- **History:** `History` (`history.py`) as a `Step` list (thought, action_summary, observation_prompt),
  **re-rendered compactly into a single user message each turn** (`planner._render`) — NOT a growing chat
  transcript. Consequence: the model never sees its own prior raw assistant turns, so there is **nothing to
  sanitize** — radical's overgeneration/hallucinated-Observation problem cannot occur here. Over-generation
  past the stop token is ignored because the parser extracts only the first Thought + first Action/Script.
- **Observation:** the FULL fresh build+test result each turn (`_observation`, `loop.py:36-41`):
  `BUILD FAILED at \`cmd\`: …` or `BUILD OK. TESTS x/y passed: …`, tail-kept to `_OBS_MAX_CHARS=8000`
  (pytest's summary + last failures live in the tail). Because the whole script re-runs, each observation is
  self-consistent — not an incremental command result whose meaning depends on accumulated state.
- **Compression:** same two-tier shape — deterministic `safety_truncate` (keep tail, `safety_max_chars=4000`
  for stored steps) then optional LLM reflective compressor. My `compress_delay=2` and
  `compress_threshold_chars=1500` are **identical to radical's constants** — clearly ported. My deterministic
  tier is simpler (tail truncation vs radical's regex block-keeping).
- **Deliverable:** the script itself (`setup.sh`) — directly runnable, no separate Verification-Bundle
  synthesis step.

### Verdict — I do not deviate where it matters; where I differ, it's simpler by design

| Dimension | Radical | React arm | Aligned? |
|---|---|---|---|
| Reasoning source | prompted `Thought:` text; native thinking stripped/fallback | same | ✅ identical |
| `reasoning_content` fallback | yes (`planner.py:234`) | yes (`response_text`) | ✅ identical |
| `stop` token | `stop=["Observation:"]` | `stop=["Observation:"]` | ✅ identical (see below) |
| `temperature` | 0 | 0 | ✅ |
| Container state | stateful + snapshots + `__ROLLBACK__` | reset-to-base each turn (free rollback) | ⚠️ mine simpler — no rollback needed |
| History form | growing chat transcript (must sanitize) | compact re-rendered Step list (nothing to sanitize) | ⚠️ mine simpler + safer |
| Observation | single-command output, compressed | full fresh build+test, tail-kept | ⚠️ mine coarser-grained, self-consistent |
| Compression delay / threshold | 2 / 1500 chars | 2 / 1500 chars | ✅ identical constants |
| Deterministic compression | regex block keep/drop | tail truncation | ⚠️ mine simpler |
| History token budget | yes (`_trim_messages_to_budget`) | none (all steps rendered) | ⚠️ minor; bounded by 30-step cap + per-step truncation |

**Optional future alignment (none required):**
1. Port radical's regex safety-compression (`SAFETY_STATUS/ERROR/BLOCK_PATTERNS`) in place of my tail
   truncation, if a repo's failure detail gets lost above 8000 chars. Marginal — pytest summary is in the tail.
2. Add a history token budget if 30-step runs ever bloat the prompt. Bounded today by per-step truncation.

Neither is a correctness gap. The script-primary model legitimately removes radical's snapshot/rollback and
transcript-sanitization machinery.

---

## Bonus finding — retires the handoff's "one MiniMax risk"

Radical passes the **identical** `stop=["Observation:"]` to MiniMax (`planner.py:222`) and it is the working
baseline. My planner passes the same `stop=["Observation:"]`. **So the `stop` param is proven-safe on
MiniMax — the "will MiniMax reject stop?" risk from the VM handoff is retired.** No code change.
