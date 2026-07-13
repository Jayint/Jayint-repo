# Handoff: Re-anchor ESSR/EBSR to the gold denominator (`rat_python50_gold.json`)

**Date:** 2026-07-12 · Consumer-side task (does NOT build the gold set — consumes it)
**You can start now.** The gold file lands when the build run finishes; until then, mock the schema below and wire the plumbing so it's a drop-in once the real file exists.

---

## 0. What you are doing (one sentence)

Change the ESSR/EBSR **denominator** from each agent's own pytest collection (floating) to the **fixed gold node-id set** produced by the gold-manifest builder, so a broken environment that collects fewer tests can no longer inflate its score.

## 1. Why this matters (the bug you are fixing)

ESSR/EBSR today divide by the *agent's own* collected/executed test count. That count is **environment-dependent**: an agent whose env fails to import half the suite collects fewer tests, shrinks its own denominator, and scores *higher*. The denominator floats with the very thing we're measuring.

The gold set fixes one repo → one **maximum cleanly-collectable** set of pytest node-IDs, certified independently at a pinned commit. That set becomes the denominator for **every** agent on that repo. Same denominator for everyone = a real head-to-head.

---

## 2. The file you consume: `rat_python50_gold.json`

Location (when the run finishes): `/opt/manifest_out_py50/rat_python50_gold.json`. One JSON object, keyed by `owner/repo`.

```json
{
  "dataset": "rat_python50",
  "pinned_from": "rat_python50_m3nothink_corrected",
  "generated_at": "2026-07-12T18:03:00Z",
  "summary": {
    "total": 50,
    "certified": 0,
    "rejected": 0,
    "error": 0,
    "total_gold_node_ids": 0
  },
  "repos": {
    "jhao104/proxy_pool": {
      "full_name": "jhao104/proxy_pool",
      "sha": "9cc0cad4c4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "status": "CERTIFIED",
      "manifest_size": 248,
      "node_ids": [
        "tests/api/test_proxy_api.py::TestAll::test_all_empty"
      ],
      "reject_reasons": [],
      "error": null,
      "base_image": "python:3.11-slim",
      "artifacts_dir": "/opt/manifest_out_py50/shardN/<slug>/<sha>/"
    }
  }
}
```

### Field contract (read this twice)

| Field | Type | Meaning / invariant |
|---|---|---|
| `dataset` | str | Always `"rat_python50"`. |
| `pinned_from` | str | The baseline run the SHAs were pinned to: `"rat_python50_m3nothink_corrected"` (RAT MiniMax-M3). |
| `generated_at` | ISO-8601 str | When the roll-up was written. |
| `summary.total` | int | Always 50. |
| `summary.certified/rejected/error` | int | Repo counts per status; sum == 50. |
| `summary.total_gold_node_ids` | int | Σ `manifest_size` over CERTIFIED repos (the aggregate denominator). |
| `repos` | object | Keyed by `"owner/repo"` (lowercase-safe; match case-insensitively). 50 entries. |
| `repos[k].full_name` | str | `"owner/repo"`. |
| `repos[k].sha` | 40-hex str | The pinned commit == the M3 baseline's `head_sha`. **This is your SHA-alignment key.** |
| `repos[k].status` | enum | `"CERTIFIED"` \| `"REJECTED"` \| `"ERROR"`. |
| `repos[k].manifest_size` | int | `== len(node_ids)`. `0` unless CERTIFIED. |
| `repos[k].node_ids` | list[str] | **THE GOLD DENOMINATOR.** Path-based pytest node-IDs (`relative/path.py::Class::test[param]`). `[]` unless CERTIFIED. |
| `repos[k].reject_reasons` | list[str] | Non-empty only when REJECTED (e.g. `["no items collected (hollow)"]`). |
| `repos[k].error` | str \| null | Exception string only when ERROR. |
| `repos[k].base_image` | str | Docker base the collection ran on. |
| `repos[k].artifacts_dir` | str | On-disk source of truth: holds `collected-nodeids.json` + `collection-certificate.json`. |

**Only `CERTIFIED` repos have a usable denominator** (`node_ids` non-empty). REJECTED/ERROR repos → no gold set → **exclude from the metric and log them** (see §5). Do not silently treat a missing denominator as 0/0.

