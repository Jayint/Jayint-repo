import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("PI_RUN_GROUND_SMOKE"),
                    reason="needs Docker + the build_script_eval smoke corpus; set PI_RUN_GROUND_SMOKE=1")
def test_ground_one_end_to_end():
    from src.eval.graph_repair_ablation.ground_run import ground_one
    from src.eval.graph_repair_ablation.oracle import TEST_DOMAIN_INJECTIONS
    smoke_root = os.environ["PI_SMOKE_ROOT"]  # caller points at the fetched corpus
    rows = ground_one(TEST_DOMAIN_INJECTIONS[0], smoke_root=smoke_root)
    if not rows:
        pytest.skip("injection did not produce an import/collection failure in this env")
    arms = {r["arm"] for r in rows}
    assert arms == {"G", "B"}
    g = next(r for r in rows if r["arm"] == "G")
    assert g["score"]["grounded"] is True  # G must localize urllib3 -> pkg:urllib3
