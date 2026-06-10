# Head-to-Head: DockerAgent vs RAT — Honest Baseline (2026-06-07)

**Setup (apples-to-apples):** identical 50-repo hard subset (`datasets/rat_python_hard_subset.json`),
identical model (`deepseek/deepseek-v4-flash` via OpenRouter, Alibaba pin), identical harness with the
**patched honest scorer** (no timeout→1.0 phantom, recursive results glob, language fix, 1800s pytest
timeout), concurrency 12, `--num-turn 30`, `--timeout 7800`.
- RAT run: `rat_run_rat_corrected/` (PID 917900) — the paper's RAT agent (`--model rat`).
- DockerAgent run: `rat_run_dockeragent/` (PID 1223709) — our agent (`--model dockeragent`).

## Headline

| metric | DockerAgent (ours) | RAT (paper) |
|---|---|---|
| **ESSR (paper macro, ÷ executed)** | **0.3729** (n=32) | **0.6775** (n=46) |
| coverage-penalized (÷50) | 0.2387 | 0.6233 |
| repos executed | 32 / 50 | 46 / 50 |
| full-pass (rate ≥ 0.999) | 3 | 16 |

**RAT beats our DockerAgent by ~0.30 absolute ESSR (~1.8×).** This is the clear baseline to aim toward.

## Two drivers of the gap

1. **Coverage / env-setup failures.** DockerAgent failed to produce a runnable test env on **15 repos that RAT executed** (`da_notexec`). build_success=0.64, collect_success=0.30. This is the single biggest contributor — DockerAgent doesn't get far enough to be scored on nearly a third of the set.
2. **Hollow 0.0s among executed.** Where it did execute, DockerAgent scored 0.000 on many repos RAT passed cleanly (copier 0 vs 0.996, verifiers 0 vs 0.956, darts 0 vs 0.998, karaoke-gen 0 vs 0.982, ai-dial-sdk 0 vs 1.0, mcpo 0 vs 1.0, mcp-atlassian 0 vs 1.0, rq 0.003 vs 1.0). Consistent with the known synthesizer drops-installs / hollow-success pattern persisting at scale despite the repair loop.

## Per-repo buckets

- **both pass (10):** pal-mcp-server, Scrapling, wafw00f, LibreTranslate, proxy_pool, markitdown, nba_api, bilingual_book_maker, DDNS, dumb-init — the achievable core (mostly ties at ~1.0).
- **RAT only (14)** — DockerAgent's improvement targets: D-FINE, verifiers, Automatic-Udemy-Enroller, copier, django-oauth-toolkit, ai-dial-sdk, docling, karaoke-gen, mcpo, resend-python, rq, mcp-atlassian, darts, yutto.
- **DA only (0):** none — DockerAgent never strictly out-passes RAT.
- **both fail (7):** memU-server, pynitrokey, docker-socket-proxy, docling*, Xee, py2many (DA 0.838 > RAT 0.732 but both <0.9), tesserocr.
- **DA not-executed (15):** OpenManus, slurm-gcp, MemOS, nexent, aiida-core, feast, les-emplois, lyuwenyu/RT-DETR, nginx-proxy, pre-commit, scylla-cluster-tests, supabase-py, stlehmann/pyads, websockets, + (env-setup failures).
- **both not-executed (3):** ingestr, n8n-autoscaling, frappe/press (the wrong-language v2-replace targets).

## Repair loop (verified working, but insufficient at scale)

The Dockerfile repair loop is proven functional (smoke on Scrapling: build-fail → repair → rebuild →
725 tests / 0.993, recovered the dropped `pip install -e ".[ai,shell]"` + test reqs). It clearly helps
on individual repos, but the 15 not-executed + many hollow 0.0s show it isn't closing the gap broadly —
the upstream synthesizer/env-setup failures dominate.

## Caveats

- DockerAgent's 0.3729 is the macro over only the 32 it executed; the coverage-penalized 0.2387 is the
  fairer single number given how often it fails to execute. RAT's coverage is far better (46/50).
- Both numbers carry the residual known-harness limitations (native core-dump → 0, the 3 wrong-language
  repos) equally, so the comparison is fair; absolute values will rise slightly under dataset v2.

## Artifacts
- Per-repo: `results/headtohead_dockeragent_vs_rat.csv`
- RAT per-repo: `results/essr_per_repo_corrected.csv`
- Runs: VM `rat_run_dockeragent/`, `rat_run_rat_corrected/`
