import re
import shlex
from typing import Any, Optional

from src.synthesizer import Synthesizer
from src.evaluation_target import is_ratbench_target, normalize_evaluation_target


def normalize_command_list(commands):
    if isinstance(commands, str):
        commands = [commands]

    normalized = []
    for command in commands or []:
        if not command:
            continue
        stripped = str(command).strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def derive_supported_verification_bundle(
    run_summary: Optional[dict[str, Any]],
    synthesizer: Optional[Synthesizer] = None,
    evaluation_target: Optional[str] = None,
) -> dict[str, list[str]]:
    summary = run_summary or {}
    detector = synthesizer or Synthesizer()
    target = normalize_evaluation_target(
        evaluation_target or summary.get("evaluation_target") or summary.get("benchmark_evaluation_target")
    )
    observed_actions = _collect_observed_successful_actions(summary)
    observed_test_commands = _collect_effective_observed_test_commands(
        summary,
        detector,
        include_failed_executed=is_ratbench_target(target),
    )

    reported_bundle = summary.get("verification_bundle") or {}
    reported_runtime_commands = normalize_command_list(
        reported_bundle.get("runtime_preparation_commands")
    )
    reported_test_commands = normalize_command_list(reported_bundle.get("test_commands"))
    if _reported_bundle_commands_are_supported(
        reported_runtime_commands,
        reported_test_commands,
        observed_actions,
        observed_test_commands,
        detector,
    ):
        return {
            "runtime_preparation_commands": reported_runtime_commands,
            "test_commands": reported_test_commands,
        }

    runtime_commands = []
    for candidate in (
        reported_runtime_commands,
        normalize_command_list(summary.get("verified_runtime_preparation_commands")),
    ):
        supported = [
            command for command in candidate if _reported_runtime_command_is_supported(command, observed_actions)
        ]
        if candidate and len(supported) == len(candidate):
            runtime_commands = supported
            break

    test_commands = []
    for candidate in (
        reported_test_commands,
        normalize_command_list(summary.get("verified_test_commands")),
        normalize_command_list(summary.get("verified_test_command")),
    ):
        supported = [
            command for command in candidate if _reported_test_command_is_supported(command, observed_test_commands, detector)
        ]
        if candidate and len(supported) == len(candidate):
            test_commands = supported
            break

    if not test_commands and observed_test_commands:
        test_commands = [observed_test_commands[-1]]

    return {
        "runtime_preparation_commands": runtime_commands,
        "test_commands": test_commands,
    }


def _collect_observed_successful_actions(run_summary: dict[str, Any]) -> list[str]:
    commands = []
    for record in run_summary.get("successful_actions") or []:
        if not isinstance(record, dict):
            continue
        command = str(record.get("command") or "").strip()
        if command:
            commands.append(command)
    return commands


def _collect_effective_observed_test_commands(
    run_summary: dict[str, Any],
    synthesizer: Synthesizer,
    include_failed_executed: bool = False,
) -> list[str]:
    commands = []
    records = list(run_summary.get("successful_actions") or [])
    if include_failed_executed:
        records.extend(run_summary.get("failed_actions") or [])

    for record in records:
        if not isinstance(record, dict):
            continue
        command = str(record.get("command") or "").strip()
        if not command:
            continue
        observation = str(
            record.get("observation_summary")
            or record.get("observation")
            or ""
        )
        analysis = synthesizer.analyze_test_run(command, observation)
        if analysis.get("is_effective_test_run") or (
            synthesizer.observation_has_effective_test_signal(observation)
            and not synthesizer.observation_has_test_failure_signal(observation)
            and not synthesizer.is_truncated_test_output_command(command)
        ) or (
            include_failed_executed
            and synthesizer.is_test_command(command)
            and synthesizer.observation_has_effective_test_signal(observation)
            and not _is_collect_only_test_command(command)
            and not synthesizer.is_truncated_test_output_command(command)
        ):
            commands.append(command)
    return commands


def _is_collect_only_test_command(command: str) -> bool:
    normalized = " " + re.sub(r"\s+", " ", command or "").strip() + " "
    return " --collect-only " in normalized or " --co " in normalized


