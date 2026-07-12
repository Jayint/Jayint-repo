# Handoff: Build the gold set on `rat_python50` for a fair ESSR comparison

**Date:** 2026-07-12 · Module: `src/manifest_builder/` (branch `john-v3-multi-lang`, ≥ `3b691ea`)
**Run this in a fresh session.** Everything below is verified against the VM as of this date.

---

## 0. Why this exists (context)

We built a gold pytest-collection manifest (the fixed ESSR denominator) — **43/50 CERTIFIED, 46,133 node-ids** — but on the **wrong corpus**. There are two confusingly-named "python-50" datasets:

- `rat_python_hard_subset.pinned.json` (pinned 2026-07-11) — what **our gold** ran on.
- `rat_python50.json` (2026-07-04) — what **every baseline** ran on (RAT-deepseek, RAT-MiniMax-M3, repo2run-M3, v3-construction, v3-repair).

They share only **18 repos**, so gold-vs-baselines was limited to ~17 repos with heavy SHA drift. **Fix: rebuild the gold set on `rat_python50`** (the one corpus all baselines used) at the baselines' commits, so the comparison becomes a real head-to-head.

## Locked decisions (from the user)
1. **Canonical baseline = RAT MiniMax-M3** (`rat_python50_m3nothink_corrected`). Pin gold to its per-repo `head_sha`.
2. **Strict SHA-aligned reuse** — reuse an existing gold manifest only when its sha == the M3 sha (fully fair). Verified reuse = **4 repos**; build the other **46**.
3. **All 50 repos.**
4. **Delete the hard-subset dataset files**, but first **archive them + preserve the 43 done manifests** (do not orphan them — "might be useful later").

## Verified facts (baked in — do not re-derive)
- Canonical M3 run has `head_sha` for **all 50** repos.
- Cross-run SHA agreement vs the M3 canonical (this is the *fairness scope* of the final comparison):
  | method | repos it ran | matches M3 sha |
  |---|---|---|
  | RAT-MiniMax-M3 (canonical) | 50 | 50/50 (by definition) |
  | repo2run-M3 | 50 | **42/50** |
  | RAT-deepseek | 50 | **39/50** |
  | v3-construction | 37 | 20/37 |
  | v3-repair | 35 | 18/35 |
- **Reuse = 4 repos** whose existing gold sha already == the M3 sha: `beehiveinnovations/pal-mcp-server`, `jhao104/proxy_pool`, `microsoft/markitdown`, `pre-commit/pre-commit`. → **build 46 / 50**.
- Baselines with saved node-id lists (for Tier-1 comparison later): RAT-deepseek 36, RAT-M3 34, repo2run-M3 27, v3-c 37, v3-r 36.

---

## Environment (VM `root@167.233.64.96`, x86_64, Docker)
- Module: `/opt/manifest_builder` (rsync target; NOT git-tracked on the box). Local repo: `/Users/john/john-v3-multi-lang`.
- Agent: Claude Code at `/opt/node/bin/claude`; auth `CLAUDE_CODE_OAUTH_TOKEN` in `/opt/harness/.env` (refresh with `ssh -t … 'PATH=/opt/node/bin:$PATH claude setup-token'` if it 401s).
- Wrapper `/opt/manifest_builder/run_env.sh` sets PATH + `IS_SANDBOX=1` (needed for `--dangerously-skip-permissions` as root) + token.
- Launcher `/opt/manifest_builder/launch_concurrent.sh <N> <model> <attempts> <out_base> [extra]` — pass `--corpus <path>` in `[extra]` to override the corpus (argparse last-wins). Sets per-worker `HOME=/tmp/cc-home-<i>` (isolates `~/.claude`).
- **Fixes already committed + synced** (do NOT need redoing): `1440233` (`--shard`/`--cleanup-images`), `2a3bc47` (symlink/gitlink IsADirectoryError), `47f826a` (agent-timeout bytes crash), `3b691ea` (`MANIFEST_AGENT_TIMEOUT`). Full suite 73 green. **Re-sync before running:** `rsync -a src/manifest_builder root@167.233.64.96:/opt/manifest_builder/src/`.

---

## Step 1 — Archive & preserve (do BEFORE deleting anything)

Preserve the 43 hard-subset manifests + their corpus provenance so nothing is orphaned:
```bash
ssh root@167.233.64.96 'bash -lc "
  cp /opt/manifest_builder/datasets/rat_python_hard_subset.pinned.json \
     /opt/manifest_out_opus/hard_subset.retired.json   # provenance of the 43 done
  mv /opt/manifest_out_opus /opt/manifest_out_hardsubset   # archive the 43 gold manifests, clearly labeled
"'
```
The 43 manifests now live under `/opt/manifest_out_hardsubset/shard*/<slug>/<sha>/` — untouched, clearly named, referenced by `hard_subset.retired.json`.

