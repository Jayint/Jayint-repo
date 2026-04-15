import re
from dataclasses import dataclass, field
from typing import Any, Optional
from xml.sax.saxutils import escape, unescape

SAFETY_COMPRESSION_NOTE = "... (repetitive output omitted by safety compression) ..."
SAFETY_TRUNCATION_NOTE = (
    "\n... (safety-compressed output truncated to stay within prompt budget) ..."
)

SAFETY_NOISE_PATTERNS = (
    r"^\s*Progress \(\d+\):",
    r"^\s*\d+K (?:\.+\s+)+\d+%.*$",
    r"^\s*Downloading from ",
    r"^\s*Downloaded from ",
    r"^\s*Get:\d+\s",
    r"^\s*Hit:\d+\s",
    r"^\s*Ign:\d+\s",
    r"^\s*Fetched\s",
    r"^\s*Resolving deltas:",
    r"^\s*Receiving objects:",
    r"^\s*remote:",
)

SAFETY_STATUS_PATTERNS = (
    r"\bBUILD SUCCESS\b",
    r"\bBUILD FAILURE\b",
    r"^Total time:",
    r"^Finished at:",
    r"^Reactor Summary",
    r"^Results ?:",
    r"^FAILURES?:",
    r"short test summary info",
    r"collected \d+ items",
    r"\b\d+ passed\b",
    r"\b\d+ failed\b",
    r"\bxfailed\b",
    r"Successfully installed",
    r"Requirement already satisfied",
    r"already satisfied",
    r"^The following NEW packages will be installed",
    r"^The following packages will be upgraded",
    r"^Need to get ",
    r"^After this operation",
    r"saved \[",
)

SAFETY_ERROR_PATTERNS = (
    r"^\s*\[ERROR\]",
    r"\btraceback\b",
    r"\bexception\b",
    r"\bcommand not found\b",
    r"\bno such file or directory\b",
    r"\bfailed\b",
    r"\berror\b",
)

SAFETY_BLOCK_PATTERNS = (
    (r"^Reactor Summary", 12),
    (r"short test summary info", 20),
    (r"^FAILURES?:", 20),
    (r"^Results ?:?", 12),
    (r"^\s*\[ERROR\]", 8),
)


UNIFIED_COMPRESSION_SYSTEM_PROMPT = """You are a trajectory compression module for an environment-setup coding agent.

Your job is to compress a single old step in the agent trajectory by rewriting ONLY the text inside the <result>...</result> block of the target step.

The agent is trying to set up a runnable/testable software environment inside Docker.
The compressed result will be shown to the agent in later turns, so you must preserve any information that could affect later decisions.

You must follow these rules strictly:
1. You may only rewrite the content inside the <result> block of the TARGET step.
2. You must NOT change:
- <think> ... </think>
- <call ...> ... </call>
- XML tags
- step ids
- command text
3. Keep the overall structure unchanged.
4. Do not invent facts that do not appear in the original result.
5. If compression is unsafe, return the original target step unchanged.
6. Prefer replacing low-value repetitive text with short placeholders rather than deleting content silently.
7. Preserve exact package names, test names, file paths, versions, and error messages whenever they may matter later.

The trajectory may contain three common kinds of waste:
- Useless information: very long repetitive output that does not change later decisions.
- Redundant information: information repeated many times in the same result.
- Expired information: local details that are no longer useful except for their takeaway.

Important preservation rules:

A. If the result is a TEST LOG:
You should preserve:
- the test session header if present
- platform/runtime/version info if present
- collected test counts
- short test summary info
- failing/error/xfail test cases
- traceback or assertion message that explains failure
- the final summary line such as "73 passed, 1 failed in 4.48s"
You may compress:
- long runs of individual PASSED lines
Use placeholders like:
- ... (individual test lines omitted; mostly PASSED)

B. If the result is an INSTALL LOG:
You must preserve:
- package manager identity if clear from the output
- successfully installed package names and versions
- already satisfied / already installed package names and versions
- key warnings
- the first real error and its nearby context
You may compress:
- download progress bars
- repeated fetch/build lines
- verbose wheel/build noise
Do NOT remove successful or already-present package lists, because the agent may otherwise reinstall them later.

C. If the result is a BUILD / GENERAL COMMAND LOG:
You should preserve:
- whether the command succeeded or failed
- key discovered files/paths if relevant
- key build artifacts if relevant
- first real error and the most informative nearby lines
You may compress:
- repetitive build progress
- repeated informational lines
- large irrelevant blocks that can be replaced by a short takeaway

Compression style:
- Be conservative.
- Preserve important lines verbatim when needed.
- Replace long repetitive spans with one short bracketed or parenthetical note.
- Keep the compressed result readable by the next agent step.
- Do not turn everything into a vague summary.
- The result should still look like a command output, just shorter.

Output format:
Return ONLY the full rewritten TARGET step, with the same <step>, <think>, <call>, and <result> tags.
Do not return explanations outside the XML.
"""


