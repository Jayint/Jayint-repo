# Why DockerAgent lost to RAT — 50-repo head-to-head (2026-06-07)

Inputs: `results/dockeragent/2026-06-07-baseline` vs `results/rat/2026-06-07-corrected`
(same model `deepseek/deepseek-v4-flash`, same harness/scorer, same 50-repo hard subset).
Method: 1 bootstrap + 50 Haiku agents, one per repo, each contrasting DA's recipe
(`<org>__<repo>.json`, Dockerfile, `logs.*`) against RAT's actual command stream
(`outer_commands.json`). Per-instance reports in `instances/`. Raw records in `_records.json`.

## Headline
- **DA mean pytest pass-rate 0.239 vs RAT 0.623** (ESSR ÷executed: **0.373 vs 0.678**, RAT ≈1.8×).
- Per-repo: **RAT-only wins 22**, both-pass 3, both-fail 16, **DA-only win 1** (py2many).
- **41 / 50 failures are DA-specific (our agent's fault)**; only 9 are dataset-hard / infra.

## THE root cause (the why-behind-the-why)
**DockerAgent's bottleneck is not the LLM agent — it's the post-agent recipe pipeline.**

RAT is **stateful and in-container**: it runs commands live in one persistent container,
watches each test failure, and repairs by running more commands; whatever succeeds *stays in
the environment* and is replayed verbatim. It does true diagnose → install → re-run loops
(e.g. saw `ModuleNotFoundError: IPython`, ran `pip install IPython`, re-ran → green).

DockerAgent is **stateless re-synthesis**: the agent explores, then a *separate* synthesizer
re-derives a clean-room Dockerfile from "verified" commands, and a verification / self-verify
layer gates which commands survive. **Every gate is a place to lose a command that already
worked.** Success inside the agent's container does **not** guarantee the command reaches the
final recipe.

Quantified: of the **41 DA-specific failures, ≈24 (~60%) are "the agent ran the right command
but our pipeline dropped or rejected it"**; only ~17 are genuine agent gaps (never found the
setup). Fix-target mentions across records: **synthesizer 31 · recipe_repair 20 · verification
13 · agent.py 12**.

