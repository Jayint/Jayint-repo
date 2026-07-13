# Can SWE-smith supply a fixed gold test manifest for environment-setup scoring?

**Status:** investigation complete, feasibility CONFIRMED with two end-to-end pilots.
**Date:** 2026-07-11. **All heavy work ran on VM `167.233.64.96` (x86_64, 30 GB)** — the
dataset math and Docker pilots do **not** run on the Mac (an earlier local run OOM'd building
per-image test-set unions; the rewrite streams shard-by-shard).
**Context:** direct follow-up to `2026-07-10-essr-denominator-dilemma-handoff.md`, whose open
question #1 names SWE-bench `PASS_TO_PASS` as the candidate for a stable `N_verified`.

---

## 0. Verdict

**SWE-smith can supply a fixed, reproducible, execution-validated gold test set — but as a
per-repo LOWER BOUND, not a complete suite, and only for the ~53–85 % of repos that run their
whole suite.** Concretely:

- The published `PASS_TO_PASS ∪ FAIL_TO_PASS` (call it `G`) **is reproducible**: on a pristine
  reference image, both pilots collected and **passed 100 % of `G`** (`manifest_collection_recall
  = manifest_pass_recall = 1.0`). This is exactly the narrow claim the task authorised:
  *"the candidate environment reproduces tests known to pass in SWE-smith's reference environment."*
- `G` is **NOT the full repo suite**. iniconfig ships `|G|=28` but the pristine image collects
  **42** passing tests; the 14 missing are **all** parametrized IDs containing a **space**
  (`test_tokenize[blank line]`), dropped by SWE-smith's `^(\S+)\s+STATUS` log parser. So `|G|`
  is a floor, never "N_verified = all tests."
- For **~47 % of repos** (`min_testing=True`: pandas, dask, moto, sqlfluff, mypy, dvc, …) `G`
  is **bug-scoped per task** and varies wildly (pandas: 4 … 22,028 tests, zero universal
  intersection). For those repos there is **no** single repo-wide gold set in the published data.

**Usable as:** a fixed lower-bound denominator for `manifest_collection_recall` /
`manifest_pass_recall` on the **70 stable** (byte-identical) repos, extendable to **111** if you
take each repo's largest manifest as the reference. **Not usable as** a whole-suite `N_verified`,
and not usable at all for the 20 bug-scoped repos.

---

## 1. Pinned artifacts (Task 2)

| artifact | pin | notes |
|---|---|---|
| SWE-smith repo | `9b74ac08118a85c39c356802f7961893af73e07f` (`main`) | 2026-03-21, "Add PHP language support #233" |
| HF dataset `SWE-bench/SWE-smith-py` | `77cab9055d42ab4a5c25c89a8f937096db13558e` (`main`) | parquet convert `3d4abd9862047f1863a4c140b91ee10acf37f01c` |
| iniconfig image | `sha256:d737c0b8e456fffbef75601a79b820c6f7bda4191c416afd173c7c233310a2cd` | single-arch amd64, ~4.66 GB on disk |
| tomli image | `sha256:a804c30560d7f27c7f9b20b76cf87deb425d1cf131267a5b6f52700969a066d3` | single-arch amd64 |

**Image tags are NOT immutable** — every image has only the `latest` tag (registry query), so a
tag can be overwritten. **Digests are immutable and content-addressed → pin by digest.** Images
are single-arch `linux/amd64` (plain v2 manifest, no multi-arch index): they only run natively on
x86_64, which is why this work is on the VM, not the arm64 Mac.

**Version discrepancy vs the paper:** the arXiv release (2504.21798) describes an earlier corpus;
the current `main` dataset is **50,908 rows over 131 repos** and includes PHP/JS/etc. profiles
added after the paper. We pin the dataset revision above; do not cite paper-era counts.

---

## 2. Dataset inspection (Task 3)  — `dataset_findings.json`

- **50,908 rows**, **131 unique repos = 131 unique images** (1:1). Tasks per image: min 8,
  median 269, max 2,389 (mean 389). Every image has >1 task.
- `PASS_TO_PASS` size: median 399, mean 1,064, **max 22,010**, **min 0** (476 rows have empty P2P).
  `FAIL_TO_PASS`: median 6, mean 81, **min 1** (never empty — every task breaks ≥1 test).
