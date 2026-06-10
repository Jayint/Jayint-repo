# Would Option-2-part-2 + GAP-1 actually help DockerAgent? (Sonnet workflow, adversarially audited)

Method: 3 parallel Sonnet investigators (self-verify-fix mechanics, Repo2Run harness-repair mechanics,
empirical per-repo recoverability) + an adversarial Sonnet audit that challenged every "recoverable"
claim. Run `selfverify-fix-vs-repo2run-repair` (21 agents).

## Bottom line
**As scoped, the two fixes help only ~5 of DockerAgent's 38 failures.** The empirical agent claimed
18 recoverable (projected +0.33 lift); the **adversarial audit cut that to 5 confirmed** — because the
*dominant* DA failure (a dropped **editable/project install**, `pip install -e .`) is **not** what the
deterministic missing-module repair fixes, and the agent's LLM repair is both short-circuited and
trajectory-blind.

## Why the fixes don't reach the main failure mode
1. **Deterministic repair can't restore an editable install.** For a missing project package (e.g.
   `nitrokey`), `requirement_for_missing_module` returns the bare PyPI name → it emits
   `pip install nitrokey` (a possibly-wrong/stale PyPI package), **never** `pip install -e .`
   (recipe_repair.py:185, 238).
2. **The LLM repair — which *does* know `pip install -e .` (Rule 14) — is silenced.** `_apply_repair`
   only calls the LLM if the deterministic path returned nothing; since it returned a (wrong) install,
   the LLM never runs (artifact_verify.py:331-334).
3. **And the agent's LLM repair is trajectory-blind.** It sends the broken recipe + project-config
   files but **not** the agent's `successful_actions`/`build_commands`, so even when reached it must
   *infer* `-e .` from config alone — unreliable (recipe_repair.py:380-429).
4. **Self-verify validates a re-rendered clean-room, not the shipped Dockerfile** (it injects a fresh
   `git clone`; the scorer's image is `docker cp`'d) — so it can mis-see the env (artifact_verify.py
   render path).

## Repo2Run harness loop is simpler AND strictly more capable
The Sonnet comparison is unambiguous (run_repo2run_benchmark.py:3398-3530):
- It **scores the last repaired artifact unconditionally** — no "resolved" adopt-gate. That single
  property gives **both** proposed agent fixes for free: validate-the-shipped-artifact **and**
  adopt-partial-repairs.
- Its LLM repair receives the **full `agent_run_summary` (build_commands + successful_actions)** and
  Rule 5 ("restore omitted successful setup commands from the trajectory") — so it **can restore the
  dropped `pip install -e .`** from trajectory evidence, which is exactly the dominant DA failure the
  agent's own repair structurally cannot fix.
- It's ~800 lines of already-correct, battle-tested code (it's what got DA to 0.876 on the Repo2Run
  benchmark). **Porting it into the RATBench scorer is plumbing; retrofitting the agent's self-verify
  to match is two harder structural changes** (adopt-partial semantics + threading the trajectory into
  the repair payload).

## Honest recoverability decomposition (38 failing repos, after adversarial audit)
| bucket | n | recoverable by what | lift if fixed |
|---|---|---|---|
| **Solid (audit-confirmed)** — 3rd-party dep / clean gate-strip / env var | **~5** | Option-2-part-2 + deterministic repair *as scoped* | **÷all 0.24 → ~0.34 (+0.10)** |
| **Editable/venv-PATH drops** (mcpo, copier, darts, verifiers, yutto, epam, sooperset, aapatre, agentic-ray…) | ~13 | only if you ALSO add editable-install recovery (port harness loop's trajectory-aware LLM repair, or make deterministic emit `-e .`) | +0.10–0.15 more → ~0.45–0.50 |
| **Service-not-started** (rq=redis, Tecnativa/nginx=docker socket, scylla) | ~4 | needs GAP-3 (start daemon in runtime_prep) — NOT these fixes | — |
| **Native lib / GPU** (docling=tesseract, RT-DETR=CUDA) | ~2 | needs apt/native-dep inference — NOT pip repair | — |
| **Infra disk-OOM** (ModelEngine, aiida, feast, frappe — "no space left on device") | ~4 | **re-run with more disk** — not an agent bug (R2R got feast 0.99) | — (artifact) |
| **Timeout** (pre-commit, websockets, tesserocr — thousands of tests / hangs) | ~3 | longer timeout, not dep repair | — |
| **Dataset-hard / not our fault** (Nitrokey all-skipped, Xee=EE auth, ingestr=no-python-tests) | ~3 | none | — |

## Answer to "would this actually help?"
- **The two fixes alone: modest — ~+0.10 (÷all 0.24→0.34), ~5 repos.** The adversarial audit shows the
  optimistic +0.33 collapses because the deterministic repair can't emit `pip install -e .`.
- **The real leverage is editable-install recovery + trajectory-aware repair**, which is *exactly* what
  the Repo2Run harness loop already does. **Recommendation: port the harness loop into the RATBench
  scorer** rather than (or before) retrofitting the agent's self-verify — it's simpler and recovers the
  ~13 editable cases the agent's repair can't.
- **~16/38 failures are NOT repair-addressable** by any of this (services, native libs, infra-OOM,
  timeouts, dataset-hard). Several "OOM/no space left" zeros are **run-infra artifacts** — re-run with
  more disk before attributing them to the agent.
- **Model caveat (from prior finding):** under MiniMax-M2.7 the installs aren't dropped at all (82% vs
  32% present). A repair loop is a *backstop*; preventing the drop (synthesis model/prompt) is the
  *cure*. Run the controlled same-model A/B before committing engineering to either layer.