UNIFIED_COMPRESSION_USER_PROMPT = """You are given a sliding window of agent steps in XML.
Compress ONLY the TARGET step by rewriting ONLY its <result> block.

TARGET_STEP_ID: {target_step_id}

Window context:
{serialized_window}

Return only the rewritten TARGET step.
"""


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _matches_any_pattern(line: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)


def _is_safety_noise_line(line: str) -> bool:
    return _matches_any_pattern(line, SAFETY_NOISE_PATTERNS)


def _is_safety_status_line(line: str) -> bool:
    return _matches_any_pattern(line, SAFETY_STATUS_PATTERNS)


def _is_safety_error_line(line: str) -> bool:
    return _matches_any_pattern(line, SAFETY_ERROR_PATTERNS)


def _take_meaningful_head_indices(lines: list[str], limit: int) -> list[int]:
    indices = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _is_safety_noise_line(line) and not _is_safety_status_line(line):
            continue
        indices.append(index)
        if len(indices) >= limit:
            break
    if not indices:
        return list(range(min(limit, len(lines))))
    return indices


def _take_meaningful_tail_indices(lines: list[str], limit: int) -> list[int]:
    indices = []
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip():
            continue
        if _is_safety_noise_line(line) and not _is_safety_status_line(line):
            continue
        indices.append(index)
        if len(indices) >= limit:
            break
    if not indices:
        return list(range(max(0, len(lines) - limit), len(lines)))
    return list(reversed(indices))


def _collect_following_block(lines: list[str], start_index: int, max_lines: int) -> set[int]:
    end_index = min(len(lines), start_index + max_lines)
    block_indices = set()
    for index in range(start_index, end_index):
        line = lines[index]
        block_indices.add(index)
        if index > start_index and not line.strip():
            break
    return block_indices


def _collect_safety_indices(
    lines: list[str],
    head_limit: int,
    tail_limit: int,
    max_error_blocks: int = 12,
) -> list[int]:
    selected = set(_take_meaningful_head_indices(lines, head_limit))
    selected.update(_take_meaningful_tail_indices(lines, tail_limit))

    error_blocks = 0
    for index, line in enumerate(lines):
        if _is_safety_status_line(line):
            selected.add(index)
        for pattern, block_len in SAFETY_BLOCK_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                selected.update(_collect_following_block(lines, index, block_len))
                break
        if error_blocks < max_error_blocks and _is_safety_error_line(line):
            start = max(0, index - 2)
            end = min(len(lines), index + 5)
            selected.update(range(start, end))
            error_blocks += 1

    if not selected:
        selected.update(range(min(len(lines), head_limit + tail_limit)))

    return sorted(selected)