- **0** rows with duplicate IDs within a list; **0** rows with P2P∩F2P overlap (lists are clean & disjoint).
- **Node-ID shapes:** 55.9 M standard `path.py::…` (96.0 %); 1.98 M `path.ext::…` with a non-`.py`
  file (3.4 %, from 6 collector-heavy repos: pygments, pyquery, voluptuous, parse, result, scrapy —
  valid pytest IDs, but won't match a naive `.py::` recogniser); **362,730 (0.6 %) are
  unittest-style** (`test_x (module.Class)`) — **not** pytest `::` node IDs, so they cannot be fed
  to `pytest <id>` directly.

---

## 3. Manifest semantics, verified from source (Task 4)

Traced against the pinned repo (file:line below are on the VM clone).

- **Derivation** = diff of two pytest runs. `grading.get_valid_report` (`grading.py:61-92`) keeps a
  test only if it appears in **both** runs and matches one of four `(pre,post)` transitions.
  Fixture-verified direction (`tests/harness/test_grading.py`, pandas fixture):
  - **`FAIL_TO_PASS`** = FAILED with the synthetic bug ∧ PASSED in the bug-free reference.
  - **`PASS_TO_PASS`** = PASSED in both.
- **`Gi = P2P ∪ F2P` is the right reproduction target** (both must pass in the pristine reference)
  — confirmed by the pilots (§5).
- **Silently dropped:** SKIPPED / XFAIL / ERROR in *either* run (match none of the four branches);
  any test absent from either run; and — because of the parser — parametrized IDs with whitespace.
- **Timeout** (`base.py:108-109`): **90 s per single-instance run, 900 s for the shared full-suite
  reference**. On timeout the **whole task is discarded** (`report.json = {timed_out: True}`,
  `valid.py:110-115`; dropped in `gather.py:336`) — manifests are never *truncated*, tasks just
  vanish. No Python profile overrides these.
- **Log parser** (`profiles/python.py:93-102`): `re.match(rf"^(\S+)(\s+){status}", line)` over
  `pytest --verbose` output. `\S+` stops at the **first space** → this is the **source-level root
  cause** of the whitespace-param drop the pilot found empirically. xdist output and collection
  errors are also missable (some repos ship custom parsers).

---

## 4. Manifest consistency across tasks sharing an image (Task 5)

Per-image, over all its tasks, comparing `G = P2P ∪ F2P`:

| metric | value |
|---|---|
| images where `G` is **byte-identical** across all tasks | **70 / 131 (53.4 %)** |
| images where every task's `G` **nests inside the largest** `G` | **111 / 131 (84.7 %)** |
| images where `G` genuinely **does not nest** (bug-scoped) | **20 / 131** |
| `P2P` byte-identical across tasks | **0 / 131** (expected — each bug repartitions the suite) |
| median (over images) of median task-vs-largest Jaccard | **1.0** (mean 0.91) |

**Root cause (source-confirmed):** the per-profile **`min_testing`** flag (`base.py:111-118`).
Default `False` → bare `test_cmd` → **whole suite** → `G` identical across tasks (only the F2P/P2P
split moves). `True` → `get_test_cmd` appends only the **bug-related test files** derived from that
task's own F2P/P2P (`base.py:564-586`, `python.py:39-44`) → tiny, wildly-varying `G`. The 20
non-nesting images are **exactly** the documented `min_testing=True` repos (pandas — confirmed via
fixture running one test file — dask, moto, sqlfluff, mypy, monai, dvc, modin, conan, scrapy,
sunpy, sympy, …).

**Do not union bug-scoped manifests.** For pandas the union across 2,354 tasks is 109,403 tests
that were *never run together*; treating it as one reference is fiction. A canonical whole-suite
baseline for those 20 repos must be **regenerated** (run the full suite once in the pristine image),
not recovered from the dataset.

---

## 5. Repository profiles + pristine-image model (Task 6)

- **Row → profile:** `registry.get_from_inst(instance)` keys on `repo` (`base.py:687-717`);
  registered under both `owner__repo.<commit8>` and `swesmith/owner__repo.<commit8>`.
- **Image name:** `f"{org}/swesmith.{arch}.{owner}_1776_{repo}.{commit[:8]}".lower()`
  (`base.py:206-207`). **`_1776_` is a literal delimiter replacing the `/` in `owner/repo`**
  (Docker tags forbid `/`).
- **Workdir / rootdir = `/testbed`**; conda env **`testbed`** at `/opt/miniconda3`
  (`constants.py:16`, pandas fixture `rootdir: /testbed`).
- **Install spec (baked at build):** `python -m pip install -e .`, Python 3.10 default.
- **Official test command (Python default, `python.py:32-36`):**
  `source /opt/miniconda3/bin/activate; conda activate testbed; pytest --disable-warnings --color=no --tb=no --verbose`
  (+ file args when `min_testing`).
- **Pristine is reachable and is the base image.** SWE-smith **inverts** SWE-bench: the synthetic
  `patch` is a **bug**, the "gold patch" is its reverse (`utils.py:78-82`, *"gold patches = bug
  patches, so fix = revert"*). The mirror `main` = pristine working repo; bug/checkout applied only
  at run time. **Start the image with no patch and no branch checkout → pristine reference.**
  (`get_container` instead checks out the buggy `<instance_id>` branch → NOT pristine; avoid it.)

---

## 6. Pilot audit (Tasks 7–8)  — safe, digest-pinned, x86_64 native

Two small pure-pytest repos, chosen from the 70 stable images. Containers were run
**non-privileged**: `--network none`, `--cpus 2 --memory 4g --pids-limit 512
--security-opt no-new-privileges --cap-drop ALL`, **no bind mounts, no docker socket, no
credentials**. The harness-owned plugin (`scripts/swesmith_audit_plugin.py`) emits structured JSON
from `pytest_collection_finish / collectreport / deselected / runtest_logreport / sessionfinish`
— **no** terminal-summary regex, **no** junit `tests="N"`, **no** static counts. Per-node
outcomes are mutually exclusive.

| pilot | image digest | \|G\| | pristine collected | passed | coll. recall | pass recall | unexpected (collected∉G) |
|---|---|---|---|---|---|---|---|
| **tomli** | `a804c305…` | 16 | 16 | 16 | **1.0** | **1.0** | **0** — manifest == full collectable set |
| **iniconfig** | `d737c0b8…` | 28 | 42 | 42 | **1.0** | **1.0** | **14** — all whitespace-param IDs |

Both: **clean collection** (exit 0, 0 collection errors, 0 deselected), all tests pass in the
pristine reference. iniconfig proves `|G|` is a **lower bound** (28 of 42 collectable); the 14
absent are 100 % whitespace-parametrized (`test_tokenize[blank line]`, `test_iscommentline_true[ ;qwe]`)
and 0 of the 28 manifest IDs contain a space — a deterministic match to the parser's `^(\S+)`.
tomli proves the manifest can also equal the full collectable set when no whitespace params exist.

Network was **disabled** (`--network none`) and both pure-unit suites ran fine — no network needed.

---

## 7. What can honestly be claimed (Task 9)

| question | answer |
|---|---|
| Install/test instructions human-reviewed? | **No.** They are per-repo `RepoProfile` code, **execution-validated** only. No human-review claim in README/docs/CONTRIBUTING (grep clean; "validated" = "ran tests, checked transition"). |
| All Docker environments manually reviewed? | **No.** Auto-built + auto-validated. |
| All generated tasks manually reviewed? | **No.** A task is "valid" iff it breaks ≥1 test and keeps ≥1 passing (`harnesses.md:50`). Purely mechanical. |
| Is ">80 % pass" measured over a stable denominator? | **Only for `min_testing=False` repos**, and only against `G` (a lower bound). For `min_testing=True` repos the denominator is per-task and unstable — the *same* pathology as our ESSR problem. |
| Does SWE-smith prove complete collection? | **No.** iniconfig: 28 manifest vs 42 collected. The parser drops whitespace-params, SKIPPED/XFAIL/ERROR, and (for `min_testing`) everything outside the bug's files. |
| Are published manifests reliable fixed lower-bound gold sets? | **Yes, for the 70 stable repos** (recall 1.0/1.0, digest-pinnable). A candidate env collecting <\|G\| or passing <\|G\| of these is provably broken. |
| Can these images support a defensible environment-reproduction benchmark? | **Yes, scoped:** pristine, digest-pinned, execution-validated, x86_64-native reference images with a reproducible lower-bound gold set — for the stable subset. Not a whole-suite completeness benchmark. |

---

## 8. How this plugs into the ESSR denominator problem

The `2026-07-10` handoff wanted `N_verified` = "all non-author-skipped tests." SWE-smith does **not**
give that (it gives a lower bound, and its own parser suppresses skips/xfails). But it gives
something the current metric lacks entirely: an **external, pristine, digest-pinned reference** with
a **fixed** node-ID set that a broken candidate env cannot shrink. Recommended use, mirroring the
task's candidate metrics:

```
Gref                    = published P2P ∪ F2P for a stable (min_testing=False) repo   # fixed floor
manifest_collection_recall = |candidate_collected ∩ Gref| / |Gref|
manifest_pass_recall       = |candidate_passed    ∩ Gref| / |Gref|
clean_collection           = collect_exit==0 ∧ collection_errors==0 ∧ skips==0 ∧ deselected==0
```

Report the **pair** `(clean_collection, manifest_pass_recall)` — never a scalar. This blocks the
denominator-shrinking exploit (the floor is external and fixed) without pretending to know the full
suite. For the 20 bug-scoped repos and the whitespace-param gap, regenerate a whole-suite baseline
from the pristine image (the pilot method already does this) if you need a true ceiling.

---

## 9. Blockers & residual validity threats

1. **Whitespace-param under-count** (proven): `Gref` silently omits parametrized tests with spaces
   in the id. Mitigation: regenerate `Gref` from the pristine image's `--collect-only` (the pilot
   does exactly this) instead of trusting the published list, OR accept it as a conservative floor.
2. **Skips/xfails absent from `Gref`**: SWE-smith drops them at derivation, so `Gref` cannot measure
   "author-skipped vs env-skipped" — the very signal the ESSR proposal wanted. `Gref` is a *pass*
   floor, not a skip-aware denominator.
3. **20 `min_testing=True` repos have no repo-wide gold set** in the data; unioning is fiction.
   Regenerate or exclude.
4. **Unittest-style IDs (0.6 %)** and non-`.py` collector IDs (3.4 %) aren't directly `pytest <id>`
   runnable — set-membership comparison still works, but selective re-run does not.
5. **Conda activation:** the canonical command prepends `source .../activate; conda activate testbed`.
   The pilots used a bare `pytest` under `bash -lc` (login shell auto-activated `testbed`; 100 % pass
   confirms the right env). The prototype should prepend the activation to match exactly — TODO noted
   in the script.
6. **`gather.py` key mismatch** (subagent-flagged): at this commit `gather.py` reads
   `results[PASS_TO_FAIL]` while `get_valid_report` writes `FAIL_TO_PASS` — shipped labels likely come
   from the Modal `bug_gen.py` path. Semantics unaffected, but don't assume `gather.py` is the
   producer of the released rows.
7. **`latest`-only tags are mutable** — always pin by the digests in §1; re-resolve before a run.
8. **Harness integration not built:** our benchmark adapter cannot yet run a candidate env against an
   external `Gref` (today's scorer reads the agent's own junit). Wiring `Gref` in is a separate task.

---

## 10. Reproduction (all on VM `167.233.64.96`, `/opt/swesmith_audit`)

```bash
# deps: /opt/rat_venv has pyarrow 24 + datasets 4.8; docker 29.5
# 1. dataset (pinned) — parquet only, no images
/opt/rat_venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("SWE-bench/SWE-smith-py", repo_type="dataset",
  revision="77cab9055d42ab4a5c25c89a8f937096db13558e",
  allow_patterns=["data/*.parquet"], local_dir="/opt/swesmith_audit/data")
PY
# 2. dataset stats + consistency (memory-safe, streaming)
/opt/rat_venv/bin/python scripts/vm_inspect.py
# 3. DRY-RUN audit (no pulls): manifest size, consistency, digest pin
/opt/rat_venv/bin/python scripts/audit_swesmith_images.py --repo iniconfig
# 4. EXECUTE pilot (pulls by digest, runs pristine collection + official cmd)
/opt/rat_venv/bin/python scripts/audit_swesmith_images.py --repo iniconfig --execute
```

---

## 11. Deliverables

- **Prototype:** `scripts/audit_swesmith_images.py` (default dry-run/no-pull; `--execute` pilots;
  `--repo/--image/--limit`; digest-pinned; JSON + markdown; emits the exact Task-10 fields;
  prints "|G| is a LOWER BOUND, not the full suite").
- **Plugin:** `scripts/swesmith_audit_plugin.py` (structured-hook pytest plugin).
- **Analysis script:** `docs/superpowers/artifacts/swesmith_audit/vm_inspect.py` (streaming stats).
- **Raw artifacts:** `docs/superpowers/artifacts/swesmith_audit/{dataset_findings,audit_report,
  iniconfig_audit,tomli_audit,ref_iniconfig,ref_tomli}.json`.
- **Full source-trace** (file:line for every claim in §3/§5) archived in this session's subagent
  transcript; key citations inlined above.
