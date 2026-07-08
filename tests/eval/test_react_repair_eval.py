import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.eval.react_repair_eval.run_eval import run_one
from src.eval.react_repair_eval import scenarios as S
from src.react_repair.log import DESIGN


def test_all_scenarios_pass_and_cover_design():
    fired = set()
    for factory in (S.scenario_green, S.scenario_build_fail_then_patch,
                    S.scenario_tests_fail_then_patch, S.scenario_explore_then_patch,
                    S.scenario_unfixable_giveup, S.scenario_plateau):
        ok, f = run_one(factory.__name__, factory, silent=True)
        assert ok, factory.__name__
        fired |= f
    # every design point exercised except the LLM-only COMPRESS tag (covered in unit tests)
    assert set(DESIGN) - fired <= {"COMPRESS"}
