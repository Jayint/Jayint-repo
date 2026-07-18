import sys, pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.agent.actions import parse_action, extract_thought, apply_edit, EditOp


def test_parse_explore():
    a = parse_action("Thought: check libs\nAction: ldconfig -p | grep pq")
    assert a.kind == "explore" and a.command == "ldconfig -p | grep pq"

def test_whole_script_block_without_edit_is_invalid():
    # edit-only: a fenced ```bash build script (no Edit: directive) is NOT a way to change setup.sh —
    # the loop re-prompts "use Edit". Whole-file rewrites (and the strip path they enabled) are gone.
    text = "Thought: add libpq\n```bash\napt-get install -y libpq-dev\npip install psycopg2\n```"
    assert parse_action(text).kind == "invalid"

def test_action_directive_wins_over_a_stray_fence():
    # A non-probe fenced block is not a valid move; an explicit Action directive alongside it wins.
    a = parse_action("Action: ls\n```bash\necho hi\n```")
    assert a.kind == "explore" and a.command == "ls"

def test_unparseable_is_invalid():
    assert parse_action("I think we should install stuff").kind == "invalid"

def test_markdown_script_block_is_invalid_not_a_patch():
    a = parse_action("We need six.\n\n**Script:**\n\n```bash\npip install six\n```")
    assert a.kind == "invalid"

def test_bare_fenced_install_block_is_invalid():
    assert parse_action("Here is the updated script:\n```bash\npip install six\n```").kind == "invalid"

def test_unlabeled_fenced_install_block_is_invalid():
    assert parse_action("```\npip install six\n```").kind == "invalid"

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

def test_single_line_install_block_is_invalid():
    # edit-only: a lone install line in a fence is not a valid move — use Edit to add it.
    assert parse_action("```bash\npip install six\n```").kind == "invalid"

def test_echo_oneliner_block_is_invalid():
    assert parse_action("```bash\necho hi\n```").kind == "invalid"

def test_multiline_mixed_block_is_invalid():
    # A block with an install line is a build-script fragment, not a probe → invalid (use Edit).
    assert parse_action("```bash\ncat /app/pyproject.toml\npip install -e .\n```").kind == "invalid"

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

def test_single_line_cd_probe_compound_recovered_as_explore_ezdata():
    # ezdata regression: a `cd … && find … && ls … && cat …` chain (every segment read-only) was
    # applied as a PATCH and overwrote the 188-install seed with a non-installing script that still
    # "built green" (false green). Its first token is `cd` (not an install/probe verb), so the old
    # first-token allowlist missed it. Driving off is_read_only per &&/||/; segment recovers it as
    # the explore the model meant — never a patch.
    text = ('```bash\n'
            'cd /app && find . -maxdepth 3 -type d | head -60 && echo "---" && ls -la '
            '&& cat pyproject.toml 2>/dev/null || true\n'
            '```')
    a = parse_action(text)
    assert a.kind == "explore" and a.command.startswith("cd /app")

def test_single_line_cd_probe_compound_recovered_as_explore_promnesia():
    text = ('```bash\n'
            'cd /app && cat pyproject.toml && cat requirements*.txt 2>/dev/null '
            '|| echo "no requirements.txt" && ls -la tests/ 2>/dev/null || echo "no tests dir"\n'
            '```')
    a = parse_action(text)
    assert a.kind == "explore" and a.command.startswith("cd /app")

def test_cd_then_install_compound_is_invalid_not_recovered():
    # A `cd … && pip install …` compound HAS a mutation (pip install) so it is NOT a read-only probe
    # → not recovered as explore; under edit-only it's invalid (use Edit), not a patch.
    assert parse_action("```bash\ncd /app && pip install -e .\n```").kind == "invalid"

def test_multiline_install_block_is_invalid():
    # A block that installs (real build-script fragment) is invalid under edit-only — use Edit.
    text = ('```bash\n'
            'python -c "import pytest" || pip install pytest\n'
            'pip install -e .\n'
            '```')
    assert parse_action(text).kind == "invalid"

def test_parse_markdown_bold_edit_directive():
    # Issue A: MiniMax decorates directives with markdown — `**Edit:**` (bold) instead of a bare
    # `Edit:`. The directive must still parse; otherwise the turn is wasted as `invalid` (the gitingest
    # run lost 5/24 turns this way). The closing `**` between the label and the verb is also tolerated.
    a = parse_action("**Thought:** add git\n\n**Edit:** insert after 3\n```bash\napt-get install -y git\n```")
    assert a.kind == "edit" and a.edit.verb == "insert" and a.edit.start == 3
    assert a.edit.content == "apt-get install -y git"

def test_parse_markdown_bold_edit_directive_real_minimax_gitingest():
    # Verbatim shape from the live gitingest MiniMax run (react_trace idx 2): a bold Edit with a
    # "line N" number and trailing prose, then the bash block. Was mis-parsed as invalid → wasted turn.
    reply = ("**Thought:** git CLI missing.\n\n"
             "**Action:** Add apt-get install -y git early in the script.\n\n"
             "**Edit:** Insert after line 8 (after the python symlink line) and before the pytest check:\n"
             "```bash\napt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1\n```")
    a = parse_action(reply)
    assert a.kind == "edit" and a.edit.verb == "insert" and a.edit.start == 8
    assert "install -y -qq git" in a.edit.content