def _render_safety_segments(
    lines: list[str],
    selected_indices: list[int],
    original_chars: int,
    threshold_chars: int,
) -> str:
    if not selected_indices:
        return "\n".join(lines)

    rendered = [
        "[Safety Compression Applied]",
        (
            f"Original observation length: {original_chars} chars "
            f"(threshold: {threshold_chars})."
        ),
        "",
    ]

    segment_start = selected_indices[0]
    segment_end = selected_indices[0]
    for index in selected_indices[1:]:
        if index == segment_end + 1:
            segment_end = index
            continue

        rendered.extend(lines[segment_start : segment_end + 1])
        omitted_count = index - segment_end - 1
        if omitted_count > 0:
            rendered.append(f"... ({omitted_count} lines omitted by safety compression) ...")
        segment_start = index
        segment_end = index

    rendered.extend(lines[segment_start : segment_end + 1])
    rendered.append("")
    rendered.append(SAFETY_COMPRESSION_NOTE)
    return "\n".join(rendered).strip()


def _truncate_safety_output(text: str, target_chars: int) -> str:
    if len(text) <= target_chars:
        return text
    if target_chars <= len(SAFETY_TRUNCATION_NOTE) + 20:
        return text[:target_chars]
    remaining = target_chars - len(SAFETY_TRUNCATION_NOTE)
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    return (
        text[:head_chars].rstrip()
        + SAFETY_TRUNCATION_NOTE
        + text[-tail_chars:].lstrip()
    )


def safety_compress_observation(
    observation_raw: str,
    threshold_chars: int = 200_000,
    target_chars: int = 20_000,
) -> tuple[str, bool]:
    text = observation_raw or ""
    if len(text) <= threshold_chars:
        return text, False

    lines = text.splitlines()
    if not lines:
        return _truncate_safety_output(text, target_chars), True

    selected = _collect_safety_indices(lines, head_limit=24, tail_limit=80)
    compressed = _render_safety_segments(
        lines,
        selected_indices=selected,
        original_chars=len(text),
        threshold_chars=threshold_chars,
    )

    if len(compressed) > target_chars:
        selected = _collect_safety_indices(lines, head_limit=12, tail_limit=40, max_error_blocks=8)
        compressed = _render_safety_segments(
            lines,
            selected_indices=selected,
            original_chars=len(text),
            threshold_chars=threshold_chars,
        )

    compressed = _truncate_safety_output(compressed, target_chars)
    return compressed, compressed != text


def build_observation_metadata(observation_raw: str) -> dict[str, Any]:
    text = observation_raw or ""
    lower = text.lower()
    return {
        "raw_chars": len(text),
        "raw_tokens_est": estimate_tokens(text),
        "has_test_markers": any(
            marker in lower
            for marker in (
                "test session starts",
                "collected ",
                "short test summary info",
                " passed",
                " failed",
                " xfailed",
                "traceback",
            )
        ),
        "has_install_markers": any(
            marker in lower
            for marker in (
                "successfully installed",
                "already satisfied",
                "already installed",
                "collecting ",
                "installing ",
                "fetching ",
                "apt-get install",
                "bundle install",
                "npm install",
            )
        ),
        "has_error_markers": any(
            marker in lower
            for marker in (
                "error",
                "failed",
                "traceback",
                "exception",
                "no such file",
                "command not found",
            )
        ),
        "safety_compressed": False,
        "prompt_chars": len(text),
    }


@dataclass
class CompressionRecord:
    eligible: bool = False
    applied: bool = False
    model: Optional[str] = None
    reason: Optional[str] = None

    original_chars: int = 0
    reduced_chars: int = 0

    original_tokens_est: int = 0
    reduced_tokens_est: int = 0
    saved_tokens_est: int = 0

    reflect_input_tokens: int = 0
    reflect_output_tokens: int = 0
    reflect_total_tokens: int = 0


@dataclass
class StepTokenUsage:
    planner_input_tokens: int = 0
    planner_output_tokens: int = 0

    reflect_input_tokens: int = 0
    reflect_output_tokens: int = 0


@dataclass
class AgentStep:
    step_id: int

    thought: str
    action: str

    success: bool
    exit_code: Optional[int]

    mutates_environment: bool
    env_revision_before: int
    env_revision_after: int

    observation_raw: str
    observation_prompt: str

    metadata: dict[str, Any] = field(default_factory=dict)
    compression: CompressionRecord = field(default_factory=CompressionRecord)
    token_usage: StepTokenUsage = field(default_factory=StepTokenUsage)


