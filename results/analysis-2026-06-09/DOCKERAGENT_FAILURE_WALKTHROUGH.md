# DockerAgent failure walkthrough — why it underperforms on RAT (run `rat_run_runner4`)

**Date:** 2026-06-09 · **Subject run:** `rat_run_runner4` (DockerAgent, faithful runner-repair, commit `d69f8a2`)
**Baselines contrasted:** `rat_run_rat_corrected` (RAT, 06-06), `rat_run_repo2run` (repo2run, 06-07)
**Method:** 19 Sonnet agents read the real per-repo logs on the VM (`167.233.64.96:/opt/rat-bench-integration`) — 10 diagnostic (one per failure bucket), 5 contrast (RAT-win vs DockerAgent-loss, side-by-side), 4 adversarial verifiers (re-checked the top root causes against raw logs). Workflow `wf_71ddf896-89a`. Raw structured findings: `dockeragent_failure_findings.json` (this dir).

> **How to read this doc.** §1–§3 are the headline + the one caveat that changes everything. §4 is the ranked failure taxonomy. §5 explains the two architectural root causes. **§6 is the guided log walkthrough** — six representative repos, quoting the actual `run.log` lines step by step, so you can see the agent's behaviour. §7 is "what RAT did right." §8 is the prioritized fix list mapped to the redesign.

---

## 1. TL;DR

The DockerAgent's headline `div_all=0.18` on `rat_run_runner4` is **not a clean measurement** and **must not be compared to RAT (0.62) / repo2run (0.39) as-is.** Reading the real logs splits the 50 repos into three very different stories:

| tier | what it is | repos | comparable? |
|---|---|---:|---|
| **0. Experimental artifact** | OpenRouter ran **out of credits mid-run** → HTTP 402 aborted the repo, usually at the *very first* LLM call (ImageSelector), before any agent step | **16 / 50 (32%)** | **No** — penalizes DockerAgent only; baselines ran earlier with credits |
| **1–4. Genuine env-construction failures** | the agent and/or synthesizer really did build a broken environment | 27 / 50 | Yes |
| **(success)** | env built, tests ran, ~99% pass | 7 / 50 | Yes |

**So a third of the "coverage gap" vs RAT is a billing artifact, not an agent weakness.** The prior conclusion in `DEFINITIVE_RUNNER_RESULT.md` — *"lower div_all is agent (deepseek) non-determinism / coverage swing"* — is **partly wrong**: 16 repos failed for a deterministic, external reason (credit exhaustion), not stochastic agent behaviour. **A re-run with sufficient credits is required before any cross-agent claim is valid.**

Among the **genuine** failures, there are exactly **two architectural root causes**, and they recur over and over:

1. **The synthesizer is lossy and unfaithful.** DockerAgent runs the agent in a sandbox, then a *separate synthesizer re-derives a Dockerfile* from the trajectory. That re-derivation step injects most failures: it **drops** the install, **hallucinates** an install from a doc comment, or copies the **wrong base image** from the repo's own production Dockerfile. The agent often did everything right inside the sandbox — the synthesizer threw it away. (Tiers 1–2, ~12 repos.)
2. **The agent's "it works" certificate is too weak — it verifies with `pytest --collect-only`.** Collection passes in <1 s even when the *real* test run will fail: it never compiles a needed C binary, never needs a live Redis, never pays the runtime cost, never triggers a missing fixture. The agent declares success; the eval then runs the real suite and it breaks. (Tier 3, ~6 repos.)

RAT avoids **both** by construction: it works in **one persistent live container** (no lossy re-synthesis step) and it **runs the full test suite during its agent phase** (no collect-only false-pass). That, not model quality, is the gap.

---

## 2. The numbers, corrected

Inline scan of all 50 repos in `rat_run_runner4` (`_result_row.json` + `run_pytest_results.json`):

```
no_dockerfile   17   <-- 14 of these are credit-wall (402) aborts, not real failures
build_failed    10
other_error      6
partial          4
zero_collect     4
docker_timeout   2
full_pass        7
```

Re-bucketed by **true root cause** (credit-wall stripped out):