## Step 2 — Delete the confusing hard-subset dataset files
```bash
# Local repo (both git-tracked -> git rm + commit on the shared branch, own files only):
cd /Users/john/john-v3-multi-lang
git rm datasets/rat_python_hard_subset.json datasets/rat_python_hard_subset.pinned.json
# Repoint the launcher default so it no longer references the deleted file:
#   edit launch_concurrent.sh: CORPUS=datasets/rat_python50.pinned.json
git add scripts_or_path/launch_concurrent.sh   # if launcher is tracked; else edit on VM only
git commit -m "chore(manifest_builder): retire rat_python_hard_subset datasets (use rat_python50)"
# VM copy:
ssh root@167.233.64.96 'rm -f /opt/manifest_builder/datasets/rat_python_hard_subset.pinned.json'
# VM launcher default -> rat_python50.pinned.json (sed the CORPUS= line)
```
Safe: the 43 manifests don't depend on the corpus file; only the launcher default referenced it.

## Step 3 — Generate the pinned corpus `rat_python50.pinned.json` (M3 SHAs)
```bash
ssh root@167.233.64.96 'python3 - <<PY
import json
run="/opt/runs/baselines/rat_python50_m3nothink_corrected"
repos=[]
for l in open(f"{run}/case_studies.jsonl"):
    r=json.loads(l); t=r.get("task",{})
    fn,sha=t.get("full_name"),t.get("head_sha")
    if fn and sha:
        repos.append({"full_name":fn,"commit":sha,"clone_url":f"https://github.com/{fn}"})
out={"dataset":"rat_python50","pinned_from":"rat_python50_m3nothink_corrected","repos":repos}
json.dump(out, open("/opt/manifest_builder/datasets/rat_python50.pinned.json","w"), indent=1)
print("pinned", len(repos), "repos")
PY'
```
Expect `pinned 50 repos`. (Also copy it into the local repo `datasets/` and commit if you want it tracked.)

## Step 4 — (optional) Pre-seed the 4 reusable manifests
Round-robin shard = index in the pinned list `% N`. This script copies each of the 4 reusable manifests from the archived hard-subset gold into the matching new shard dir so the resumable run auto-skips them. **Skipping this step just rebuilds those 4 (~40 min extra) — harmless.**
```bash
# Pseudocode to implement in the new session (write as a .py, rsync, run):
#  N = 3
#  pin = load rat_python50.pinned.json["repos"]
#  reuse = {"beehiveinnovations/pal-mcp-server","jhao104/proxy_pool","microsoft/markitdown","pre-commit/pre-commit"}
#  for i, repo in enumerate(pin):
#      if repo["full_name"] in reuse:
#          shard = i % N + 1
#          src = glob /opt/manifest_out_hardsubset/shard*/<slug>/<repo["commit"]>/   # slug = repo_slug(clone_url)
#          dst = /opt/manifest_out_py50/shard{shard}/<slug>/<commit>/
#          copytree(src, dst)
#  (slug via src.manifest_builder.workspace.repo_slug)
```

## Step 5 — Run the gold builder on `rat_python50`
Fresh out dir `/opt/manifest_out_py50` (keeps hard-subset gold separate).
```bash
# re-sync module first (see Environment). Start the disk guard (15G floor) as in prior runs.
ssh -o ServerAliveInterval=30 root@167.233.64.96 \
  'MANIFEST_AGENT_TIMEOUT=5400 bash /opt/manifest_builder/launch_concurrent.sh \
     3 opus 2 /opt/manifest_out_py50 \
     --corpus /opt/manifest_builder/datasets/rat_python50.pinned.json'
```
- **N=3 shards, opus, attempts=2, 90-min agent timeout** (rat_python50 has its own heavy repos — checkmk, azure-cli, baserow, perfkitbenchmarker, slither — 5400s gives them a real shot without the 2h waste).
- Resumable: skips the 4 pre-seeded (or rebuilds them if you skipped Step 4). Builds ~46.
- **Disk guard REQUIRED** (build cache is the hog): background loop pruning `docker builder prune -af` when free < 15G. Free disk first: `docker builder prune -af` (recovers ~60G).
- **Expect a heavy tail** like last time. The timeout-crash fix (`47f826a`) means heavy repos now soft-fail into REJECTED instead of ERROR. If some still can't build in 90 min, do a targeted 2h-timeout retry of just those (see the hard-subset run's playbook: filtered corpus + `MANIFEST_AGENT_TIMEOUT=7200 … attempts 1`).

