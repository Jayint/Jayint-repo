# Agentic prompt style — LANDED behind `REACT_MSG_STYLE=agentic`

**Date:** 2026-07-13 · **Branch:** `john-v3-multi-lang` · **Tests:** 386 green in `tests/react_repair/`
**Default:** `classic` (unchanged). The new shape is opt-in and unvalidated — it needs the VM A/B.

---

## Observation

Reading the **real** turn-3 prompt from the live itsdangerous e2e
(`docs/superpowers/artifacts/2026-07-13-react-prompt-live-sample.txt`, 15,446 chars) surfaced four
things no unit test would have caught:

1. The last user message speaks in **three voices at once** — tool output (`==== ERRORS ====`),
   harness state (the numbered script), and narrator instructions (ledger, turn budget, rejection
   footer) — with nothing in the bytes distinguishing them.
2. **`BUILD OK. TESTS 0/5 passed.` is false.** That repo has 297 tests. Zero ran. The "5" is five
   *modules* that failed to import. `executed = passed+failed+errors` is the right GATE denominator;
   rendering it as a ratio tells the model five tests exist and none passed.
3. The immediate slot — our **biggest** budget (8,000 chars) — spent **3,532 chars** on five
   near-identical tracebacks carrying **one** unique fact. Nothing compressed them: `safety_compress`
   is SIZE-gated at 8k, and a few-KB pytest failure (the arm's most common observation) sails under it.
4. **`TestOutcome.collected` was dead on arrival.** `VERIFY_TEST_CMD` is `python -m pytest -q`, and
   `-q` never prints `collected N items`, so `_COLLECTED` never matched, `collected` was permanently
   `0`, and the `(C collected)` header suffix could not fire once. Verified against a real `pytest -q`.

## Why

Agent transcripts have one atom: `command → output`. We shipped output with no command. Worse, the
arm's strangest property (the WHOLE script re-runs from a clean container **every turn**) had to be
explained in three sentences of system prose because the transcript never *showed* it. Models are
post-trained on the real shape; every deviation is off-distribution for no gain.

## What

New lever **`REACT_MSG_STYLE=agentic`** (default `classic` — the A/B control arm, byte-identical to
what shipped, except for the `collected` bug fix which lands in both).

| change | file |
|---|---|
| `$ cmd → exit N` envelope; pytest's real counts, never a ratio; no invented pytest rc | `envelope.py` (new) |
| dedup pytest blocks by CAUSE + drop importlib boilerplate | `pytest_blocks.py` (new) |
| the lever, shared by write-time and read-time | `style.py` (new) |
| agentic message list: calls as kwargs, edit tool-result, fenced harness state | `message_view.py` |
| `collected` derived from the `-q` summary; `failed`/`errors`/`skipped` kept apart | `gate.py` |
| structured `_outcome()`; dedup **before** compress; `_action_struct()` for refusals | `loop.py` |
| `Step.outcome` | `history.py` |
| `strip_pip_progress()` (opt-in; keeps `Successfully installed` / `Requirement already satisfied`) | `observation.py` |
| `rejected` threaded so a refusal lands in the refused call's own result slot | `planner.py` |

**Ordering matters and is the subtle part** (see `style.py`): dedup runs at **write** time, before
`safety_compress`. Its selection pass keeps at most 12 error blocks; on a large flood those 12 slots
fill with copies of ONE cause and a genuinely different cause further down is dropped *permanently*,
because the compressed text is what gets stored. Deduping first means the 12 slots hold 12 distinct
**causes**. Both transforms are idempotent, so the render layer re-applies them harmlessly.

## Verification

- **386 tests green** (`tests/react_repair/`), up from 326. New: `test_pytest_blocks.py` (12),
  `test_envelope.py` (17), `test_message_view_agentic.py` (14), plus gate/observation/loop additions.
- **The buried-cause bug is proved by a test, in both directions**: `test_a_flood_of_identical_errors_
  buries_a_distinct_cause_without_the_bundle` (classic drops `libpq.so.5`) and
  `..._the_bundle_dedups_first_so_the_buried_cause_survives` (agentic keeps it).
- **The loop is proved to populate `Step.outcome`** end-to-end (`test_loop_end_to_end_renders_a_valid_
  agentic_prompt`) — the view tests hand-build History, so without this the envelope could have
  rendered empty in production and every unit test would still pass.
- **Replayed against the real live trace.** On the actual turn-3 state: the pytest block goes
  3,451 → 909 chars (26%), total prompt 15,446 → 12,612 (82%), and the whole reduction is content the
  model did not need. Rendered artifact: `2026-07-13-react-prompt-AGENTIC-sample.txt`.
- Whole-repo suites unaffected: depgraph 1298, envstate 175, manifest_builder 73, eval 357 green.
  (`tests/bench` has 1 pre-existing Docker-e2e failure, unrelated — confirmed by stashing.)

## NOT done — deliberately

- **The A/B has never been run.** `classic` vs `agentic` needs the VM. The new shape is *unvalidated*.
- **Native `tool_calls` / `tool`-role messages.** Assistant turns render calls as text
  (`edit(verb="replace", start=7, ...)`), not the real protocol. Lowest-value item; it would make the
  rejection-in-position fix natural rather than simulated.
- **Elision policy.** Old observations still elide by AGE, which (a) evicts explore results — whose
  content never goes stale, unlike a superseded run — and (b) rewrites an already-sent message, so it
  is the one cache-hostile thing we do. Left alone so this A/B isolates the transcript SHAPE.
- **`REACT_MSG_IMMEDIATE_CAP` is still inert above 8000**, because `loop._obs_body` already caps the
  stored observation at `REACT_OBS_MAX_CHARS` (also 8000). The stored "raw" is not raw.
