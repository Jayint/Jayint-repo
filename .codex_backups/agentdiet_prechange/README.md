This directory stores pre-change backups for the AgentDiet observation-compression work.

Base HEAD commit: `2f73c69`

Backed up existing files:
- `agent.py`
- `src/planner.py`
- `src/image_selector.py`
- `tests/test_agent_verification.py`
- `tests/test_synthesizer.py`
- `tests/test_adapter_logic.py`

Rollback plan:
1. Run `./.codex_backups/agentdiet_prechange/restore.sh`
2. If needed, remove any newly added files listed in that script
3. Re-run tests to confirm behavior matches the pre-change snapshot
