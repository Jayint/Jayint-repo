import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union
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


UNIFIED_COMPRESSION_SYSTEM_PROMPT = """You compress exactly one command observation for a Docker environment setup agent.

Only compress ORIGINAL_TARGET_RESULT. READ_ONLY_CONTEXT is only a relevance signal.
Never copy facts from READ_ONLY_CONTEXT into the compressed result.
The compressed result must look like output produced by TARGET_ACTION.
If compression is unsafe, return ORIGINAL_TARGET_RESULT unchanged.

Preserve exact package names/versions, installed/already-satisfied package lists,
test counts/summaries, failing tests, paths, key warnings, and real errors.

Important preservation rules:

A. If ORIGINAL_TARGET_RESULT is a TEST LOG, preserve:
- test session header if present
- platform/runtime/version info if present
- collected test counts
- short test summary info
- failing/error/xfail test cases
- traceback or assertion message that explains failure
- final summary line such as "73 passed, 1 failed in 4.48s"
You may compress long runs of individual PASSED lines with placeholders such as:
- ... (individual test lines omitted; mostly PASSED)

B. If ORIGINAL_TARGET_RESULT is an INSTALL LOG, preserve:
- package manager identity if clear from the output
- Collecting/Downloading/Using cached/package-resolution lines identifying packages or versions
- Installing collected packages lines, preferably verbatim
- Successfully built lines when package names are listed
- Successfully installed lines, preferably verbatim
- successfully installed package names and versions
- already satisfied/already installed package names and versions
- key warnings
- first real error and nearby context
You may compress download progress bars, repeated fetch/build lines, and verbose wheel/build noise.
Do NOT remove successful or already-present package lists, because the agent may otherwise reinstall them later.
Do NOT replace an install log with a high-level takeaway if doing so hides which packages were installed, skipped, or failed.

C. If ORIGINAL_TARGET_RESULT is a BUILD / GENERAL COMMAND LOG, preserve:
- whether the command succeeded or failed
- key discovered files/paths if relevant
- key build artifacts if relevant
- first real error and the most informative nearby lines
You may compress repetitive build progress, repeated informational lines, and large irrelevant blocks.

Compress repetitive progress, passed-test runs, and verbose build noise with short placeholders.
Return only the requested <compression> XML block. Do not include analysis, markdown,
<step>, <think>, or <call>.
"""