```
CREDIT-WALL (402, experimental artifact)        16   docling, epam/ai-dial-sdk, aiida-core, feast, nexent,
                                                     slurm-gcp, frappe/press, les-emplois, google/Xee,
                                                     karaoke-gen, agentic-ray, resend-python, scylla,
                                                     pyads, MemOS, D-FINE
GENUINE: synthesizer dropped/hallucinated install 9  darts, mcp-atlassian, LibreTranslate, django-oauth,
                                                     memU-server, swar/nba_api, pynitrokey, yutto, bilingual_book_maker
GENUINE: wrong base image (Alpine + apt preamble) 3  Tecnativa/docker-socket-proxy, weibo-crawler, n8n-autoscaling
GENUINE: collect-only false-pass (no real run)   ~6  dumb-init, rq, pre-commit, websockets, Argus, tesserocr
GENUINE: ran out of 30 turns / react loop         4  nginx-proxy, supabase-py, Scrapling, OpenManus
GENUINE: missing test dep / system lib            3  pal-mcp-server, verifiers, py2many
GENUINE: native-build / infra                     2  RT-DETR (host containerd), (dumb-init counted above)
GENUINE: no Dockerfile emitted (real)             1  bruin-data/ingestr
SUCCESS                                            7  wafw00f, DDNS, udemy-enroller, copier, proxy_pool,
                                                     markitdown, mcpo
```

**21 repos that RAT solved (essr≈1.0) but DockerAgent did not** span every bucket — those are the side-by-side contrasts in §7.

---

## 3. READ FIRST — the credit-wall artifact (Tier 0)

**16/50 repos died because the OpenRouter account ran out of credits during the run.** The error is identical and unambiguous, and in 14 of the 16 it fired on the **ImageSelector's first LLM call — before a single agent step ran** (the repo "failed" in 1.4–1.6 seconds):

```
✗ Error processing instance stlehmann__pyads: Error code: 402 - {'error': {'message':
'This request requires more credits, or fewer max_tokens. You requested up to 65536 tokens,
but can only afford 45222. To increase, visit https://openrouter.ai/settings/credits ...',
'code': 402}}
```
```
_meta.json:  "duration_s": 1.615,  "failure_reason": "no_dockerfile"
```

The affordability number **drops across the run** (59,400 → 45,222 → 26,016 tokens remaining) — the wallet was being drained *during* `rat_run_runner4`. Because RAT (06-06) and repo2run (06-07) ran **earlier**, they were not hit. This makes the `runner4` coverage number structurally unfair to DockerAgent.