---

## 3. What the gold set changes: JUST the denominator

Your existing ESSR/EBSR numerators stay conceptually the same. You own those formulas — the gold set only supplies (a) the fixed denominator and (b) the reference node-id set to intersect against.

For a CERTIFIED repo `r` and baseline agent `A`:

- **Denominator** `D_r = len(gold.repos[r].node_ids)` — same for every agent.
- **Reference set** `G_r = set(gold.repos[r].node_ids)`.

Two numerators (both intersect with `G_r` so an agent gets **zero credit** for tests outside the gold set — including tests gold itself rejected):

- **Collection / build coverage** (maps to EBSR-style — "did the env make the tests collectable"):
  `C_{A,r} = |collect(A,r) ∩ G_r| / D_r`
  where `collect(A,r)` = the node-ids agent A's environment collected (path-based; see §4 for where to read them).

- **Pass coverage** (maps to ESSR-style — "did the env make the tests green"):
  `P_{A,r} = |passed(A,r) ∩ G_r| / D_r`
  where `passed(A,r)` = the gold node-ids A ran **and passed**.

Both are in `[0, 1]` (intersection ≤ `D_r`, no clamp needed). Aggregate = **mean over the SHA-aligned CERTIFIED repos** the agent ran. Report the included/excluded repo counts alongside the mean.

> Map `C`/`P` onto your project's exact ESSR vs EBSR names before shipping — the two building blocks are "collectable coverage" and "pass coverage". The `÷exec` (paper) vs `÷all` (coverage-penalized) distinction still applies to the *numerator*; the gold set is what makes the `÷all` denominator honest and fixed.

### Worked example
`proxy_pool`: `D_r = 248`. Agent A collects 300 node-ids, 240 of which are in `G_r`, and 220 of those 240 pass.
- `C_{A,r} = 240/248 = 0.968`
- `P_{A,r} = 220/248 = 0.887`
The 52 extra tests A collected outside gold count for nothing. An agent whose broken env collected only 120 gold tests scores `C = 120/248 = 0.484` — it can no longer hide behind a shrunken denominator.

---

## 4. Where the baseline inputs live (the numerators)

Baseline runs on the VM under `/opt/runs/`. The canonical corpus is `rat_python50`. Runs to score:

| method | run dir |
|---|---|
| RAT-MiniMax-M3 (canonical) | `/opt/runs/baselines/rat_python50_m3nothink_corrected` |
| RAT-deepseek | `/opt/runs/baselines/rat_python50-20260704-170358` |
| repo2run-M3 | `/opt/runs/baselines/rat_python50_repo2run_m3nothink-20260705-162552` |
| v3-construction | `/opt/runs/john-planner-v3/construction-python50-20260707-072356` |
| v3-repair | `/opt/runs/john-planner-v3/repair-ablation-python50-c5` |

Per run:
- **Provenance / SHA:** `case_studies.jsonl` — one row per repo; `row["task"]["full_name"]` and `row["task"]["head_sha"]`. This is how you SHA-align (`head_sha == gold sha`).
- **Collected node-ids** (`collect(A,r)`): `<run>/output/<...>/run_pytest_collect_results.json` → `raw_output` (newline-delimited; keep lines containing `"::"`). **Already path-based** — same unit as gold.
- **Pass/fail** (`passed(A,r)`): the run-phase pytest report in the same output dir (the second of the two pytest JSONs each agent emits). **Confirm the exact filename in one baseline output dir before coding** — do not assume; grep for the report that carries per-node outcomes, then read passed node-ids from it. If pass-level data is missing for a method, compute only `C` (collection coverage) for it and note the gap.

Do NOT locate these via glob-and-guess — resolve each repo's output dir from its `case_studies.jsonl` provenance row (artifacts pointer), same as `tier1b.py` does.

---

## 5. Matching rules — get one wrong and every number is garbage