@dataclass
class TokenBucket:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class RunTokenLedger:
    image_selector: TokenBucket = field(default_factory=TokenBucket)
    planner: TokenBucket = field(default_factory=TokenBucket)
    reflection: TokenBucket = field(default_factory=TokenBucket)
    memory: TokenBucket = field(default_factory=TokenBucket)
    total: TokenBucket = field(default_factory=TokenBucket)

    def add(self, bucket_name: str, input_tokens: int, output_tokens: int):
        bucket = getattr(self, bucket_name)
        bucket.input_tokens += input_tokens
        bucket.output_tokens += output_tokens
        bucket.total_tokens += input_tokens + output_tokens

        self.total.input_tokens += input_tokens
        self.total.output_tokens += output_tokens
        self.total.total_tokens += input_tokens + output_tokens


def serialize_step_for_reflection(step: AgentStep, target: bool = False) -> str:
    target_attr = ' target="true"' if target else ""
    thought = escape(step.thought or "")
    action = escape(step.action or "")
    result_text = step.observation_prompt if step.observation_prompt else step.observation_raw
    result = escape(result_text or "")
    return (
        f'<step id="{step.step_id}"{target_attr}>\n'
        f"<think>{thought}</think>\n"
        f'<call tool="bash">{action}</call>\n'
        f"<result>\n{result}\n</result>\n"
        f"</step>"
    )


def serialize_window_for_reflection(steps: list[AgentStep], target_step_id: int) -> str:
    parts = ["<trajectory>"]
    for step in steps:
        parts.append(
            serialize_step_for_reflection(step, target=(step.step_id == target_step_id))
        )
    parts.append("</trajectory>")
    return "\n".join(parts)


def extract_result_block_from_rewritten_step(content: str) -> Optional[str]:
    match = re.search(r"<result>\s*(.*?)\s*</result>", content, re.DOTALL)
    if not match:
        return None
    return unescape(match.group(1).strip())


class ObservationCompressor:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def compress(
        self,
        target_step: AgentStep,
        context_steps: list[AgentStep],
    ) -> tuple[str, CompressionRecord]:
        record = CompressionRecord(
            eligible=True,
            model=self.model,
            original_chars=len(target_step.observation_raw or ""),
            original_tokens_est=estimate_tokens(target_step.observation_raw or ""),
        )

        serialized_window = serialize_window_for_reflection(
            context_steps,
            target_step_id=target_step.step_id,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": UNIFIED_COMPRESSION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": UNIFIED_COMPRESSION_USER_PROMPT.format(
                        target_step_id=target_step.step_id,
                        serialized_window=serialized_window,
                    ),
                },
            ],
            temperature=0,
        )

        content = response.choices[0].message.content or ""
        reduced_result = extract_result_block_from_rewritten_step(content)
        if reduced_result is None:
            record.reason = "failed_to_parse_rewritten_result"
            return target_step.observation_raw, record

        record.reflect_input_tokens = response.usage.prompt_tokens
        record.reflect_output_tokens = response.usage.completion_tokens
        record.reflect_total_tokens = response.usage.total_tokens

        record.reduced_chars = len(reduced_result)
        record.reduced_tokens_est = estimate_tokens(reduced_result)
        record.saved_tokens_est = max(
            0, record.original_tokens_est - record.reduced_tokens_est
        )
        return reduced_result, record


def should_apply_compression(
    step: AgentStep,
    record: CompressionRecord,
    compress_threshold_chars: int,
    benefit_threshold_tokens: int,
) -> tuple[bool, str]:
    raw_len = len(step.observation_raw or "")
    if raw_len < compress_threshold_chars:
        return False, "too_short"
    if record.saved_tokens_est < benefit_threshold_tokens:
        return False, "benefit_too_small"
    if record.reduced_chars <= 0:
        return False, "empty_result"
    return True, "applied"
