# Handoff — run the v3 construction-only ratbench batch (concurrency 6)

Paste everything below into a fresh Claude Code session.

---

## Task
Run the **v3 agent** (run_v3 graph-scheduler) on the **medlarge15** ratbench benchmark in
**construction-only** mode (first-pass build script, NO repair loop) at **concurrency 6** on the VM,
then report how many repos pass pytest on the first-pass build script.

## Environment / access
- VM: `ssh root@167.233.64.96` (Linux, has `setsid`/`timeout`; has Docker).
- The v3 agent is variety **`john-planner-v3`** → tracks branch **`v3-core`** → checkout
  `/opt/agents/john-planner-v3`, currently at commit **`bedf97c`**. DO NOT redeploy — it's already live.
- Harness: `/opt/harness` (bench CLI + `run_rat_benchmark.py`). Dataset (git-tracked):
  `/opt/harness/datasets/rat_python_medlarge15.json` (15 repos).
- Creds live in `/opt/harness/.env` (OPENROUTER + MINIMAX). Python: **`/opt/rat_venv/bin/python`**
  (system python3 lacks deps). RAT tree: `export RAT_ROOT=/opt/runanything/src`.

## Already validated (context — no need to re-verify)
- run_v3 is wired to ratbench via a Dockerfile-emitting adapter (`multi_docker_eval_adapter.py`).
- A tomllib/py3.10 fix makes construction actually read declared deps (was silently reading zero).
- Construction-only mode: `V3_CONSTRUCTION_ONLY=1` → the adapter passes `--construction-only`
  (build graph WITH the LLM base-image + service/config classify, render the INITIAL setup.sh, SKIP
  the repair loop). mvt construction-only = **164/165** on both deepseek and MiniMax-M3.

## LAUNCH (detached — the batch is long; a foreground SSH will be SIGTERM'd)
Run this via ssh. It writes a launch script on the VM and starts it with `setsid` so it survives
the SSH session closing:

```bash
ssh -o ServerAliveInterval=15 root@167.233.64.96 'bash -s' <<'REMOTE'
cat > /opt/harness/run_construction_batch.sh <<'SH'
#!/bin/bash
set -a; source /opt/harness/.env; set +a
export RAT_ROOT=/opt/runanything/src
export V3_CONSTRUCTION_ONLY=1              # first-pass build script, no repair loop
export V3_ADAPTER_RUN_TIMEOUT=2400         # 40-min cap on each repo's construction
# --- MODEL: deepseek (default, faster). For MiniMax-M3 instead, uncomment the next two lines: ---
# export LLM_API_PROVIDER=minimax
# export MINIMAX_THINKING=disabled
cd /opt/harness
exec /opt/rat_venv/bin/python /opt/harness/bench john-planner-v3 \
  --tier all \
  --repos-json /opt/harness/datasets/rat_python_medlarge15.json \
  --llm deepseek/deepseek-v4-flash \
  --num-turn 30 --repair-mode runner \
  --concurrency 6 \
  --run-name construction-batch
SH
chmod +x /opt/harness/run_construction_batch.sh
LOG=/opt/harness/run_construction_batch.log; : > "$LOG"
setsid bash /opt/harness/run_construction_batch.sh > "$LOG" 2>&1 < /dev/null &
echo "$!" > /opt/harness/run_construction_batch.pid
echo "LAUNCHED pid=$! log=$LOG"
sleep 8; tail -8 "$LOG"
REMOTE
```

To run on **MiniMax-M3** instead of deepseek: uncomment the two `LLM_API_PROVIDER`/`MINIMAX_THINKING`
lines AND change `--llm deepseek/deepseek-v4-flash` → `--llm MiniMax-M3`. (MiniMax is ~40% slower.)

## MONITOR (the adapter CAPTURES run_v3's output, so the bench log is quiet during construction)
Watch these instead of the bench log:
- Live construction containers = it's working: `ssh root@167.233.64.96 'docker ps --format "{{.Names}} {{.Status}}" | grep depgraph-probe'`
- Per-repo artifacts appear when a repo finishes:
  `ssh root@167.233.64.96 'find /opt/runs/john-planner-v3/construction-batch-*/output -name run_pytest_results.json'`
- Process alive: `ssh root@167.233.64.96 'kill -0 $(cat /opt/harness/run_construction_batch.pid) && echo running || echo DONE'`
- Poll with a VM-side sleep so you don't burn cycles, e.g. `ssh ... 'sleep 300; <check>'` (Bash tool timeout ≥ 320000ms).
- 15 repos, 6 at a time, ~8–12 min each construction-only ⇒ roughly ~30–45 min total (deepseek).

## AGGREGATE the result (how many "initial success")
When `kill -0` says DONE, run this to count passes (real-success = pass_rate ≥ 0.8):

```bash
ssh root@167.233.64.96 '/opt/rat_venv/bin/python - <<PY
import json, glob, os
rb=sorted(glob.glob("/opt/runs/john-planner-v3/construction-batch-*"))[-1]
rows=[]
for f in glob.glob(rb+"/output/*/*/run_pytest_results.json"):
    repo="/".join(f.split("/output/")[1].split("/")[:2])
    s=json.load(open(f)).get("summary",{})
    tot,pas=s.get("total_tests",0),s.get("passed",0)
    rate=(pas/tot) if tot else 0.0
    rows.append((repo,tot,pas,s.get("failed",0),s.get("skipped",0),rate))
rows.sort(key=lambda r:-r[5])
ok=sum(1 for r in rows if r[5]>=0.8 and r[1]>0)
print(f"bucket: {rb}")
for repo,tot,pas,fl,sk,rate in rows:
    print(f"  {repo:45} {pas}/{tot} pass  fail={fl} skip={sk}  rate={rate:.2f}  {\"OK\" if rate>=0.8 and tot>0 else \"\"}")
print(f"\nINITIAL SUCCESS (pass_rate>=0.8): {ok}/{len(rows)} repos with results")
PY'
```

## Gotchas / expectations
- **Service/large repos fail — expected.** medlarge15 includes service repos (postgres-mcp) and native
  ones (slither→solc). In construction-only mode services are NOT provisioned (they render as commented
  `#@need` stubs; the repair loop provisions them, and it's skipped here), and the ratbench harness
  can't run live services anyway. Count them as expected 0s; the meaningful number is service-free repos.
- **`[repair] … No module named 'src.verification_bundle'`** in the log is NON-FATAL (a separate
  v3↔harness gap in the runner-repair path); it does not affect the pytest score.
- **Concurrency 6** = 6 concurrent Docker builds + 6 concurrent LLM call streams. Watch VM CPU/mem/Docker
  and, on MiniMax, rate limits (429s). If you see thrash or 429s, relaunch with `--concurrency 3`.
- Containers are UUID/auto-named, output dirs are per-repo → concurrency is safe (no collisions).
- Prune Docker between big runs: `ssh root@167.233.64.96 'docker image prune -f'`. Do NOT touch the
  user's pre-existing `depgraph-probe-*` containers that predate your run.

## Report back
Per-repo pass/total + the INITIAL-SUCCESS count (pass_rate≥0.8), which repos failed and why
(service/native vs a real construction gap), model + wall-clock. If you want a model comparison, re-run
the identical batch with the MiniMax lines enabled and diff the two.
