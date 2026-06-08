"""tests/test_toolcall_extraction.py — TDD RED phase.

Tests for tool-call fallback in _extract_worker_action.

Conventions:
- unittest.TestCase (no pytest fixtures/markers)
- SimpleNamespace fakes where needed
- run via: .venv/bin/python -m pytest tests/test_toolcall_extraction.py -q
"""
import unittest

from src.envstate.worker import _extract_worker_action


# Exact raw content from the bug report (MiniMax M2.7 native tool-call format)
_MINIMAX_EXAMPLE = """\
Let me start by probing the current environment state.
<minimax:tool_call>
<invoke name="Bash">
<parameter name="command">pwd && ls -la /app/</parameter>
</invoke>
</minimax:tool_call>"""

_MULTILINE_TOOLCALL = """\
Thought: I need to write a heredoc.
<minimax:tool_call>
<invoke name="Bash">
<parameter name="command">cat > /tmp/setup.sh << 'EOF'
pip install numpy
pip install scipy
EOF
bash /tmp/setup.sh</parameter>
</invoke>
</minimax:tool_call>"""

_PLAIN_REACT = "Thought: t\nAction: ls -la"

_BOTH_ACTION_AND_TOOLCALL = """\
Thought: do something
Action: ls /repo
<minimax:tool_call>
<invoke name="Bash">
<parameter name="command">pwd</parameter>
</invoke>
</minimax:tool_call>"""

_OTHER_INVOKE_NAME = """\
<invoke name="shell">
<parameter name="command">echo hello</parameter>
</invoke>"""

_NO_ACTION_NO_TOOLCALL = "Thought: I'm confused.\nSome random text."


class ToolCallExtractionTests(unittest.TestCase):
    """Tests for the tool-call fallback in _extract_worker_action."""

    def test_minimax_toolcall_extracts_command(self):
        """Exact example from bug report -> 'pwd && ls -la /app/'."""
        result = _extract_worker_action(_MINIMAX_EXAMPLE)
        self.assertEqual(result, "pwd && ls -la /app/")

    def test_multiline_toolcall_preserves_full_command(self):
        """Multi-line heredoc command is NOT truncated to first line."""
        result = _extract_worker_action(_MULTILINE_TOOLCALL)
        expected = (
            "cat > /tmp/setup.sh << 'EOF'\n"
            "pip install numpy\n"
            "pip install scipy\n"
            "EOF\n"
            "bash /tmp/setup.sh"
        )
        self.assertEqual(result, expected)

    def test_plain_action_line_unchanged(self):
        """Plain 'Action: ls -la' still works (Arm B path byte-identical)."""
        result = _extract_worker_action(_PLAIN_REACT)
        self.assertEqual(result, "ls -la")

    def test_action_line_wins_over_toolcall(self):
        """When BOTH Action: line and tool-call are present, Action: takes precedence."""
        result = _extract_worker_action(_BOTH_ACTION_AND_TOOLCALL)
        self.assertEqual(result, "ls /repo")

    def test_other_invoke_name_still_extracts_command(self):
        """invoke name='shell' (not 'Bash') -> command is still extracted."""
        result = _extract_worker_action(_OTHER_INVOKE_NAME)
        self.assertEqual(result, "echo hello")

    def test_no_action_no_toolcall_returns_empty(self):
        """No Action: and no tool-call -> returns ''."""
        result = _extract_worker_action(_NO_ACTION_NO_TOOLCALL)
        self.assertEqual(result, "")

    def test_none_input_returns_empty(self):
        """None content -> returns '' (unchanged guard)."""
        result = _extract_worker_action(None)
        self.assertEqual(result, "")

    def test_plain_action_first_line_truncation_preserved(self):
        """Arm B: Action: with trailing text is truncated to first line."""
        content = "Thought: x\nAction: pip install requests\nextra junk"
        result = _extract_worker_action(content)
        self.assertEqual(result, "pip install requests")

    def test_toolcall_with_markdown_fences_stripped(self):
        """Code fences inside <parameter name="command"> are removed."""
        content = (
            '<parameter name="command">```bash\nls /repo\n```</parameter>'
        )
        result = _extract_worker_action(content)
        self.assertEqual(result, "ls /repo")


if __name__ == "__main__":
    unittest.main()
