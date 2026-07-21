import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# This directory carries __init__.py (tests/eval/ and tests/eval/graph_repair_ablation/
# are both packages), so pytest's rootdir walk stops at tests/ and never puts THIS
# directory on sys.path. test_ground.py's bare `from corpus_grounding import GCASES`
# needs its own directory on sys.path too (mirrors test_run_one.py's pattern of adding
# both repo-root and src/, not just src/) -- without this line the import fails with
# ModuleNotFoundError: No module named 'corpus_grounding', masking the intended RED
# (NotImplementedError from run_grounding) behind a collection error instead.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