Two verified exemplars:
- **`rq/rq`** (DA 0.00 / RAT 1.00): the agent proposed `redis-server --daemonize yes`; the
  artifact-verify layer **rejected it** ("not previously observed succeeding in the final
  environment") and **auto-finalized an empty runtime-prep set** → the daemon never started →
  **345 ConnectionError**. RAT simply ran `redis-server --daemonize yes` and passed.
- **`open-webui/mcpo`** (DA 0.00 / RAT 1.00): the agent ran `pip install -e ".[dev]"`
  *successfully*, but the **synthesizer dropped it** from the final Dockerfile, keeping only
  `pip install pytest pytest-asyncio`; eval_script was merely `pytest --collect-only` →
  **ModuleNotFoundError (typer, dotenv, fastapi, pydantic)**.

## Failure categories (all 50)

| # | Category | Count | DA-specific? | Mechanism |
|---|---|---:|---|---|
| 1 | `missing_runtime_or_test_deps` | 13 | mostly yes | Build OK, tests `ModuleNotFoundError` — optional/test extras (`[dev]`, `[test]`) never installed or **dropped** from the recipe. |
| 2 | `missing_project_self_install` | 5 | yes | The repo's **own package** never `pip install -e .`'d (or it was dropped) → its modules don't import. |
| 3 | `native_system_deps_missing` | 4 | yes | No `apt-get install` of system libs (tesseract, build-essential, etc.). RAT installed them. |
| 4 | `test_collection_error` | 4 | mixed | `--collect-only` errored (bad conftest import / wrong rootdir). |
| 5 | `docker_build_failed` | 3 | yes | Malformed Dockerfile (RUN-continuation bug) or impossible version pins → no image. |
| 6 | `wrong_test_command` | 3 | mixed | eval ran the wrong/empty test target (e.g. only `--collect-only`). |
| 7 | `empty_or_rejected_verification_bundle` | 3 | yes | Verification Bundle rejected → eval script runs **nothing** → 0. |
| 8 | `scoring_or_infra_artifact` | 3 | mixed | Recipe/eval plumbing produced a hollow 0 despite a runnable repo. |
| 9 | `parity_both_passed` | 6 | n/a | DA == RAT (e.g. pal-mcp-server, wafw00f, LibreTranslate). |
| 10 | `dataset_hard_rat_also_failed` | 2 | no | Repo unrunnable in-container (RAT also 0). |
| 11 | `da_outperformed_rat` | 2 | n/a | DA ≥ RAT (py2many is the genuine win). |
| 12 | `python_version_or_toolchain_mismatch` | 1 | yes | Merged conflicting requirement files / nonexistent pin (`ipython==8.14.0`). |
| 13 | `service_not_started` | 1 | no | Test needs Docker socket at import (nginx-proxy) — dataset-hard. |

> Note: a few Haiku category labels are imperfect (e.g. `rq/rq` filed under deps though its
> `root_cause` correctly identifies the rejected redis-daemon command; `bruin-data/ingestr`
> filed `da_outperformed` though it actually build-failed on Go/Node misdetection). The
> `root_cause` / `what_rat_did_differently` text in each record is the reliable signal.

## Consolidated into 6 actionable buckets

**A. Pipeline discards a working command — ≈24 repos (the dominant, most fixable cause).**
Synthesizer drops `pip install -e .[dev]` (mcpo, copier, NevaMind memU); Verification-Bundle
rejects a verified command and auto-finalizes empty (rq redis, pre-commit, supabase, conor
n8n); self-verify loop gives up `status=unresolved; keeping original recipe` after 3 rounds
(rq, mcpo). **Fix:** `src/synthesizer.py` + `src/artifact_verify.py` + `src/recipe_repair.py`.

**B. Incomplete dependency closure — genuine (≈9–13).** Synthesizer never parses pyproject
`[project.optional-dependencies]` / poetry test groups (D4Vinci IPython, epam, docling,
sooperset). **Fix:** `src/synthesizer.py` extras extraction.

**C. Dockerfile build failures — ≈4.** Malformed RUN-continuation (`RUN ... \` then `RUN ...`)
in OpenManus, D-FINE, frappe, les-emplois; conflicting merged requirements (slurm-gcp).
**Fix:** `src/synthesizer.py` RUN emission + a pre-return `docker build` smoke test.

**D. Wrong / empty test command — ≈5.** eval_script = `--collect-only` only (mcpo); wrong
target / rootdir (django-oauth, Argus, nomadkaraoke, websockets, markitdown, tesserocr).
**Fix:** `agent.py` test-command verification must require actual *execution*, not collection.

**E. Native system deps missing — ≈4.** No `apt-get install` of libtesseract/build-essential
(tesserocr, pyads, feast, RT-DETR, weibo-crawler). **Fix:** synthesizer system-dep inference.

**F. Genuinely dataset-hard / not us — 9.** nginx-proxy (Docker socket at import),
OpenManus/FoundationAgents (upstream test code broken, both 0/0), supabase, etc. RAT also fails
or scores 0. Not counted against the agent.

## Recommended fixes, by file (priority order)
1. **`src/synthesizer.py`** — (a) never drop the agent's successful `pip install -e .`/`.[dev]`
   /`-r requirements*.txt` from the final Dockerfile; (b) extract pyproject optional-dependency
   groups; (c) fix RUN-continuation emission; (d) infer native `apt-get` deps. *Highest ROI —
   touches buckets A/B/C/E.*
2. **`src/artifact_verify.py`** — stop rejecting commands "not previously observed succeeding
   in the final environment" when they DID succeed in the agent's container; never
   auto-finalize an **empty** runtime-prep/test set (treat empty as failure, not success).
3. **`src/recipe_repair.py`** — on `status=unresolved`, don't silently keep the original broken
   recipe; carry forward the agent's last known-good commands; add a service-start heuristic
   (ConnectionError → start the daemon) and a ModuleNotFoundError → `pip install <mod>` loop.
4. **`agent.py`** — require the verified test command to *execute and run ≥1 test*, not just
   collect; persist container state so the agent's installs survive into the recipe.

## What this run validates
Confirms the `dockeragent-synthesizer-drops-installs` hypothesis **at scale**: DA's
`build_success` frequently fires while `test_success=false` (≈12 of the 22 losses) because the
synthesis/verification layer ships hollow recipes. The gap to RAT is overwhelmingly a
**recipe-pipeline engineering problem, not an LLM-reasoning problem.**
