# Runbook: A/B the react per-turn prompt (blob vs message-list)

**What:** compare `REACT_PROMPT_STYLE=blob` (default, single re-rendered user blob) vs `messages`
(growing assistant/user message list) on a handful of repos — turn economy + token cost + DONE rate.

**Status:** the lever is built, tested, and proven to propagate end-to-end (loop → planner →
`build_messages`, byte-exact action). This runbook is the ready-to-run procedure. It has **not** been
executed on the VM (needs the VM handle + deploy). Nothing here changes default behavior.

## The lever

Read per-call in `planner._messages`, so setting the env var in the run process is all it takes — no
code change, no flag plumbing through the harness.

| env var | default | effect |
|---|---|---|
| `REACT_PROMPT_STYLE` | `blob` | `messages` = growing assistant/user list |
| `REACT_MSG_KEEP_LAST_OBS` | `3` | observations kept verbatim (messages mode); older elided |

## Per-repo driver

`scripts/run_v3_e2e.py <repo> --arm react --trace-out <path>` runs the react arm on one repo and
writes the ReactLog trace JSONL (the `end` record carries `outcome` + `steps`). `[Tokens]` lines go to
stdout — capture them to a `.log` for the token economy.

## Procedure (on the VM — react arm is deploy-first / local-only)

```bash
# 0. deploy the branch's react arm to the VM (react arm is not in the standard bench fetch)
#    rsync/scp src/react_repair + scripts/run_v3_e2e.py + scripts/react_ab_compare.py to the box.

# 1. pick the repo set (start small — the e2e-smoke set + a couple of service repos)
REPOS=(/opt/repos/click /opt/repos/itsdangerous /opt/repos/typer /opt/repos/httpx /opt/repos/rq)
MODEL="deepseek-v4-flash"        # or the arm's usual slug
mkdir -p out/blob out/messages

# 2. run each repo TWICE — identical except REACT_PROMPT_STYLE. Same seed setup.sh via --initial-script
#    if you want repair-only parity; otherwise construction runs identically for both.
for r in "${REPOS[@]}"; do
  name=$(basename "$r")
  REACT_PROMPT_STYLE=blob \
    python scripts/run_v3_e2e.py "$r" --arm react --model "$MODEL" --max-steps 30 \
      --trace-out out/blob/$name.jsonl        > out/blob/$name.log 2>&1
  REACT_PROMPT_STYLE=messages \
    python scripts/run_v3_e2e.py "$r" --arm react --model "$MODEL" --max-steps 30 \
      --trace-out out/messages/$name.jsonl    > out/messages/$name.log 2>&1
done

# 3. compare turn economy + token cost + DONE rate
python scripts/react_ab_compare.py out/blob out/messages
```

Output is a per-repo side-by-side table (`outcome / steps / tokens`) + an aggregate
(DONE rate, avg turns-to-DONE, avg tokens/repo).

## Reading the result

- **DONE rate** — does the message-list shape reach a clean env more often? (Primary.)
- **Turns-to-DONE** — at equal DONE rate, fewer turns = better economy (the whose-words + elision bet).
- **Tokens/repo** — the message list grows but elision + no re-rendered narrator should keep it lean;
  watch for the crossover where a long transcript costs more than the blob.
- **Pass-rate / EBSR / ESSR** — NOT in this tool. Pull those from the benchmark aggregator
  (`run_rat_benchmark.py` aggregate mode) on the same repos, per arm, and compare separately.

## Caveats / knobs to sweep if the first pass is inconclusive

- `REACT_MSG_KEEP_LAST_OBS` (default 3) — the reset-each-turn repetition means this drives token cost;
  try 2 and 5.
- The message-list assistant turns are **text** (`→ edit: …`), not native `tool_calls`/`tool`-role
  pairs. If a provider reasons noticeably better with real tool-call history, that's the next lever to
  build — but test the cheap text form first.
- Keep `--max-steps` identical across arms (turn economy is only comparable at equal budget).
