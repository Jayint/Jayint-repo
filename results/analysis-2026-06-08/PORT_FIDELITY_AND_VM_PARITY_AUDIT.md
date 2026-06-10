# Port Fidelity and VM Parity Audit — 2026-06-08

**Local HEAD:** ac9604f | **VM:** root@167.233.64.96:/opt/rat-bench-integration

## VM Parity: CLEAN

All 46 Python files SHA256-identical local vs VM. Four primary runner files confirmed:

| File | Hash (first 8) | Match |
|---|---|---|
| run_rat_benchmark.py | 185ffc8f | YES |
| repo2run_repair_port.py | 5955cf02 | YES |
| agent.py | d7afe987 | YES |
| multi_docker_eval_adapter.py | 6e0f2b65 | YES |

Both recent fix commits confirmed present on VM (b2e09cb trajectory-path fix, ac9604f gate fix). One stale 0-byte test.py persists (cosmetic, fix: ssh rm -f).

## Port Fidelity: NOT 100% FAITHFUL

Two confirmed gaps in repair-DECISION logic. All 69 verbatim symbols, all LLM prompts, all constants and regexes are byte-identical. Forbidden imports (src.recipe_repair, src.artifact_verify) are absent.

### Gap 1 — HIGH: ensure_eval_dockerignore_includes_test_artifacts missing
Source run_repo2run_benchmark.py:3389-3393 calls this function before the repair loop to rewrite .dockerignore and prevent test artifact exclusion. The port (repo2run_repair_port.py:2644) derives eval_build_context_path but never calls the function. Repos with .dockerignore files that exclude test directories will silently build without tests and report 0 tests collected, inflating benchmark pass rates.

### Gap 2 — MEDIUM: derive_verification_commands skips filter_runtime_preparation_commands
Source uses derive_repo2run_collect_commands (run_repo2run_benchmark.py:3386-3388) which applies filter_runtime_preparation_commands(). Port uses derive_verification_commands (repo2run_repair_port.py:2635) which skips this filter, potentially leaving collect-only commands in runtime_commands.

### Additional operational issues (not decision-logic drift):
- docker cp in junit_to_pytest_results is permanently dead (--rm destroys container before cp runs) — repair loop always scores via regex fallback; will corrupt scores when repair builds first succeed
- Repair unconditionally overwrites DockerAgentModel's accurate xml-parsed results with regex fallback when repair build succeeds — latent until first repair success
- run_pytest_results.json not written when all builds fail (test_execution is None) — spec says unconditional write
- docker_platform=None hardcoded — platform-specific repos may silently use wrong arch
- real_test_command drops cd <dir> && prefix silently
- deploy.sh untracked, no post-deploy hash assertion, patches/ untracked, runner output has no commit stamp

Full report: /Users/john/rat-bench-integration/results/analysis-2026-06-08/PORT_FIDELITY_AND_VM_PARITY_AUDIT.md