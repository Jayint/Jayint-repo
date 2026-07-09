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

def test_parse_patch_with_markdown_bold_label():
    # deepseek drifts to markdown: `**Script:**` instead of `Script:` — must still parse.
    a = parse_action("We need six.\n\n**Script:**\n\n```bash\napt-get install -y libpq-dev\npip install six\n```")
    assert a.kind == "patch" and "libpq-dev" in a.new_script

def test_parse_patch_bare_fenced_block_no_label():
    a = parse_action("Here is the updated script:\n```bash\npip install six\n```")
    assert a.kind == "patch" and "pip install six" in a.new_script

def test_parse_patch_unlabeled_fence():
    a = parse_action("```\npip install six\n```")
    assert a.kind == "patch" and "pip install six" in a.new_script

def test_python_fence_does_not_hijack_explore():
    # a ```python snippet is not a shell script; the Action line should win.
    a = parse_action("Action: ls\n```python\nprint('hi')\n```")
    assert a.kind == "explore" and a.command == "ls"

def test_wrapped_action_in_fence_recovered_as_explore():
    # Bug B: MiniMax wrapped a read-only probe in a ```bash fence. Accepting it as a patch would
    # replace the whole setup.sh with `Action: cat …` (a non-bash line) → build corruption.
    a = parse_action("Thought: inspect deps\n```bash\nAction: cat /app/pyproject.toml\n```")
    assert a.kind == "explore" and a.command == "cat /app/pyproject.toml"

def test_bare_readonly_probe_in_fence_recovered_as_explore():
    # The degenerate follow-on shape: a lone read-only investigation command in a fence.
    a = parse_action("```bash\nfind /app -maxdepth 3 -name 'pyproject.toml' | head -30\n```")
    assert a.kind == "explore" and a.command.startswith("find /app")

def test_shebang_then_action_in_fence_is_explore():
    # A shebang is a comment line; the single meaningful line is still the Action directive.
    a = parse_action("```bash\n#!/usr/bin/env bash\nAction: ls /app\n```")
    assert a.kind == "explore" and a.command == "ls /app"

def test_single_line_install_stays_patch():
    # A one-line INSTALL is a genuine (if minimal) build change — not read-only, stays a patch.
    a = parse_action("```bash\npip install six\n```")
    assert a.kind == "patch" and "pip install six" in a.new_script

def test_echo_oneliner_stays_patch():
    # `echo` is read-only but NOT an investigation probe — a lone script statement stays a patch
    # (guards the probe allowlist against over-catching; preserves patch-wins semantics).
    a = parse_action("```bash\necho hi\n```")
    assert a.kind == "patch" and "echo hi" in a.new_script

def test_multiline_mixed_block_stays_patch():
    # A block with an install line is a real (mixed) script even if it opens with a read-only probe.
    a = parse_action("```bash\ncat /app/pyproject.toml\npip install -e .\n```")
    assert a.kind == "patch"

def test_multiline_all_readonly_probe_block_is_invalid_gitingest():
    # Bug C (concurrency run, gitingest): a fenced block of ONLY read-only version probes must NOT
    # replace setup.sh — it installs nothing, so the build "succeeds" with an empty env (false green).
    text = ('```bash\n'
            'python -m pip --version 2>&1 || echo "pip not available"\n'
            'python -m pytest --version 2>&1 || echo "pytest not installed"\n'
            '```')
    assert parse_action(text).kind == "invalid"

def test_multiline_all_readonly_probe_block_is_invalid_ingestr():
    # Bug C (ingestr): find + cat probes wrapped as a Script.
    text = ('```bash\n'
            'find /app -maxdepth 2 -name "*.toml" 2>/dev/null | head -20\n'
            'cat /app/pyproject.toml 2>/dev/null | head -50\n'
            '```')
    assert parse_action(text).kind == "invalid"

def test_multiline_readonly_check_before_install_stays_patch():
    # The common real seed pattern: a read-only guard whose OR-tail installs. The line contains
    # `pip install` so it is NOT read-only → the block is a genuine patch.
    text = ('```bash\n'
            'python -c "import pytest" || pip install pytest\n'
            'pip install -e .\n'
            '```')
    assert parse_action(text).kind == "patch"

def test_extract_thought():
    assert extract_thought("Thought: the header is missing\nAction: ls") == "the header is missing"
