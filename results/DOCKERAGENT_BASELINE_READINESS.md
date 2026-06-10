# DockerAgent Baseline Readiness + Repair-Loop Verdict

Date: 2026-06-07
Author: subagent investigation (read-only on VM; unit tests local-only)
Scope: Confirm whether the DockerAgent (our-agent) RAT baseline can be run on the VM,
whether the Dockerfile post-synthesis repair loop is working, and produce an isolated
execution plan that does NOT disturb the live RAT re-run (PID 917900).

Labeling: every claim is tagged (verified) with file:line or command output, or
(unverified/inferred). Confidence is stated explicitly.

---

## 0. Live-run safety status (verified)

- PID 917900 is ALIVE, 23-26 min elapsed at time of check (verified: `ps -p 917900`).
  Command line:
  `/opt/rat_venv/bin/python run_rat_benchmark.py --model rat --repos-json datasets/rat_python_hard_subset.json --root-path ./rat_run_rat_corrected --concurrency 12 --timeout 7800 --num-turn 30 --llm deepseek/deepseek-v4-flash`
- It is `--model rat` (the paper's RATModel), NOT our `--model dockeragent` path (verified).
  => The DockerAgent repair loop is NOT executing in the live run. None of the smoke/baseline
  code paths overlap with what 917900 runs, EXCEPT the shared docker daemon + disk + the
  run_rat_benchmark.py module.
- Progress: 16/50 `_result_row.json` written, 28/50 output dirs started (verified: find/ls).
  Live output dir `rat_run_rat_corrected/` is 478M and growing (verified: du).
- 12 docker containers active (== concurrency 12) (verified: `docker ps -q | wc -l`).
- Load avg 3.99 / 7.40 / 8.11 (verified: uptime). All run_rat procs belong to the SAME
  corrected run; no competing benchmark (verified: ps grep of root-path/model).
- Disk: 225G total, 146G used (68%), 71G free; inodes 15% used (verified: df -h / df -i).

NO action was taken that could disturb 917900. All VM commands were read-only
(ps/ls/cat/head/grep/md5sum/df/du/docker ps + a python import that builds/runs NO containers).

---

## 1. can_run_on_vm: CONDITIONAL (YES, but only AFTER 917900 finishes)

The code is ready and deployed; the ONLY blocker is resource contention with the live run.

### Readiness (verified)
- VM code == local current-branch code. All 5 key files have IDENTICAL md5 between
  `/opt/rat-bench-integration/*` (VM) and `/Users/john/rat-bench-integration/*` (Mac) (verified):
  - agent.py                      d8966322dba295615643419930e74ad8
  - src/recipe_repair.py          d2db700b102f3b86f544999d7a12fcd9
  - src/artifact_verify.py        9c52e381235a1755d2c9dc5baff63f40
  - multi_docker_eval_adapter.py  d297c19021e819c32e4f4136b1215801
  - run_rat_benchmark.py          82644c2ffe698592d8f5698cb4043ac8  (matches even though
    git shows it locally as " M" / untracked-modified — content is already on the VM)
  => NO DEPLOY NEEDED. (This is a change from older notes; run_rat_benchmark.py is now in sync.)
- dockeragent_model.py provenance: OUR shim, not harness-stock RAT (verified). Header:
  "DockerAgentModel — plugs our DockerAgent into the RAT eval harness". It imports
  `MultiDockerEvalAdapter` from `DOCKERAGENT_ROOT` (our repo). md5 0664639205af57f9feeda297a2387525.
  It routes through `multi_docker_eval_adapter.process_single_instance` -> `DockerAgent`
  (agent.py) with DEFAULT repair flags => the dockeragent path runs WITH the repair loop on.
- Import-level check on VM (NO docker): with DOCKERAGENT_ROOT + RAT_ROOT set, rat_venv imports
  `run_rat_benchmark`, `DockerAgentModel`, `verify_and_repair_recipe`,
  `repair_recipe_for_missing_modules`, `normalize_repaired_recipe` => `IMPORT_OK` (verified).
- LLM repair tier reachable: `/opt/rat-bench-integration/.env` exists with key NAMES
  LLM_API_PROVIDER, OPENROUTER_API_KEY, OPENROUTER_API_BASE, OPENROUTER_PROVIDER (verified;
  values NOT printed). Deterministic tier needs no LLM.
- RAT_ROOT auto-resolution: there is NO repo-local `runanything/src`; the runner's sibling
  candidate `os.path.dirname(repo)/runanything/src` = `/opt/runanything/src` EXISTS and is what
  resolves (verified). The smoke/baseline should still pass RAT_ROOT explicitly for determinism.

### Blockers (precise)
1. (verified, HARD) Resource contention: 12 live containers + the docker daemon + disk I/O +
   network are saturated by 917900. Starting any docker build/run now would oversubscribe the
   daemon and could slow / perturb the live run. => MUST wait for 917900 to finish.
2. (verified, SOFT) Disk headroom: 71G free / 68% used; a 27.6G PyTorch image is the largest
   consumer. A 50-repo DockerAgent baseline building fresh per-repo images can push disk fill.
   Monitor; prune dangling images between waves if needed.
3. (NOT a blocker) Deploy: none needed (md5 identical). dockeragent_model.py provenance: ours,
   correct, points at current agent/src (verified).

---

## 2. repair_loop_verdict: WORKING_WITH_CAVEATS

Three evidence streams, stated separately:

### Stream A — code-trace correctness: SUPPORTS (verified, HIGH)
Verified by reading current-branch files (md5-identical on VM):
- agent.py:131-132 defaults `enable_post_synthesis_repair=True`, `self_verify_max_rounds=2`;
  stored at :136-137. multi_docker_eval_adapter.py constructs DockerAgent WITHOUT overriding
  these (input trace :764) => the dockeragent path runs with the loop ON by default.
- Call sites agent.py:971 and :995 invoke `_self_verify_and_repair` after Dockerfile synthesis
  (both the normal finalize and the transient-LLM-error finalize path).
- Method agent.py:1167; gated at :1175 (enable flag) and :1177 (verified_test_command);
  calls `verify_and_repair_recipe(... max_rounds=self_verify_max_rounds)` (:1181-1195);
  import at agent.py:16.
- Orchestrator src/artifact_verify.py:321; `range(max_rounds+1)` = 3 iterations (:357);
  renders a self-contained clean-room Dockerfile (git clone injected), builds a FRESH image,
  runs the project's OWN verified test command, classifies effectiveness (:105-150).
  resolved+break at :401-405; skipped_no_test_command guard at :346-347.
- The "never drop the test command" guard is REAL (verified, recipe_repair.py:372-373):
  `if not _normalize_commands(repaired.get("test_commands")): return None` — an LLM repair that
  removed/weakened the test command yields None and is NOT applied. Deterministic repair
  (recipe_repair.py:218-242) ONLY appends `pip install ...` to build_commands; it physically
  cannot touch test_commands.
- Deterministic module resolution (recipe_repair.py:165-185, 218-242): missing import ->
  prefer repo-declared requirement (requirements*.txt / poetry.lock) -> known-fallback map
  (ppocr->paddleocr) -> bare module name; appended deduped (recipe_repair.py:202-215).

### Stream B — local unit tests: SUPPORTS (verified, HIGH)
`python3 -m pytest tests/test_recipe_repair.py tests/test_artifact_verify.py -q`
=> 36 passed in 0.10s, 0 failed, NO docker invoked (verified locally, Python 3.12.10, darwin).
Coverage includes: missing-module extraction/dedup, pip-name normalization, requirements pin
resolution, deterministic-repair immutability+dedup+pin-use, never-drop-test-command guard
(`test_rejects_when_test_commands_emptied`), LLM-repair success/error paths, classify for
rc=0 / rc=5 / internal-import / ran-with-failures / missing-third-party, and the orchestrator
end-to-end with Docker mocked: skipped_no_test_command, hollow->resolved via deterministic
repair, unresolved, and build-failure paths.
Caveat: orchestrator tests MOCK docker build/run. They prove control flow + classification +
adoption logic; they do NOT exercise real image builds.

### Stream C — prior-run artifacts (k8): DOES NOT SUPPORT (loop never ran) (verified, HIGH)
- The repair loop NEVER FIRED in the only prior DockerAgent run (k8, results/dockeragent/2026-06-06-k8/).
  Evidence (verified): grep for '[Self-Verify]' / 'verify_and_repair_recipe' / '_self_verify_and_repair'
  across all 50 run.log + 50 per-repo JSON => 0 matches; artifact_repair_rounds=[] for all 50.
- Root cause is TIMESTAMP, not a bug (verified): src/recipe_repair.py and src/artifact_verify.py
  were created 2026-06-06 01:41-01:56 UTC; the k8 run completed 2026-06-05 15:21 UTC — the repair
  code DID NOT EXIST during k8. So k8 cannot confirm OR refute runtime behavior.
- k8 instead demonstrates the PROBLEM the loop targets: 31/50 repos used a collect-only
  verified_test_command (hollow); of 28 status=success, 15 had pass_rate=0, 13 with
  ModuleNotFoundError; _analysis_summary.json byRootCause = {test_deps_not_installed:11,
  editable_install_missing:7, dockerfile_synthesis_malformed:9}. 18 of these (test_deps +
  editable_install) are exactly the deterministic/LLM repair target class (verified).

### Net verdict: WORKING_WITH_CAVEATS
Two of three streams (code-trace + unit tests) positively support a correct, enabled,
guard-protected repair loop. The third (prior artifacts) is silent because the code postdates
the only prior run — it neither confirms nor contradicts. No live end-to-end build has ever
exercised the loop.

### repair_loop_confidence: HIGH on design/enablement/guard; MEDIUM on real-world firing+resolving
- HIGH (verified): the loop is wired, enabled-by-default on the dockeragent path, bounded to
  3 build attempts, and the LLM tier cannot drop the test command.
- MEDIUM/UNVERIFIED: that it actually FIRES and RESOLVES on a real repo with a real docker build.

### unverified_gap (only a live end-to-end smoke can confirm)
1. That a real clean-room image builds from the synthesized recipe and the project's verified
   test command actually runs inside it (no mock).
2. That a real ModuleNotFoundError is detected, deterministic `pip install <pkg>` is appended,
   the image is REBUILT, and the round flips to effective=True (status=resolved, changed=True),
   and the agent adopts the repaired Dockerfile (agent.py:1202-1207).
3. That `self.verified_test_command` is set to an EFFECTIVE command at runtime (not collect-only).
   If it is collect-only, classify may call round 0 "effective" and zero repair rounds run — the
   loop cannot retroactively upgrade a hollow verified command (agent.py:1177 / artifact_verify.py:346).
4. That the OpenRouter LLM tier is reachable at runtime when the deterministic tier adds nothing.

---

## 3. live_smoke_plan (run ONLY AFTER 917900 finishes)

Goal: a single-repo DockerAgent run that DELIBERATELY triggers a repair round, fully isolated
from the live run's output tree.

### Preconditions to check FIRST (all read-only)
1. RAT live run is DONE:
   `ssh root@167.233.64.96 'ps -p 917900 -o pid= || echo RAT_DONE'`  => expect RAT_DONE
   and `ps aux | grep run_rat_benchmark | grep -v grep | wc -l`     => expect 0
2. No live containers:  `ssh root@167.233.64.96 'docker ps -q | wc -l'`  => expect 0 (or only
   unrelated long-lived containers you recognize)
3. Disk headroom:       `ssh root@167.233.64.96 'df -h / | tail -1'`     => want >= ~40G free.
   If tight, prune dangling: `docker image prune -f` (only after RAT done; this is a write op).

### Repo choice (verified rationale)
PRIMARY: `D4Vinci/Scrapling` — k8 showed the synthesizer emitted only `pip install requests`,
dropping `pip install -e ".[all]"` + test reqs => 94 ModuleNotFoundError on collection
(editable_install_missing). This guarantees round 0 is ineffective => a repair round fires.
ALTERNATE (deterministic-clean): `EnableSecurity/wafw00f` — test_deps_not_installed; missing
third-party (responses/pytest-mock declared in `[dev]`) resolves cleanly via the deterministic
tier (requirements-pin path), a cleaner demonstration that round flips to resolved.

Caveat (verified from recipe_repair.py:165-185): for an editable own-package miss like
`scrapling`, the deterministic tier falls back to bare `pip install scrapling` (PyPI), which
triggers a round but may differ from the editable install; the LLM tier can overlay the
correct editable command. wafw00f is the better choice if the goal is a clean deterministic
resolve; Scrapling is the better choice if the goal is "prove the loop fires on the known bug".

### Exact isolated commands (separate root, concurrency 1, fresh dir)
```bash
# On the VM, ONLY after RAT_DONE. Separate --root-path so it never touches rat_run_rat_corrected/.
ssh root@167.233.64.96
cd /opt/rat-bench-integration

export DOCKERAGENT_ROOT=/opt/rat-bench-integration
export RAT_ROOT=/opt/runanything/src          # explicit (sibling auto-default also resolves here)

# Single-repo smoke, isolated output dir, dockeragent path, same LLM as the baseline:
/opt/rat_venv/bin/python run_rat_benchmark.py \
  --only D4Vinci/Scrapling \
  --model dockeragent \
  --root-path ./dockeragent_smoke \
  --repos-json datasets/rat_python_hard_subset.json \
  --llm deepseek/deepseek-v4-flash \
  --timeout 7800 \
  --num-turn 30 \
  2>&1 | tee ./dockeragent_smoke/_smoke.log
```
(`--only` runs sequentially => concurrency 1. `./dockeragent_smoke` is created fresh and is
distinct from `./rat_run_rat_corrected`.)

### What to grep for AFTER the smoke (confirms the loop fired + resolved)
```bash
grep -n "\[Self-Verify\]" ./dockeragent_smoke/_smoke.log                 # round logs => loop fired
python3 - <<'PY'
import json,glob
for p in glob.glob("./dockeragent_smoke/output/**/_result_row.json", recursive=True):
    r=json.load(open(p))
    print(p, "repair_rounds=", r.get("artifact_repair_rounds"), "status=", r.get("status"))
PY
# Inspect the adopted Dockerfile for the appended pip install (the repair):
find ./dockeragent_smoke/output -name Dockerfile -exec grep -n "pip install" {} +
```
Success signals (the unverified_gap closes if observed): `[Self-Verify] Round N` appears;
artifact_repair_rounds is non-empty; the adopted Dockerfile contains the install the synthesizer
had dropped; round status transitions to resolved.

---

## 4. baseline_plan (full 50-repo DockerAgent baseline, head-to-head with RAT)

Run AFTER 917900 finishes, mirroring the live RAT run's knobs for a fair comparison.

### Match the live RAT run exactly (verified from 917900 cmdline)
- model: `--model dockeragent`  (the only difference vs the live run, which used `--model rat`)
- llm: `--llm deepseek/deepseek-v4-flash`   (SAME as live run; note runner DEFAULT is
  `deepseek-chat` at run_rat_benchmark.py:644, so the flag MUST be passed explicitly)
- concurrency: `--concurrency 12`   (SAME)
- timeout: `--timeout 7800`         (SAME)
- num-turn: `--num-turn 30`         (SAME)
- repos: `--repos-json datasets/rat_python_hard_subset.json`  (SAME 50 repos)
- root-path: a NEW dir, e.g. `--root-path ./rat_run_dockeragent_corrected`  (DISTINCT from
  rat_run_rat_corrected so the two result sets are side-by-side and comparable)

### Exact command
```bash
ssh root@167.233.64.96
cd /opt/rat-bench-integration
export DOCKERAGENT_ROOT=/opt/rat-bench-integration
export RAT_ROOT=/opt/runanything/src

/opt/rat_venv/bin/python run_rat_benchmark.py \
  --model dockeragent \
  --repos-json datasets/rat_python_hard_subset.json \
  --root-path ./rat_run_dockeragent_corrected \
  --concurrency 12 \
  --timeout 7800 \
  --num-turn 30 \
  --llm deepseek/deepseek-v4-flash \
  2>&1 | tee ./rat_run_dockeragent_corrected/_run50.log
```
Deploy: NOT needed (md5 identical, verified). dockeragent_model.py already present and correct.

### During/after
- Monitor disk every wave: `df -h /`. Prune dangling images between waves if free < ~30G
  (`docker image prune -f`). The 27.6G PyTorch base will be reused, not rebuilt per-repo.
- Aggregate: `run_rat_benchmark.py --aggregate-only --root-path ./rat_run_dockeragent_corrected`.
- Compare to the corrected RAT run at ./rat_run_rat_corrected (same llm/concurrency/timeout/
  num-turn/repos) for an apples-to-apples DockerAgent-vs-RAT head-to-head.

Recommendation: run the single-repo SMOKE first (section 3) to confirm the repair loop fires
end-to-end on a real build, THEN launch the full baseline. This closes the unverified_gap before
spending the full 50-repo run.

---

## 5. go_no_go (running anything NOW)

NO live run now. (verified rationale)
- 917900 is actively using the docker daemon (12 containers), disk, and network. Any
  docker build/run started now would oversubscribe the daemon and risk perturbing the live
  RAT re-run — and would itself be slow/unreliable under contention.
- The smoke and baseline are GO only after the precondition checks in section 3 pass
  (RAT_DONE + 0 live containers + disk headroom).
- All investigation here was read-only and did not touch the live run.

---

## 6. Risks
1. (HIGH) Running the smoke/baseline before 917900 completes contends for the docker daemon and
   could perturb the live RAT re-run and/or fail under load. Mitigation: wait for RAT_DONE.
2. (MEDIUM) Disk fill: 50 fresh per-repo image builds on top of a 27.6G PyTorch base at 71G
   free could exhaust disk mid-run. Mitigation: monitor df, prune dangling images between waves.
3. (MEDIUM) Hollow verified_test_command at runtime: if the agent sets a collect-only
   verified_test_command, round 0 may classify as effective and zero repair rounds run — the
   loop cannot upgrade a hollow command (agent.py:1177 / artifact_verify.py:346). The smoke
   should confirm the agent now produces an effective (not collect-only) command for the chosen
   repo. (This is the most important behavior to verify live.)
4. (MEDIUM) Editable-own-package repair: deterministic tier may append `pip install <pkg>` (PyPI)
   instead of the editable install; relies on the LLM tier for the exact editable command. The
   guard prevents test-command loss but not a sub-optimal build fix. Confirm on the Scrapling smoke.
5. (LOW) LLM tier reachability at runtime: deterministic tier needs no LLM, but the LLM overlay
   needs OpenRouter. .env keys are present (names verified); runtime reachability unverified.
6. (LOW) `--llm` default is `deepseek-chat`, not the comparison model. Forgetting `--llm
   deepseek/deepseek-v4-flash` would silently break head-to-head comparability. Always pass it.

---

## 7. Evidence index (commands actually run)
- LOCAL: `python3 -m pytest tests/test_recipe_repair.py tests/test_artifact_verify.py -q` => 36 passed.
- LOCAL: `md5 -q agent.py src/recipe_repair.py src/artifact_verify.py multi_docker_eval_adapter.py run_rat_benchmark.py`.
- LOCAL: grep of agent.py / recipe_repair.py / artifact_verify.py / run_rat_benchmark.py for the cited line refs.
- LOCAL: parsed results/dockeragent/2026-06-06-k8/_analysis_summary.json (50 instances, fixability/root_cause).
- VM (read-only): `ps -p 917900`, full ps of run_rat tree, md5sum of 5 files + dockeragent_model.py,
  rat_venv import check (NO docker), df -h / df -i / docker ps -q | wc -l / docker images -q | wc -l,
  du of rat_run_rat_corrected, dataset repo count, RAT_ROOT path existence, .env key NAMES (no values),
  result-row count (16/50) + output dir count (28/50), uptime.