def test_parse_heading_prefixed_edit_directive():
    # `### Edit: …` (a markdown heading) must parse the same as a bare `Edit:`.
    a = parse_action("### Edit: replace 2\n```bash\npip install narwhals\n```")
    assert a.kind == "edit" and a.edit.verb == "replace" and (a.edit.start, a.edit.end) == (2, 2)

def test_parse_markdown_bold_action_directive():
    # `**Action:** <cmd>` (bold) must parse as an explore, same as a bare `Action:`.
    a = parse_action("**Thought:** check\n\n**Action:** ls /app")
    assert a.kind == "explore" and a.command == "ls /app"

def test_markdown_bold_edit_wins_over_bold_action_prose():
    # The model emitted BOTH a bold Action (prose) and a bold Edit (the concrete change). Edit wins —
    # same precedence as bare directives — so the edit is applied, not the Action prose.
    reply = ("**Action:** Add git to the script.\n\n"
             "**Edit:** insert after 8\n```bash\napt-get install -y git\n```")
    a = parse_action(reply)
    assert a.kind == "edit" and a.edit.start == 8 and a.edit.content == "apt-get install -y git"

def test_parse_edit_replace_single_line_with_block():
    a = parse_action("Thought: pin\nEdit: replace 3\n```bash\npip install narwhals\n```")
    assert a.kind == "edit" and a.edit == EditOp("replace", 3, 3, "pip install narwhals")

def test_edit_uses_the_bash_block_after_the_directive():
    # A model may show an example fence in its prose BEFORE `Edit:`; the edit content must be the
    # block that FOLLOWS the directive, so only the intended bash is applied (not a stray earlier one).
    text = ("Thought: e.g. add a line like this\n```bash\necho stray\n```\n"
            "Edit: insert after 3\n```bash\napt-get install -y redis-server\n```")
    a = parse_action(text)
    assert a.kind == "edit" and a.edit.content == "apt-get install -y redis-server"

def test_parse_edit_replace_range():
    a = parse_action("Edit: replace 3-5\n```bash\npip install x\n```")
    assert a.kind == "edit" and a.edit.verb == "replace" and (a.edit.start, a.edit.end) == (3, 5)

def test_parse_edit_insert_after_with_block():
    a = parse_action("Edit: insert after 2\n```bash\napt-get install -y libpq-dev\n```")
    assert a.kind == "edit" and a.edit.verb == "insert" and a.edit.start == 2
    assert "libpq-dev" in a.edit.content

def test_parse_edit_delete_needs_no_block():
    a = parse_action("Thought: drop the bad pin\nEdit: delete 7")
    assert a.kind == "edit" and a.edit == EditOp("delete", 7, 7, "")

def test_parse_edit_tolerates_line_word():
    a = parse_action("Edit: delete lines 4-6")
    assert a.kind == "edit" and (a.edit.start, a.edit.end) == (4, 6)

def test_parse_edit_replace_without_block_is_invalid():
    # replace/insert must carry the new line(s); a bare Edit header can't be applied.
    assert parse_action("Edit: replace 3").kind == "invalid"

def test_edit_directive_wins_over_script_block():
    # An Edit: with a fenced block uses the block as the EDIT content, not a whole-script patch.
    a = parse_action("Edit: replace 2\n```bash\npip install six\n```")
    assert a.kind == "edit" and a.edit.content == "pip install six"

def test_apply_edit_replace_single_can_expand():
    assert apply_edit("a\nb\nc\n", EditOp("replace", 2, 2, "B1\nB2")) == "a\nB1\nB2\nc\n"

def test_apply_edit_replace_range():
    assert apply_edit("a\nb\nc\nd\n", EditOp("replace", 2, 3, "X")) == "a\nX\nd\n"

def test_apply_edit_insert_after_and_at_top():
    assert apply_edit("a\nb\n", EditOp("insert", 1, 1, "mid")) == "a\nmid\nb\n"
    assert apply_edit("a\nb\n", EditOp("insert", 0, 0, "top")) == "top\na\nb\n"

def test_apply_edit_delete_removes_lines():
    assert apply_edit("a\nb\nc\n", EditOp("delete", 2, 2, "")) == "a\nc\n"
    assert apply_edit("a\nb\nc\nd\n", EditOp("delete", 2, 3, "")) == "a\nd\n"

def test_apply_edit_out_of_range_returns_none():
    assert apply_edit("a\nb\n", EditOp("replace", 5, 5, "x")) is None
    assert apply_edit("a\nb\n", EditOp("insert", 9, 9, "x")) is None
    assert apply_edit("a\nb\n", EditOp("replace", 2, 1, "x")) is None   # end < start

def test_extract_thought():
    assert extract_thought("Thought: the header is missing\nAction: ls") == "the header is missing"

def test_extract_thought_stops_before_edit_directive():
    assert extract_thought("Thought: add redis\nEdit: insert after 3\n```bash\nx\n```") == "add redis"

def test_extract_thought_from_leading_prose_without_label():
    # the model wrote reasoning as plain prose then an Edit (no "Thought:" label) — capture the prose.
    reply = ("The tests fail because redis isn't running; add a local redis.\n\n"
             "Edit: insert after 54\n```bash\nredis-server --daemonize yes\n```")
    assert extract_thought(reply).startswith("The tests fail because redis isn't running")

def test_extract_thought_empty_when_reply_is_bare_directive():
    assert extract_thought("Edit: insert after 3\n```bash\nx\n```") == ""