1. **Node-id normalization (both sides).** Strip leading `src/`, `./`, `/src/` before set ops so `src/pkg/test_x.py::t` and `pkg/test_x.py::t` match. Apply the *same* normalizer to gold and to baseline node-ids. (See `norm()` in `tier1b.py`.)
2. **SHA alignment is mandatory.** Only score repo `r` for agent `A` if `A`'s `head_sha == gold.repos[r].sha`. Expected aligned counts: **RAT-M3 50/50, repo2run-M3 42/50, RAT-deepseek 39/50, v3-construction ~20/37, v3-repair ~18/35.** Different SHA = different test set = incomparable; drop it and log it.
3. **Status filter.** Only CERTIFIED gold repos have a denominator. Skip REJECTED/ERROR gold repos entirely; report how many you skipped and which.
4. **Unit trap — do NOT cross-compare counts.** The baselines' `tests.total` fields came from **junit_xml** (classname-based: `tests.x::t`). Gold and the collect lists are **path-based** (`tests/x.py::t`). These never intersect. Use ONLY the saved path-based node-id lists for set math; ignore junit totals.
5. **No silent caps.** Every excluded repo (non-CERTIFIED gold, SHA-misaligned, missing collect file) must appear in the output with its reason. A mean over a silently-shrunk repo set is the exact bug we're removing.

---

## 6. Mock while you wait

The real file may not exist yet. Generate a conformant mock and develop against it — swap to the real path when the run finishes.

```python
# minimal mock — 2 repos, both CERTIFIED, one REJECTED shows the excluded path
mock = {
  "dataset": "rat_python50",
  "pinned_from": "rat_python50_m3nothink_corrected",
  "generated_at": "2026-07-12T00:00:00Z",
  "summary": {"total": 3, "certified": 2, "rejected": 1, "error": 0, "total_gold_node_ids": 5},
  "repos": {
    "jhao104/proxy_pool": {"full_name": "jhao104/proxy_pool", "sha": "a"*40,
      "status": "CERTIFIED", "manifest_size": 3,
      "node_ids": ["tests/t_a.py::t1", "tests/t_a.py::t2", "tests/t_b.py::t3"],
      "reject_reasons": [], "error": None, "base_image": "python:3.11-slim",
      "artifacts_dir": "/opt/manifest_out_py50/shard1/jhao104__proxy_pool/"+"a"*40+"/"},
    "microsoft/markitdown": {"full_name": "microsoft/markitdown", "sha": "b"*40,
      "status": "CERTIFIED", "manifest_size": 2,
      "node_ids": ["tests/t_c.py::t1", "tests/t_c.py::t2"],
      "reject_reasons": [], "error": None, "base_image": "python:3.11-slim",
      "artifacts_dir": "/opt/manifest_out_py50/shard2/microsoft__markitdown/"+"b"*40+"/"},
    "some/testless": {"full_name": "some/testless", "sha": "c"*40,
      "status": "REJECTED", "manifest_size": 0, "node_ids": [],
      "reject_reasons": ["no items collected (hollow)"], "error": None,
      "base_image": "python:3.11-slim", "artifacts_dir": "/opt/manifest_out_py50/shard3/some__testless/"+"c"*40+"/"},
  },
}
```

---

## 7. Deliverable

A metric module/script that, given `rat_python50_gold.json` + the five baseline run dirs, emits per method:
- mean `C` (collection coverage) and mean `P` (pass coverage) over SHA-aligned CERTIFIED repos,
- the per-repo breakdown (repo, gold sha, `D_r`, collected∩gold, passed∩gold, `C`, `P`),
- the excluded list with reasons (non-CERTIFIED / SHA-misaligned / missing data),
- N included and N excluded, so the mean is never read as "all 50".

Read `tier1b.py` (SHA-aligned node-id intersection, `norm()`, provenance-based output-dir resolution) in `/opt/manifest_out_hardsubset/` as the reference implementation for the collection-coverage (`C`) half — extend it with the pass-coverage (`P`) half.

## 8. Landmines (from the build session)
- Write `.py` files and `rsync` them; never inline python-over-ssh with quotes in f-strings/heredocs.
- Dedup any `corpus_results.jsonl`-style appends by `(repo_url, sha)` last-wins.
- Case-insensitive repo keys.
- Confirm the exact pass/fail report filename before trusting §4; verify against one real baseline output dir.
