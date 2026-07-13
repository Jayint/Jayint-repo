# Handoff: finish the gold-set tail, then run the v3 pinned re-run

**As of 2026-07-12 20:31 UTC.** Run this in a fresh session. VM: `ssh root@167.233.64.96`.

---

## 0. Where things stand RIGHT NOW

**Gold set (`rat_python50`, pinned to RAT-MiniMax-M3's per-repo `head_sha`): 45/50 CERTIFIED.**

```
still building : mlflow (shard1, 1h06m), tinygrad (shard4), ezdata (queued behind mlflow)
done/idle      : shard2, shard3
disk           : 19G free (92%)  <-- TIGHT
diskguard      : ALIVE (floor 30G, corrected cleaners)
```

| status | repos |
|---|---|
| CERTIFIED | 45 (incl. 4 preseeded reuse) |
| **ERROR — owed a retry** | **posthog**, **checkmk** (both died to MY cleanup bugs, never on merit) |
| building | mlflow, tinygrad, ezdata |

**Everything is backed up 3×**: live `/opt/manifest_out_py50/`, VM snapshot `/opt/manifest_out_py50_pass1_backup`, and off-VM at `docs/superpowers/artifacts/gold/`.

---

## 1. TASK A — monitor the tail to completion

```bash
ssh root@167.233.64.96 'cd /opt/manifest_builder && bash py50_status.sh'   # full status
ssh root@167.233.64.96 'cd /opt/manifest_builder && python3 tail_state.py' # per-repo tail state
```
A shard showing **DEAD is usually DONE** (it finished its subset and exited). Confirm with:
```bash
ssh root@167.233.64.96 'cd /opt/manifest_builder && python3 pending_by_shard.py'  # flags true ORPHANS
```

**Disk is the live risk.** If free drops toward ~12G, throttle: kill the heaviest build (mlflow) and let it run solo later. That playbook worked cleanly before (`abort_posthog.sh` is the template). **Do NOT** hand-delete "terminal" images/workspaces — see Landmines.

---

## 2. TASK B — the two owed retries (posthog + checkmk)

Both ERRORed because of **my** cleanup bugs, not their own merit:
- posthog: workspace deleted mid-**certify**, then its clone deleted mid-**`git clone`** (exit 128).
- checkmk: same clone-deletion bug.

**posthog is the big prize: it demonstrably collects 77,642 node-ids with ZERO import_skipped.** Its only real blocker was the *node-id stability* gate (the two collection runs disagreed; it was mid-debug on `PYTHONHASHSEED` when the 90-min cap hit).

Run them **solo on a free box** (they are the two biggest clones: ~5.9GB / ~6.5GB — running them concurrently with other builds is what caused the disk crashes):
```bash
# after the tail finishes AND disk is healthy
ssh root@167.233.64.96 'bash /opt/manifest_builder/posthog_retry.sh'   # has safety gates built in
```
`posthog_retry.sh` verifies (a) no live shard owns posthog → no docker-tag collision, (b) shard3 idle. It runs opus, attempts=2, **3h/attempt** (posthog needs uninterrupted time, not a restart-from-seed).
For checkmk, copy the script and swap the repo (note: checkmk failed on merit earlier too — it kept modifying **protected files** to fix collection errors, which auto-rejects; it may legitimately REJECT).

**Encouraging precedent:** Scrapling REJECTED twice, then **CERTIFIED on the third pass (768 node-ids)**. A twice-failed repo can still certify.

---

## 3. TASK C — regenerate the final gold JSON

```bash
ssh root@167.233.64.96 'cd /opt/manifest_builder && python3 build_gold_json.py'
# -> /opt/manifest_out_py50/rat_python50_gold.json   (flips "partial": false when 0 PENDING)
```
Schema is documented in `docs/superpowers/handoffs/2026-07-12-gold-set-schema-for-essr-ebsr.md`.
Per repo: `{full_name, sha, status, manifest_size, node_ids, reject_reasons, error, base_image, artifacts_dir}`.
`node_ids` = path-based pytest node-ids = **THE gold denominator**.

---

## 4. Tier-1 results so far (context — already done)

Scored with `tier1_score.py` (per-method) and `tier1_common.py` (fair head-to-head).

**Common subset (12 repos, all 3 measurable):**
| method | EBSR (collect) | ESSR (pass) |
|---|---|---|
| **RAT-M3** (canonical) | **0.908** | **0.774** |
| repo2run-M3 | 0.784 | 0.675 |
| v3-construct | 0.763 | 0.653 |

⚠️ **Do not over-read this.** 6 of the 12 repos are saturated ties (~1.000). The whole spread comes from **feast**, **podman-compose**, **tgo**. And the repo2run-vs-v3 ordering **flips** when the subset changes 10→12 → that gap is **noise**; only RAT's lead is robust.

**Why only 12 comparable?** NOT commit drift — **missing pytest output**:
| method | OK | missing data | provenance gap |
|---|---|---|---|
| RAT-M3 | 26 | **17** | 0 |
| repo2run-M3 | 23 | **18** | 2 |
| v3-construct | 23 | 5 | **14** |

🔴 **The ceiling: `RAT-OK ∩ repo2run-OK = 16 repos`.** So even a *perfect* v3 re-run moves comparable from **14 → 16**. Only **Tier-2** (rebuild every agent's env and run pytest ourselves) unlocks the rest (~42).

---

## 5. THE PLAN — v3 pinned re-run

### Why: commit pinning does not exist anywhere in the v3 path
| fact | evidence |
|---|---|
| dataset has **no commit field** | `/opt/harness/datasets/rat_python50.json` keys: `full_name, clone_url, default_branch, …` |
| `head_sha` is an **output**, not an input | `run_rat_benchmark.py:183` records what the agent *got* |
| deployed v3 agent has **no pinning** | `/opt/agents/john-planner-v3` @ `bedf97cc`; `grep pin_sha` → nothing |
| v3's Dockerfile clones HEAD | `git clone --depth=1 <url> /testbed`, no checkout |

→ v3's 12 drifted + 11 null-SHA repos are a **capability gap**, not a config mistake.

**And the drift is NOT ignorable** — I checked every drifted repo at the node-ID level:
> **0 of the 8** v3-drift repos are salvageable. **Every one added test functions** — even `azure-cli` at just **4 commits** apart (+1 test), and `frappe/press` at 26 commits with 1 test file (+3 tests). Active repos churn tests constantly; you cannot compare across commits.
>
> (Contrast: the 4 I *did* clear — Scrapling, ingestr, copier, vizro — touched **zero** test files, incl. ingestr at 29 commits. The rule is **zero test churn**, not "small drift".)

**Already recovered without a re-run (+9):** 4 zero-churn drift repos, plus **5 null-SHA repos whose inferred run-time HEAD is byte-identical to gold** (Archipelago, EvalAI, DDNS, synthetic-data-generator, explainshell — v3 *did* build gold's commit, it just never recorded it).
**v3 usable today: 29/43. Only the remaining 14 get re-run.**

### The re-run set — exactly these 14 repos

Regenerate any time with `python3 /opt/manifest_builder/v3_rerun_set.py` → also writes `/opt/manifest_builder/v3_rerun_set.json` (`{rerun: [...], usable: [...], gold_shas: {...}}`), which is the input for Step 3.

| repo | why | gold sha |
|---|---|---|
| `aiidateam/aiida-core` | DRIFT | `9cff5ffe` |
| `Azure/azure-cli` | DRIFT | `889dcca3` |
| `baserow/baserow` | DRIFT | `0621bcde` |
| `django-oauth/django-oauth-toolkit` | DRIFT | `74b10062` |
| `frappe/press` | DRIFT | `73a2a411` |
| `GoogleCloudPlatform/PerfKitBenchmarker` | DRIFT | `bd08f2d5` |
| `rq/rq` | DRIFT | `eacec8ff` |
| `wecode-ai/Wegent` | DRIFT | `f296d388` |
| `Donkie/Spoolman` | NULL-SHA | `eafbc649` |
| `mozilla/addons-server` | NULL-SHA | `6ff48bc8` |
| `OpenCTI-Platform/connectors` | NULL-SHA | `0de8b827` |
| `polarsource/polar` | NULL-SHA | `97e94309` |
| `python-websockets/websockets` | NULL-SHA | `ff4869ba` |
| `Qiskit/qiskit` | NULL-SHA | `655dfbbd` |

The other **29 are usable as-is** and must NOT be re-run — 20 already SHA-aligned, 4 zero-test-churn drift, 5 null-SHA-but-inferred-identical.

### ⚠️ Consistency risk: the harness working tree is DIRTY
`/opt/harness` HEAD == `c3dcaed` (the run's `harness_commit` ✅) **but the tree is modified**:
`agent.py` +29, `run_rat_benchmark.py` +57 (`MM`), `varieties.toml`, `repo2run_model.py`, new `meter_rat_tokens.py` / `zero_rat_temperature.py`.
The v3 **agent** is clean at `bedf97cc` = the run manifest's `agent_commit` ✅.

### Steps

**Step 1 — does the harness diff even touch v3?**
```bash
ssh root@167.233.64.96 'git -C /opt/harness diff c3dcaed -- agent.py run_rat_benchmark.py'
```
Inspect only the `dockeragent` path. Most dirty files are RAT/repo2run-specific.
- untouched → run with the current tree.
- touched → run from a **clean `c3dcaed` checkout**. **Do NOT stash the user's uncommitted work.**

**Step 2 — add pinning (the only real code change).**
- **Dataset:** use `/opt/manifest_builder/datasets/rat_python50.pinned.json` (already carries gold's 50 SHAs).
- **Runner:** thread `commit` → agent (today it only *records* `head_sha`).
- **Agent (`v3-core @ bedf97c`), Dockerfile renderer** — replace the bare shallow clone with a pinned fetch (`--depth=1` **cannot** check out an arbitrary SHA):
```dockerfile
RUN git init /testbed && cd /testbed \
 && git remote add origin <url> \
 && git fetch --depth 1 origin <GOLD_SHA> \
 && git checkout --detach FETCH_HEAD
```
This changes **only which code v3 sees**, not how it constructs the env → stays a fair re-run.

**Step 3 — re-run the 14 only.**
Corpus = the 14 rows above (`v3_rerun_set.json` → build a `rat_python50.v3rerun.json` dataset carrying `commit` per repo). Match the original manifest exactly so the 14 stay comparable to the kept 29: `variety=john-planner-v3, model=dockeragent, concurrency=3, num_turn=30`, agent pinned at `bedf97cc` + the Step-2 pinning patch, **fresh output dir**. Then merge: **29 kept + 14 fresh = 43**.

**The one thing to be honest about:** the merged v3 number mixes a *pinned* checkout (the 14) with an *unpinned* one that happened to land on gold's commit (the 29). That is defensible — Step 2 changes **only how `/testbed` gets its commit**, not a single line of how v3 constructs the environment — but say it out loud in the write-up rather than letting a reviewer find it.
**Cheap insurance (recommended):** add **2–3 of the already-aligned 29** (e.g. `fastapi/typer`, `containers/podman-compose`) to the re-run corpus as **controls**. If the pinned path reproduces their existing result, the merge is empirically justified; if it doesn't, the pinning patch changed behaviour and you'd want to know *before* publishing. Score the controls from the ORIGINAL run — they exist only to validate the merge.

**Step 4 — verify + re-score.**
1. Assert `head_sha == gold sha` for **14/14** (the check that was never possible before). Any repo that still drifts = the pinning patch didn't take; do not paper over it.
2. Controls reproduce their original EBSR/ESSR → merge is sound.
3. Merge the 14 fresh outputs with the 29 kept ones and re-run `tier1_common.py`.

**Reality check on the payoff:** this fixes v3's *provenance* gap, not the *missing-pytest-output* gap. Per §4, `RAT-OK ∩ repo2run-OK = 16`, so the comparable set moves **14 → 16 at best**. Worth doing (it's cheap and it's a correctness bug), but it is **not** what unlocks the full 43 — only Tier-2 is.

### Sequencing
Run **after** the gold tail finishes **and after Phase 0 disk prep**. Scoping to 14 (+~3 controls) instead of 43 cuts the docker-build load by ~⅔ — but it still includes **Qiskit** and **addons-server**, two of the heaviest repos in the corpus, and the box is at 19G. Do not skip Phase 0.

---

## 6. PHASE 0 — disk prep (do this regardless)

🔑 **There is an idle 147 GB volume**: `/mnt/HC_Volume_105930614` — **147G, 28K used**, mounted but **NOT used by Docker** (`Docker Root Dir: /var/lib/docker` on the full root disk). **Every disk-full incident happened while this sat empty.**

`diskguard.log` shows free disk hit **`0G` forty-seven times** during this run.

1. Relocate Docker storage (or at least build scratch) onto the 147G volume → requires a daemon restart, so do it **after the tail finishes**.
2. Sweep genuinely-stale `/tmp` dirs (`clonetest`, `testenv`, `dltest`, `repotest`, …).
   ⚠️ **`/tmp/cc-home-1..4` are the LIVE shard HOME dirs — do NOT delete while shards run.** (A subagent misidentified them as clutter; deleting them would kill the run.)

After this: safe concurrency goes N=2 → **N=4–6**.

---

## 7. LANDMINES (each of these already bit us)

1. **Only a CERTIFIED repo is safe to clean up.** A REJECTED/ERROR row may be **retried**, and an in-progress/certifying repo has **no row at all**. Treating "has any `corpus_results` row" as done deleted posthog's workspace mid-certify AND its clone mid-`git clone`. `clean_wt.py` / `clean_images.py` are now CERTIFIED-only — **keep it that way.**
2. **`pkill -f` self-kill.** Bracketed regex (`[d]iskguard`) still self-matches if your *command line contains the literal string* elsewhere. **Run kills from a script FILE**, never inline over ssh.
3. **Never inline python-over-ssh** with quotes/f-strings — it breaks constantly. Write a `.py`, `rsync`, run.
4. **junit ≠ node-ids.** Baseline `summary.total_tests` is classname-based (Archipelago junit = **236,474** vs gold **20,943**). NEVER intersect or divide by it. Use only path-based node-id sets.
5. **pytest has TWO verbose layouts** — standard (`nodeid PASSED [ 0%]`) and **xdist** (`[gw1] [ 0%] PASSED nodeid`). Parsing only one silently yields **ESSR=0** for every xdist repo.
6. **Never score missing data as 0** — exclude it and report the count. (websockets/darts have no run file; DDNS's collect output is *console text*, not a node-id list — its real EBSR is 1.000, not 0.)
7. **Preseeded certs carry a `.git`-suffixed `repo_url`** — normalize when matching "already certified", or you'll rebuild them and create a **second, divergent cert for the same repo**.
8. **Preseed placement is shard-count-specific** (`idx % N + 1`). Changing N requires re-seeding.
9. **`build_one` leaks** its `/tmp/manifest-wt-*` workspace and the agents' throwaway docker images (`wgtest`, `feast-final`, …) — the guard compensates; the real fix belongs in the module.

---

## 8. Script inventory (all on VM at `/opt/manifest_builder/`)

| script | purpose |
|---|---|
| `py50_status.sh` | full run status |
| `tail_state.py` | per-repo state of the tail corpus |
| `pending_by_shard.py` | detects true ORPHANS (pending on a dead shard) |
| `build_gold_json.py` | Step-C consolidated gold JSON |
| `tier1_score.py` | per-method EBSR/ESSR (handles xdist, console-collect, missing-data) |
| `tier1_common.py` | fair head-to-head on the common subset |
| `coverage_matrix.py` | per-repo × per-method: *why* each repo is excluded |
| `full_drift_report.py` | SHA drift + test/dep-manifest churn |
| `nodeid_drift.py` | did drift actually change NODE-IDs? |
| `v3_rerun_set.py` | **the 14 to re-run vs the 29 to keep** → `v3_rerun_set.json` |
| `clean_wt.py` / `clean_images.py` | **CERTIFIED-only** reclaimers |
| `diskguard.sh` | guard v3 (floor 30G) |
| `posthog_retry.sh` | solo retry w/ collision safety gates |
| `abort_posthog.sh` | throttle template when disk crashes |

**Key paths:** gold `/opt/manifest_out_py50/rat_python50_gold.json` · pinned corpus `/opt/manifest_builder/datasets/rat_python50.pinned.json` · baselines `/opt/runs/baselines/{rat_python50_m3nothink_corrected, rat_python50_repo2run_m3nothink-20260705-162552}`, `/opt/runs/john-planner-v3/construction-python50-20260707-072356` · harness `/opt/harness` (DIRTY) · v3 agent `/opt/agents/john-planner-v3` @ `bedf97cc`.

**Do not touch `/opt/runs`** (75G, other people's benchmark data).
