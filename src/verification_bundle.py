import re
from typing import Any, Optional

from src.synthesizer import Synthesizer


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
) -> dict[str, list[str]]:
    summary = run_summary or {}
    detector = synthesizer or Synthesizer()
    observed_actions = _collect_observed_successful_actions(summary)
    observed_test_commands = _collect_effective_observed_test_commands(summary, detector)

    runtime_commands = []
    for candidate in (
        normalize_command_list((summary.get("verification_bundle") or {}).get("runtime_preparation_commands")),
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
        normalize_command_list((summary.get("verification_bundle") or {}).get("test_commands")),
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
) -> list[str]:
    commands = []
    for record in run_summary.get("successful_actions") or []:
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
        ):
            commands.append(command)
    return commands


def _reported_runtime_command_is_supported(reported_command: str, observed_actions: list[str]) -> bool:
    normalized_reported = _normalize_command_for_compare(reported_command)
    if not normalized_reported:
        return False
    return any(
        _normalize_command_for_compare(observed) == normalized_reported
        for observed in observed_actions
    )


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
