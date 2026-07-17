"""Loop-scope envstate constants (ride to ``orchestrate/loop/`` in Phase 2 T5).

Kept as a dependency-free leaf so ``orchestrator`` and ``graph_scheduler`` share
the same objects without an import cycle. ``VERIFY_TEST_CMD`` now lives in the
neutral ``src.constants`` leaf (both the repair arm and the loop need it — src/
stage-refactor §4A decision #1); it is re-exported here so the loop's existing
importers and ``test_constants_single_source`` keep resolving one object.
"""
from __future__ import annotations

from src.constants import VERIFY_TEST_CMD  # noqa: F401  (canonical home: src/constants.py; re-exported for the loop)

# run_v3 no-progress bound: give up honestly once the VERIFY_TEST_CMD outcome
# signature is an identical FAILURE for this many consecutive cycles despite
# repair activity (design: residual-giveup-fix.md). Chosen == graph_scheduler
# attempt_cap so a single obligation gets its full retry budget before "even
# fully repairing it did not move the gate" can be concluded.
NO_PROGRESS_CYCLES: int = 3

# run_v3 residual-churn bound: give up honestly after this many consecutive
# cycles whose ONLY repair diagnosis was RESIDUAL (a test-logic / phantom
# obligation that no environment change can close) with no real ENVIRONMENT
# repair in between. Unlike NO_PROGRESS_CYCLES this does NOT depend on a stable
# pytest outcome signature — it is driven by the (reliable) repair-time
# diagnosis — so it converges even when pytest output is nondeterministic and
# a fresh phantom is minted every cycle (design: residual-node-drop.md).
RESIDUAL_GIVEUP_CYCLES: int = 3
