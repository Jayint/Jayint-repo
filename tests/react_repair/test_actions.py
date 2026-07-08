import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.react_repair.actions import parse_action, extract_thought


def test_parse_explore():
    a = parse_action("Thought: check libs\nAction: ldconfig -p | grep pq")
    assert a.kind == "explore" and a.command == "ldconfig -p | grep pq"

def test_parse_patch_full_script():
    text = "Thought: add libpq\nScript:\n```bash\napt-get install -y libpq-dev\npip install psycopg2\n```"
    a = parse_action(text)
    assert a.kind == "patch"
    assert "libpq-dev" in a.new_script and a.new_script.endswith("\n")

def test_patch_wins_over_action_when_both_present():
    a = parse_action("Action: ls\nScript:\n```bash\necho hi\n```")
    assert a.kind == "patch"

def test_unparseable_is_invalid():
    assert parse_action("I think we should install stuff").kind == "invalid"

def test_extract_thought():
    assert extract_thought("Thought: the header is missing\nAction: ls") == "the header is missing"