Two distinct bugs compound it:
- **The request ceiling is 65,536 `max_tokens`** for even a tiny ImageSelector call — so as soon as the balance dips below ~64 k, *every* call 402s regardless of how little it actually needs.
- **The ImageSelector keeps hammering after the first 402** (aiida-core: it tried all 22 remaining per-file confirmation calls, each 402'd, then fell back to detecting the language as `rust` and aborted).

**Fix before re-running (P0):** (a) top up credits; (b) lower ImageSelector `max_tokens` to a realistic ceiling; (c) pre-flight credit-balance check that aborts the *batch* (not silently each repo) when balance < threshold; (d) stop the ImageSelector after the first 402. Ideally move off per-credit-balance billing. **None of these 16 repos tell us anything about env construction — re-run them.**

---

## 4. Failure-mode taxonomy (genuine failures, ranked)

| # | failure mode | repos | tier | the one-line cause |
|---|---|---:|---|---|
| 1 | **Synthesizer drops / hallucinates the install** | 9 | 1 | the re-derived Dockerfile doesn't match what the agent actually, successfully ran in the sandbox |
| 2 | **`collect-only` false-pass** | ~6 | 3 | agent certifies with `pytest --collect-only`; real run needs a binary / service / time the agent never tested |
| 3 | **Wrong base image + Alpine apt preamble** | 3 | 2 | synthesizer takes `FROM` from the repo's *production* Dockerfile (Alpine), then injects a Debian-only apt block that can't run on Alpine |
| 4 | **Ran out of 30 turns / ReAct loop** | 4 | — | agent burns turns (sometimes producing no `Action`) and never reaches a verified env |
| 5 | **Missing test dep / system lib** | 3 | — | a needed plugin or `-dev` package isn't in the image |
| 6 | **Native-build / host infra** | 2 | 4 | RT-DETR: containerd overlayfs fails to commit a ~2 GB torch layer (host bug, not agent) |
| 7 | **No Dockerfile emitted (genuine)** | 1 | — | agent genuinely never converged |

---

## 5. The two architectural root causes

### Cause A — the synthesizer is a lossy second pass (Tiers 1 & 2)

DockerAgent's pipeline is **agent-in-sandbox → synthesizer re-derives a Dockerfile → eval builds that Dockerfile.** The agent frequently *succeeds* in the sandbox; the synthesizer then produces a Dockerfile that does **not** reproduce it. Three observed sub-failures:

- **A1 — drops a prerequisite tool install.** `mcp-atlassian`: agent ran `pip install uv` (✓) then `uv sync --dev` (✓). Synthesizer emitted `RUN uv sync --dev` **without** the `pip install uv` before it → `uv: not found` (exit 127).
- **A2 — hallucinates a command from repo docs.** `darts`: agent really ran `pip install -e ".[torch,notorch,dev,optional]"` (✓). Synthesizer ignored that and emitted `RUN uv sync --group dev-all` — a string it lifted from a **comment** in `pyproject.toml` (`# For uv users: uv sync --group dev-all`) → `uv: not found` (exit 127).
- **A3 — honors a stale "rejected" flag.** `LibreTranslate`, `django-oauth-toolkit`: the agent's first `pip install -e ".[test]"` was piped through `| tail`, which the sandbox preflight **rejects** ("must not pipe through head/tail/grep"). The agent re-ran it clean and it **succeeded** — but the synthesizer still marked the install `excluded` and emitted `build_commands: []`. The eval image got only `pip install pytest`, so collection dies with `ModuleNotFoundError: No module named 'django'` / `unrecognized arguments: --cov`.
- **A4 — wrong `FROM` + unconditional Debian apt block (Tier 2).** `Tecnativa`, `weibo-crawler`, `n8n`: synthesizer set `FROM haproxy:3.2.4-alpine` / `python:3.12.0-alpine` (copied from the repo's own Dockerfile, **overriding** the ImageSelector's `python:3.x`), then injected a Debian apt-hardening `RUN ... > /etc/apt/apt.conf.d/99jayint-retries`. Alpine has no `/etc/apt/apt.conf.d/` → `can't create ...: nonexistent directory` (exit 1).

**The common fault:** the Dockerfile is re-derived from re-reading the trajectory/repo, not from a faithful, ordered list of the agent's *verified successful mutating actions*. RAT has no such step — its live container **is** the artifact — so it never loses the install.

### Cause B — the verification certificate is `collect-only` (Tier 3)

The agent's "Verification Bundle" routinely certifies the env with `pytest --collect-only -q`. Collection is a dry run: it imports test modules but **does not execute tests**. It therefore passes when the real run will fail:

- `dumb-init`: collected 182 tests, declared success — but never ran `make build`, so the `dumb-init` **C binary doesn't exist** → 172 tests `FileNotFoundError: 'dumb-init'`.
- `rq`: collected 573 tests with Redis running *in the agent session*, but the entrypoint's `redis-server --daemonize yes` **dies in the eval container** → 345 `ConnectionError: ...localhost:6379`.
- `pre-commit`, `websockets`: collected fast (`820 tests in 0.69s`), but the eval's `run_pytest.py` runs the **full** suite (integration tests spawning git/subprocesses) → exceeds the 600 s `docker exec` timeout → `docker_timeout`.
- `Argus`: collected 1 test, but execution hits `fixture 'ns_host' not found`.

**The common fault:** "collected" ≠ "passes." RAT runs the **full** suite during its agent phase and *sees* these failures, so it fixes them (compiles the binary, starts the service durably, writes the fixture). This is exactly the "silent false pass" the redesign (`docs/DESIGN-environment-state-maintainer.md` §2) is built to make impossible.

---

## 6. Guided log walkthrough (the real messages, step by step)

Six representative repos. All quotes are verbatim from `run.log` / recipe JSON / `_result_row.json` on the VM.

### W1 — The credit wall: `stlehmann/pyads` (Tier 0, and what it *should* have been)

The entire run is **1.6 seconds**. It never gets past base-image selection:

```
[DockerAgent] Analyzing repository to select optimal base image...
[ImageSelector] Analyzing repository structure...

✗ Error processing instance stlehmann__pyads: Error code: 402 - {'error': {'message':
'This request requires more credits ... you requested up to 65536 tokens, but can only
afford 45222 ...', 'code': 402}}
```
```
_meta.json:  "duration_s": 1.615,  "failure_reason": "no_dockerfile"
```

This is logged as `no_dockerfile`, which *looks* like an agent failure in the bucket counts — but the agent never ran. **What RAT did with the same repo** (it scored 114/114): it discovered pyads ships a **C shared library as a git submodule** and built it iteratively inside its live container —

```
apt-get install -y -qq build-essential        returncode 0
git submodule update --init --recursive       (after rm -rf the stale /repo/adslib)
meson setup /repo/adslib/build /repo/adslib    returncode 0
ninja -C /repo/adslib/build                     returncode 0
cp /repo/adslib/build/libadslib.so ...
```

**Lesson:** pyads is genuinely solvable (RAT proves it). DockerAgent's only failure here was running out of credits at the door. Re-run it.

### W2 — The synthesizer *hallucinates* an install: `unit8co/darts` (Tier 1, A2)

Inside the sandbox the agent does the right thing — a big editable install:

```
[Action]
pip install -e ".[torch,notorch,dev,optional]"
[Container ID: ab9d37ae46c4]
Executing: pip install -e ".[torch,notorch,dev,optional]"
```

But `pyproject.toml` contains a **comment** aimed at human `uv` users:

```
# For uv users: uv sync --group dev-all (includes all extras)
```

The synthesizer lifts that comment into a `RUN` instruction — and **discards the agent's real `pip install`**. The eval build then dies because `uv` was never installed:

```
#9 [6/7] RUN uv sync --group dev-all
#9 0.267 /bin/sh: 1: uv: not found
#9 ERROR: process "/bin/sh -c uv sync --group dev-all" did not complete successfully: exit code: 127

Dockerfile:18
  17 |     # Agent's verified setup instructions
  18 | >>> RUN uv sync --group dev-all
```

Note the comment the synthesizer wrote above it: *"# Agent's verified setup instructions"* — except this was never what the agent ran. **RAT** just used pip and passed: `pip install -e "/repo[dev,optional]"` → `pip install "darts[torch]"` → full run 1305 s, returncode 0.

**Lesson:** the synthesizer must emit RUN steps **only** from the agent's ordered successful mutating actions — never from repo comments/README content.

### W3 — The synthesizer *drops* the install: `django-oauth-toolkit` (Tier 1, A3) → 0 tests

Step 4, the agent's first install attempt is piped through `tail`, which the sandbox **preflight rejects**:

```
python3 -m pip install -e ".[test]" 2>&1 | tail -20
Command rejected before execution by sandbox preflight.
[SYSTEM] COMMAND REJECTED ... must not pipe output through `head`, `tail`, or `grep` ...
```

The agent immediately re-runs it **clean**, and it **succeeds** (Django 6.0.6 + all test deps install, snapshot committed):

```
python3 -m pip install -e ".[test]"
[Container ID: 87575d242208]
Command succeeded.
```

But the synthesizer remembers only the *rejected* form, marks the install `excluded`, and ships `build_commands: []`. The eval image is just a bare clone + pytest:

```
#9 [6/6] RUN pip install --no-cache-dir pytest
```

So collection cannot even import the app:

```
ImportError while loading conftest '/testbed/tests/conftest.py'.
tests/conftest.py:7: in <module>
    from django import VERSION
E   ModuleNotFoundError: No module named 'django'
```

`LibreTranslate` is the same bug with a twist: the dropped `pip install -e '.[test]'` also drops `pytest-cov`, and the repo's `addopts = ["--cov=libretranslate", ...]` then makes pytest exit 4 with *"unrecognized arguments: --cov"* and **0 collected**. **RAT** ran the identical install directly in its live container and got `pytest_pass_rate=1.0`.

**Lesson:** if a command was rejected for output-piping but **re-run successfully**, the synthesizer must key on the *final successful form*, never blank out installs.

### W4 — Wrong base image + Alpine apt preamble: `Tecnativa/docker-socket-proxy` (Tier 2)

The ImageSelector correctly chose a Python base, and the agent ran in it:

```
[ImageSelector] Selected base image: python:3.8
[DockerAgent] Selected base image: python:3.8
```

But the synthesizer overrode `FROM` with the repo's **production** image and bolted on the Debian apt block:

```
FROM haproxy:3.2.4-alpine
WORKDIR /testbed
# Configure apt reliability for eval image builds
RUN printf '%s\n' 'Acquire::Retries "5";' ... > /etc/apt/apt.conf.d/99jayint-retries
```

Alpine has no such path, so it fails on the **first** RUN:

```
#6 0.243 /bin/sh: can't create /etc/apt/apt.conf.d/99jayint-retries: nonexistent directory
#6 ERROR: process "..." did not complete successfully: exit code: 1
```

`weibo-crawler` is identical with `FROM python:3.12.0-alpine`. **RAT** kept `python:3.10-slim` (Debian) and passed.

**Lesson:** `FROM` must come from the ImageSelector's choice, never from a Dockerfile found in the repo tree; and the apt-hardening block must be guarded behind a "is this Debian/Ubuntu?" check.

### W5 — `collect-only` false-pass, no compiled binary: `Yelp/dumb-init` (Tier 3)

dumb-init is a **C** project; its tests exec the compiled binary. The agent installs the Python wrapper, collects 182 tests, and concludes:

```
[Finished] Agent has reached a conclusion.
Thought: All 182 tests collected successfully. The environment is fully configured.
```

The Makefile clearly needs a compile step the agent never ran:

```
build: VERSION.h
	$(CC) $(CFLAGS) -o dumb-init dumb-init.c
```

So at real test time, 172/182 fail:

```
E   FileNotFoundError: [Errno 2] No such file or directory: 'dumb-init'
```

**RAT** read the Makefile/conftest, compiled it, and put it on PATH:

```
apt-get install -y -qq gcc
gcc -std=gnu99 -static -s -Wall -Werror -O3 -o dumb-init dumb-init.c   returncode 0
cp /repo/dumb-init /usr/local/bin/
python3 /home/tools/run_pytest.py                                      returncode 0
```

**Lesson:** `--collect-only` never invokes the binary, so it can't catch a missing compile. Verify by running the tests.

### W6 — `collect-only` false-pass, service dies + timeout: `rq` and `pre-commit` (Tier 3)

`rq` needs Redis. The agent starts it **in-session**, collects 573 tests, and certifies:

```
Thought: All 573 tests collected successfully ... Redis running and all dependencies installed.
Verification Bundle:
{"runtime_preparation_commands": [], "test_commands": ["pytest --collect-only -q --disable-warnings"]}
```

It writes an entrypoint that daemonizes Redis — but `redis-server --daemonize yes` doesn't survive in the eval container, so:

```
E   redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused.
```

345 tests fail. **RAT** started Redis in the *same shell session* that ran pytest and passed 556. (There's also a harness bug: the runner4 eval bypasses the `eval_script` where the agent put the Redis start.)

`pre-commit` shows the timeout face of the same problem. The agent certifies with `820 tests collected in 0.69s`, but the eval's `run_pytest.py` **ignores the agent's test command** and always runs the full suite:

```
cmd = ["python", "-m", "pytest"]; cmd.extend(["-v","--tb=short","--continue-on-collection-errors", ...])
```
```
"status": "timeout", "failure_reason": "docker_timeout",
"error": "... '/run_pytest.py' timed out after 599.99 seconds"
```

pre-commit's 820 integration tests (git hook installs, subprocess spawns) can't finish in 600 s. **RAT** ran the full suite during its agent phase (255 s, 753/820 pass) and recorded a real result.

**Lesson (two-part):** (1) the agent must verify with a real run, not collect-only; (2) the harness's `run_pytest.py` should honor the agent's verified `test_commands` (and add `--maxfail`/`--timeout` caps) instead of always cold-running everything.

---

## 7. What RAT got right (the architectural contrast)

Across the 21 repos RAT solved and DockerAgent lost, RAT's advantage is the **same two things every time**, and neither is model quality:

1. **One persistent live container, no re-synthesis.** RAT installs directly in the container and *that* is the artifact. It cannot "drop the install" because there is no second pass that re-reads the trajectory — what ran is what ships. This single property eliminates Tier 1 (9 repos) and Tier 2 (3 repos) outright.
2. **It runs the full test suite during the agent phase, then iterates.** Because RAT *sees* real failures, it fixes them inside the loop:
   - compiles the C binary (`dumb-init`),
   - starts the service in-session (`rq`),
   - writes a stub `conftest.py` for a missing fixture (`Argus`: `@pytest.fixture def ns_host(): return "test_ns_host"`),
   - writes a **scoped replacement test** when the repo's test deep-imports something uninstallable (`D-FINE`: a minimal `tests/test_core_imports.py`),
   - even **synthesizes a test** when the repo has none (`weibo-crawler`).

DockerAgent's ReAct agent is capable of all of these — but its `collect-only` certificate lets it stop *before* it would discover it needs to, and its synthesizer then discards what it did do.

> Net: **the gap is the pipeline shape (sandbox→re-synthesize→rebuild + collect-only certificate), not the LLM.** Strip the credit-wall artifact and fix those two things and DockerAgent's reachable coverage is far closer to RAT than `0.18 vs 0.62` suggests.

---

## 8. Prioritized fixes (mapped to the redesign)

| P | fix | repos unblocked | maps to |
|---|---|---:|---|
| **P0 (exp.)** | Top up credits; cap ImageSelector `max_tokens`; pre-flight batch credit check; stop ImageSelector after first 402; **re-run runner4** | 16 | runner/infra, not product |
| **P0 (product)** | **Synthesizer faithfulness:** derive RUN steps *only* from the ordered list of the agent's successful, environment-mutating actions; include prerequisite tool installs (`pip install uv` before `uv sync`); never read repo comments/README into RUN; treat a rejected-then-re-run command by its successful form | 9 | redesign §3 — derive Dockerfile from **host-certified `EnvState`**, not from re-reading the transcript |
| **P0 (product)** | **Real verification certificate:** agent must run the full (or representative, capped) suite before declaring success; host probe certifies actual pass, not collection; start services in-session; compile native binaries; add fixtures | ~6 | redesign §2 / "no silent false pass" — only a host probe may write `status=PRESENT` |
| **P1** | `FROM` = ImageSelector image, never the repo's production Dockerfile; guard the apt preamble behind a Debian/Ubuntu check | 3 | synthesizer base-image policy |
| **P1 (harness)** | `run_pytest.py` must honor the agent's verified `test_commands`; don't bypass the `eval_script` (Redis); add `--maxfail`/per-test timeout | rq, pre-commit, websockets | eval harness |
| **P2 (infra)** | RT-DETR containerd `lchown` on ~2 GB torch layer: retry the commit, or use a CUDA base image, or checkpoint snapshots less often | 1 | host/sandbox |

**The redesign earns its keep here.** Its one rule — *only host code running a probe may certify `PRESENT`; the LLM may only propose* — is the direct structural fix for **both** dominant genuine failure modes: a host-certified `EnvState` can't "drop" or "hallucinate" an install (the Dockerfile is generated from certified facts, not a re-read transcript), and it can't false-pass on `collect-only` (the probe is the real test run). Tiers 1 + 3 are ~15 of the 27 genuine failures.

---

## 9. Appendix — provenance & raw artifacts

- **Structured findings (machine-readable):** `dockeragent_failure_findings.json` (this dir) — 50 per-repo findings, 21 contrasts, 4 verifications, with verbatim excerpts.
- **Adversarial verifications:** all four top root causes returned `partially_confirmed` — the *patterns* hold on the raw logs, with per-repo mechanism corrections folded into §5–§6 above (e.g. `darts`'s commit crash is secondary to the `uv` hallucination; `Scrapling`'s loop claim was overstated — it did issue a valid action).
- **VM raw logs (read-only):** `167.233.64.96:/opt/rat-bench-integration/rat_run_runner4/output/<owner>/<repo>/run.log` (+ `_result_row.json`, `eval_build/Dockerfile`, recipe `<owner>__<repo>.json`). RAT baseline at `rat_run_rat_corrected/...`, repo2run at `rat_run_repo2run/...`.
- **This analysis is read-only.** No benchmark run was started, killed, or modified.