def _reported_runtime_command_is_supported(reported_command: str, observed_actions: list[str]) -> bool:
    normalized_reported = _normalize_command_for_compare(reported_command)
    if not normalized_reported:
        return False
    return any(
        _normalize_command_for_compare(observed) == normalized_reported
        for observed in observed_actions
    )


def _reported_bundle_commands_are_supported(
    runtime_commands: list[str],
    test_commands: list[str],
    observed_actions: list[str],
    observed_test_commands: list[str],
    synthesizer: Synthesizer,
) -> bool:
    if not test_commands:
        return False

    runtime_supported = all(
        _reported_runtime_command_is_supported(command, observed_actions)
        for command in runtime_commands
    )
    tests_supported = all(
        _reported_test_command_is_supported(command, observed_test_commands, synthesizer)
        for command in test_commands
    )
    if runtime_supported and tests_supported:
        return True

    if runtime_supported or not runtime_commands:
        return False
    if not all(_runtime_command_can_be_inline_env_prefix(command) for command in runtime_commands):
        return False

    inline_env_prefix = " ".join(runtime_commands).strip()
    matched_inline_runtime = False
    for test_command in test_commands:
        if _reported_test_command_is_supported(test_command, observed_test_commands, synthesizer):
            continue
        combined_command = f"{inline_env_prefix} {test_command}".strip()
        if _reported_test_command_is_supported(combined_command, observed_test_commands, synthesizer):
            matched_inline_runtime = True
            continue
        return False

    return matched_inline_runtime


def _runtime_command_can_be_inline_env_prefix(command: str) -> bool:
    try:
        parts = shlex.split(str(command or "").strip(), posix=True)
    except ValueError:
        return False
    if not parts:
        return False
    return all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.+$", part) for part in parts)


def _reported_test_command_is_supported(
    reported_command: str,
    observed_test_commands: list[str],
    synthesizer: Synthesizer,
) -> bool:
    normalized_reported = _normalize_command_for_compare(reported_command)
    if not normalized_reported:
        return False

    for observed in observed_test_commands:
        supported = _supported_reported_test_variants(observed, synthesizer)
        if normalized_reported in supported:
            return True
    return False


def _supported_reported_test_variants(observed_command: str, synthesizer: Synthesizer) -> set[str]:
    variants = set()
    normalized_observed = _normalize_command_for_compare(observed_command)
    if normalized_observed:
        variants.add(normalized_observed)

    segments = [
        _strip_trailing_redirections(raw_segment.strip())
        for raw_segment, _ in synthesizer._split_shell_chain(observed_command)
        if raw_segment.strip()
    ]
    if not segments:
        return variants

    prefix_segments = []
    test_seen = False
    benign_tail = True

    for index, segment in enumerate(segments):
        normalized_segment = synthesizer._normalize_command_segment(segment)
        if not normalized_segment:
            continue

        prefix_segments.append(segment)
        if synthesizer.is_test_command(segment):
            test_seen = True
            remaining = segments[index + 1 :]
            benign_tail = all(_segment_is_benign_post_verification_output(item, synthesizer) for item in remaining)
            break

    if test_seen and benign_tail:
        normalized_prefix = _normalize_command_for_compare(" && ".join(prefix_segments))
        if normalized_prefix:
            variants.add(normalized_prefix)

    return variants


def _segment_is_benign_post_verification_output(segment: str, synthesizer: Synthesizer) -> bool:
    normalized = synthesizer._normalize_command_segment(segment)
    if not normalized:
        return True
    return normalized.startswith(("echo ", "printf ", "cat "))


def _strip_trailing_redirections(command: str) -> str:
    cleaned = (command or "").strip()
    while True:
        updated = re.sub(r"\s+(?:\d?>&\d|[12]?>/?[^\s]+|&>/[^\s]+)$", "", cleaned).strip()
        if updated == cleaned:
            return cleaned
        cleaned = updated


def _normalize_command_for_compare(command: str) -> str:
    return " ".join(str(command or "").split()).strip()
