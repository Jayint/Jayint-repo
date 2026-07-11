# Handoff: manifest_builder corpus shakeout (sonnet, attempts=1) + concurrency analysis

**Date:** 2026-07-12 · Module: `src/manifest_builder/` (branch `john-v3-multi-lang`, ≥ `550da80`)

## Part 1 — Shakeout handoff prompt (cheap model, dry-run the plumbing)

> **Goal.** Run the full 50-repo pinned corpus with the CHEAP model (**sonnet**) at **attempts=1**,
> to confirm that per-repo **scores + Dockerfiles are correctly stored and aggregated** — a plumbing
> dry-run before spending on a full **opus** pass. Pass/fail rate does NOT matter here (sonnet@1 will
> miss the hard repos); what matters is: every repo produces a stored artifact dir, the aggregate
> files are written, resumability works, and no repo crashes the batch.
>
> **Environment (already set up on the VM — verify first):**
> - VM `root@167.233.64.96` (x86_64, Docker 29.5.3). Module at `/opt/manifest_builder`.
> - Claude Code at `/opt/node/bin/claude`; auth = `CLAUDE_CODE_OAUTH_TOKEN` in `/opt/harness/.env`.
>   If a run prints `401 Invalid bearer token`, refresh: `ssh -t root@167.233.64.96 'PATH=/opt/node/bin:$PATH claude setup-token'` then update the `.env` line.
> - Wrapper `/opt/manifest_builder/run_env.sh` exports PATH + `IS_SANDBOX=1` (needed for
>   `--dangerously-skip-permissions` as root) + the token.
> - Make sure the module on the VM is current: from the Mac repo root,
>   `rsync -a src/__init__.py src/manifest_builder root@167.233.64.96:/opt/manifest_builder/src/`.
>
> **Run (sequential, resumable, detached — 50 sonnet agent runs = hours):**
> ```bash
> ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=100000 root@167.233.64.96 \
>   'bash /opt/manifest_builder/run_env.sh python3 -m src.manifest_builder corpus \
>      --corpus datasets/rat_python_hard_subset.pinned.json \
>      --model sonnet --attempts 1 --out /opt/manifest_out_corpus \
>    > /opt/manifest_out_corpus/run.log 2>&1'
> ```
> Optional first: add `--limit 3` for a 3-repo smoke before the full 50. Re-running skips repos
> already `CERTIFIED` on disk (resumable); add `--force` to redo.
>
> **Then verify storage + aggregation (this is the actual deliverable):**
> ```bash
> ssh root@167.233.64.96 'bash -lc "
>   cat /opt/manifest_out_corpus/corpus_summary.json;                 # counts by status + per-repo sizes
>   echo ---; wc -l /opt/manifest_out_corpus/corpus_results.jsonl;    # one line per processed repo
>   echo ---; ls /opt/manifest_out_corpus | head;                      # per-repo <slug> dirs
>   D=\$(ls -d /opt/manifest_out_corpus/*/*/ 2>/dev/null | head -1);
>   echo checking \$D; ls \$D;                                          # Dockerfile + collected-nodeids.json + cert
>   python3 -c \"import json;c=json.load(open(\$D+\\\"collection-certificate.json\\\"));print(c[\\\"status\\\"],c[\\\"manifest_size\\\"],c[\\\"runs\\\"])\"
> "'
> ```
> **PASS criteria:** `corpus_summary.json` shows `total` = number of repos attempted; every processed
> repo has a `<slug>/<sha>/` dir containing `Dockerfile`, `collected-nodeids.json`,
> `collection-certificate.json`; each cert's `manifest_size` == `len(collected-nodeids.json)` ==
> `runs[0].collected_count`; the run did not abort on any single repo (failures show as `ERROR`/`REJECTED`
> rows, not a crash).
>
> **Watch:** disk (50 images accrue on ~40 GB free) — if it fills, `docker image prune -f` (artifacts
> persist independently). A hung agent caps at 3600 s per attempt then soft-fails that repo.

## Part 2 — Can it run in parallel? (yes, bounded, with per-worker isolation)

**Already isolated per repo (safe under concurrency):** each `build_one` uses a unique
`mkdtemp` workspace, a unique `TemporaryDirectory` for collect output, and a unique artifact dir
`<out>/<slug>/<sha>/`. Docker tag/container = `manifest-<slug>` / `-run`; **all 50 corpus slugs are
unique (verified)**, so different repos never collide on tags or containers. No global mutable state
is touched in the build path.

**Race conditions / shared state that DO need handling:**

1. **Claude Code config (`~/.claude`) — the main hazard.** Concurrent `claude` processes share
   `$HOME/.claude` (config, session state, telemetry, and potentially OAuth-token refresh writes).
   Concurrent writers can corrupt it. ⇒ **Give each worker its own `HOME`** (`HOME=/tmp/cc-home-<n>`,
   token supplied via env so no stored creds needed). This is why **process-based sharding** is the
   right model and **thread-based** concurrency is not (threads share one process env/`HOME`).
2. **Aggregate append race.** N workers appending to one `corpus_results.jsonl` can interleave. ⇒
   Give each shard its **own `--out`** (its own results file + per-repo dirs), merge at the end.
   (The per-repo `<slug>/<sha>/` dirs are already collision-free by slug+sha, so a shared `--out` is
   safe for *those* — only the shared results-file append is not.)
3. **Docker resource + subscription rate limits.** N concurrent agent runs + docker builds contend
   for CPU/mem/disk and hit the Claude subscription's rate limit (429). ⇒ **Bound N ≈ 3–4.** Each
   collect is capped (`--cpus 2 --memory 4g`); a 429 just fails one attempt → that repo is rejected
   → picked up on a resumable re-run.
4. **Disk / image accumulation.** Built images are not removed (matters even sequentially: 50 images
   on ~40 GB). ⇒ periodic `docker image prune -f`, or add image cleanup to `build_one` (recommended
   code change).
5. **Docker-tag uniqueness (robustness).** Safe for THIS corpus (unique slugs), but for other
   corpora add a unique suffix (`sha[:8]`) to the tag/container name — recommended code change.

**Recommended recipe (works with current code, no changes):**
- Split `rat_python_hard_subset.pinned.json` into N shard files.
- Launch N processes, each with a distinct `HOME` and distinct `--out`, e.g.:
  ```bash
  HOME=/tmp/cc-home-1 bash /opt/manifest_builder/run_env.sh \
    python3 -m src.manifest_builder corpus --corpus shard1.json \
      --out /opt/manifest_out_corpus/shard1 --model opus --attempts 3
  ```
- Start at **N=3**, watch CPU/mem/disk/429s, scale cautiously. Merge shard results at the end.

**Cleaner support (offer — small code changes if wanted):** `--shard i/N` on `corpus` (so no JSON
splitting), per-invocation-unique docker tags, and post-repo `docker rmi` for disk. None are required
to run concurrently today via sharding; they'd make it turnkey.

**Bottom line:** parallelism is safe and worthwhile via **bounded process-sharding (N≈3–4)** with
**per-worker `HOME`** and **per-shard `--out`**; the only truly required isolations are those two —
everything else (unique tags for this corpus, resource caps) is already satisfied or is a tuning knob.
Do the sequential sonnet shakeout first to confirm the plumbing, then shard the opus run.
