#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
BACKUP_DIR="$ROOT_DIR/.codex_backups/agentdiet_prechange"

cp "$BACKUP_DIR/agent.py" "$ROOT_DIR/agent.py"
cp "$BACKUP_DIR/src/planner.py" "$ROOT_DIR/src/planner.py"
cp "$BACKUP_DIR/src/image_selector.py" "$ROOT_DIR/src/image_selector.py"
cp "$BACKUP_DIR/tests/test_agent_verification.py" "$ROOT_DIR/tests/test_agent_verification.py"
cp "$BACKUP_DIR/tests/test_synthesizer.py" "$ROOT_DIR/tests/test_synthesizer.py"
cp "$BACKUP_DIR/tests/test_adapter_logic.py" "$ROOT_DIR/tests/test_adapter_logic.py"

# Remove new files added by the AgentDiet change set.
rm -f "$ROOT_DIR/src/observation_compressor.py"
rm -f "$ROOT_DIR/tests/test_observation_compressor.py"
rm -f "$ROOT_DIR/tests/test_planner_history.py"

echo "Restored backed-up files for AgentDiet change set."
