DEFAULT_LLM_MODEL = "MiniMax-M2.7-highspeed"
DEFAULT_MEMORY_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"

# Canonical execution-verify command used by the Phase-1 execution gate.
# The gate requires a bare interpreter (no venv wrapper) and >=1 passed test.
# `--continue-on-collection-errors` matches the ratbench OFFICIAL scorer: one un-importable module
# must not abort the whole session (strict `pytest -q` zeroed repos that had real passing tests,
# hiding progress from the agent and optimizing a different target than the benchmark scores).
# The react arm's per-cause error breakdown (pytest_summary) reads the FAILURES/ERRORS traceback
# sections, which pytest emits by default — no extra reporting flag is needed here.
# Neutral leaf home (both agent/ repair and orchestrate/ loop need it); see src/ stage-refactor §4A.
VERIFY_TEST_CMD: str = "python -m pytest -q --continue-on-collection-errors"