UNIFIED_COMPRESSION_USER_PROMPT = """READ_ONLY_CONTEXT:
The following steps are not compression targets. Use them only to decide which
details in ORIGINAL_TARGET_RESULT matter later.

{serialized_context}

TARGET STEP TO COMPRESS:
TARGET_STEP_ID: {target_step_id}
TARGET_ACTION:
{target_action}

TARGET_THOUGHT:
{target_thought}

ORIGINAL_TARGET_RESULT:
{target_result}

Return only:
<compression target_step_id="{target_step_id}">
<compressed_result>
compressed ORIGINAL_TARGET_RESULT only
</compressed_result>
</compression>
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
    recipe: TokenBucket = field(default_factory=TokenBucket)
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


def serialize_context_for_compression(
    steps: list[AgentStep],
    target_step_id: int,
) -> str:
    parts = []
    for step in steps:
        if step.step_id == target_step_id:
            continue
        result_text = step.observation_prompt if step.observation_prompt else step.observation_raw
        parts.append(
            "\n".join(
                [
                    f"CONTEXT STEP {step.step_id}",
                    "ACTION:",
                    step.action or "",
                    "RESULT:",
                    result_text or "",
                ]
            )
        )
    return "\n\n".join(parts) if parts else "(no read-only context)"


def _extract_tag_content(content: str, tag_name: str) -> Optional[str]:
    match = re.search(
        rf"<{tag_name}\b[^>]*>\s*(.*?)\s*</{tag_name}>",
        content,
        re.DOTALL,
    )
    if not match:
        return None
    return unescape(match.group(1).strip())


def _extract_int_attr(attrs: str, attr_name: str) -> Optional[int]:
    match = re.search(rf'\b{attr_name}\s*=\s*(?:"|\')?(\d+)(?:"|\')?', attrs)
    if not match:
        return None
    return int(match.group(1))


def extract_compressed_result_from_response(
    content: str,
    target_step_id: int,
) -> Optional[str]:
    for match in re.finditer(
        r"<compression\b(?P<attrs>[^>]*)>(?P<body>.*?)</compression>",
        content,
        re.DOTALL,
    ):
        response_step_id = _extract_int_attr(match.group("attrs"), "target_step_id")
        if response_step_id != target_step_id:
            continue
        return _extract_tag_content(match.group("body"), "compressed_result")
    return None


def _find_step_body_by_id(content: str, step_id: int) -> Optional[str]:
    for match in re.finditer(
        r"<step\b(?P<attrs>[^>]*)>(?P<body>.*?)</step>",
        content,
        re.DOTALL,
    ):
        attrs = match.group("attrs")
        step_id_attr = _extract_int_attr(attrs, "id")
        if step_id_attr == step_id:
            return match.group("body")
    return None


def extract_result_block_from_rewritten_step(
    content: str,
    target_step: Optional[AgentStep] = None,
) -> Optional[str]:
    if target_step is None:
        return _extract_tag_content(content, "result")

    compressed_result = extract_compressed_result_from_response(
        content,
        target_step.step_id,
    )
    if compressed_result is not None:
        return compressed_result

    step_body = _find_step_body_by_id(content, target_step.step_id)
    if step_body is None:
        return None

    rewritten_thought = _extract_tag_content(step_body, "think")
    rewritten_action = _extract_tag_content(step_body, "call")
    if rewritten_thought is None or rewritten_action is None:
        return None

    if rewritten_thought != (target_step.thought or "").strip():
        return None
    if rewritten_action != (target_step.action or "").strip():
        return None

    return _extract_tag_content(step_body, "result")


class ObservationCompressor:
    def __init__(self, client, model: str, log_dir: Optional[Union[str, Path]] = None):
        self.client = client
        self.model = model
        self.log_dir = Path(log_dir) if log_dir else None
        self.log_counter = 0

    def _log_llm_input(self, messages) -> Optional[int]:
        if not self.log_dir:
            return None

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_index = self.log_counter
        log_file = self.log_dir / f"{log_index}.md"

        with log_file.open("w", encoding="utf-8") as f:
            f.write(f"##### LLM INPUT (compression call #{log_index}) #####\n")
            f.write("================================ Human Message =================================\n\n")
            for msg in messages:
                role = msg.get("role", "unknown")
                msg_content = msg.get("content", "")
                if role == "system":
                    f.write(f"[{role.upper()}]\n{msg_content}\n\n")
                elif role == "user":
                    f.write(f"{msg_content}\n\n")
                else:
                    f.write(f"[{role.upper()}]\n{msg_content}\n\n")

        self.log_counter += 1
        return log_index

    def _log_llm_output(self, log_index, content, usage=None, metadata=None):
        if self.log_dir is None or log_index is None:
            return

        log_file = self.log_dir / f"{log_index}.md"
        with log_file.open("a", encoding="utf-8") as f:
            f.write("================================ AI Message =================================\n\n")
            f.write(f"{content}\n\n")
            f.write("================================ Metadata =================================\n\n")
            f.write(f"- Model: {self.model}\n")
            if usage is not None:
                f.write(f"- Prompt Tokens: {usage.prompt_tokens}\n")
                f.write(f"- Completion Tokens: {usage.completion_tokens}\n")
                f.write(f"- Total Tokens: {usage.total_tokens}\n")
            for key, value in (metadata or {}).items():
                f.write(f"- {key}: {value}\n")

        self.log_counter = max(self.log_counter, log_index + 1)

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

        serialized_context = serialize_context_for_compression(
            context_steps,
            target_step_id=target_step.step_id,
        )
        target_result = target_step.observation_prompt or target_step.observation_raw or ""

        messages = [
            {"role": "system", "content": UNIFIED_COMPRESSION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": UNIFIED_COMPRESSION_USER_PROMPT.format(
                    target_step_id=target_step.step_id,
                    serialized_context=serialized_context,
                    target_action=target_step.action or "",
                    target_thought=target_step.thought or "",
                    target_result=target_result,
                ),
            },
        ]

        log_index = self._log_llm_input(messages)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
        )

        content = response.choices[0].message.content or ""
        self._log_llm_output(
            log_index,
            content=content,
            usage=response.usage,
            metadata={
                "Target Step ID": target_step.step_id,
                "Context Step IDs": [
                    step.step_id for step in context_steps
                ],
                "Original Observation Chars": len(target_step.observation_raw or ""),
            },
        )
        reduced_result = extract_result_block_from_rewritten_step(
            content,
            target_step=target_step,
        )
        if reduced_result is None:
            record.reason = "invalid_rewritten_target_step"
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