## Step 6 — Emit the consolidated gold JSON
Write `build_gold_json.py` (schema below) → `/opt/manifest_out_py50/rat_python50_gold.json`. Dedup by `(repo_url, sha)` last-wins (resumable appends duplicate rows).

## Step 7 — Fair comparison (Tier 1, node-id intersection)
For each baseline method, parse its saved node-ids (`<run>/output/<...>/run_pytest_collect_results.json → raw_output`), intersect with gold `node_ids`, score `|collected ∩ gold| / |gold|` per repo, **only for repos where baseline `head_sha == gold sha`** (now ~50/50 for RAT-M3, 42 for repo2run-M3, 39 for RAT-deepseek). Mean the per-repo scores = the honest ESSR. Reuse the logic in `tier1b.py` (in `/opt/manifest_out_hardsubset/`, or rewrite).

---

## Output schema — `rat_python50_gold.json` (for a mock/parallel agent)

A downstream agent can mock exactly this and work in parallel during the run:
```json
{
  "dataset": "rat_python50",
  "pinned_from": "rat_python50_m3nothink_corrected",
  "generated_at": "<ISO-8601>",
  "summary": { "total": 50, "certified": 0, "rejected": 0, "error": 0, "total_gold_node_ids": 0 },
  "repos": {
    "jhao104/proxy_pool": {
      "full_name": "jhao104/proxy_pool",
      "sha": "9cc0cad4c4…",                 // 40-hex; == the M3 pinned commit
      "status": "CERTIFIED",                 // enum: "CERTIFIED" | "REJECTED" | "ERROR"
      "manifest_size": 248,                  // int; == len(node_ids); 0 unless CERTIFIED
      "node_ids": [                          // THE GOLD SET; [] unless CERTIFIED
        "tests/api/test_proxy_api.py::TestAll::test_all_empty"
      ],
      "reject_reasons": [],                  // [] unless REJECTED, e.g. ["no items collected (hollow)"]
      "error": null,                         // null unless ERROR (exception string)
      "base_image": "python:3.11-slim",
      "artifacts_dir": "/opt/manifest_out_py50/shardN/<slug>/<sha>/"
    }
    // … 50 entries keyed by "owner/repo"
  }
}
```
**Field contract:** `status` ∈ {CERTIFIED, REJECTED, ERROR}; `node_ids` = path-based pytest node-ids (`relative/path.py::Class::test[param]`) and IS the gold denominator; `manifest_size == len(node_ids)`; only CERTIFIED repos have non-empty `node_ids`. Source of truth per repo stays as `collected-nodeids.json` + `collection-certificate.json` on disk; this file is the roll-up.

---

## Gotchas / landmines (learned the hard way this session)
- **Inline python-over-ssh with `\"…\"` in f-strings/heredocs breaks** — always write a `.py`, `rsync` it, run it.
- **Resumable runs append duplicate rows** to `corpus_results.jsonl` → always **dedup by `(repo_url, sha)` last-wins** when counting/aggregating.
- **Disk**: build cache is the hog (one prune freed 52–64 GB). `--cleanup-images` only removes `manifest-<slug>` images, not build cache or **agent-created stray images/containers** (probe/test images) — prune periodically.
- **`launch_concurrent.sh` writes to `<out_base>/shard{1..N}/`**; status/aggregation scripts must glob `shard*`, not hard-code `(1,2,3)`.
- **Node-id units**: gold = path-based (`x.py::test`); baseline `tests.total` came from **junit_xml** (classname-based) — do NOT compare those counts. Tier-1 uses the *saved node-id lists* (`run_pytest_collect_results.json`), which ARE path-based, and intersects them — that's the fair method.
- **SHA drift is the core issue** — always pin the corpus to the canonical baseline's `head_sha` and only compare SHA-aligned repos.
- The 4 hard-subset-heavy repos (nexent, feast, karaoke-gen, docling) are **NOT in rat_python50** — they won't appear here.

## Reusable helper scripts (currently under `/opt/manifest_out_hardsubset/`)
`opus_status.py`, `final_tally.py`, `write_index.py` (dedup by repo), `tier1b.py` (SHA-aligned node-id intersection), `verify_plan.py` (SHA agreement + reuse). Repoint their `BASE` to `/opt/manifest_out_py50`.
