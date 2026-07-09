"""Per-run ReAct transcript + observation compression (spec §6). Tier 1 = deterministic
safety truncation (keep the tail, where errors live). Tier 2 (Task 4) = an LLM reflective
pass over old large observations. Pure except the injected compressor. No arm-C imports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Step:
    step_id: int
    thought: str
    action_summary: str
    observation_raw: str
    observation_prompt: str          # possibly truncated (Tier 1) then compressed (Tier 2)


def safety_truncate(text: str, *, max_chars: int, keep_tail: bool = True) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    if not keep_tail:
        return text[:max_chars] + "…[truncated]…", True
    # Keep BOTH ends: the head carries the build header / failing command / start of the error,
    # the tail carries the pytest summary + final error line. Tail-only buried the head under
    # download/compiler noise on recent (not-yet-LLM-compressed) steps.
    head = max_chars // 4
    tail = max_chars - head
    return f"{text[:head]}\n…[omitted]…\n{text[-tail:]}", True


class History:
    def __init__(self, *, safety_max_chars: int = 4000, compress_delay: int = 2,
                 compress_threshold_chars: int = 1500,
                 compressor: "Callable[[Step, list[Step]], str] | None" = None,
                 log=None):
        self.steps: list[Step] = []
        self.safety_max_chars = safety_max_chars
        self.compress_delay = compress_delay
        self.compress_threshold_chars = compress_threshold_chars
        self.compressor = compressor
        self.log = log

    def record(self, step_id: int, thought: str, action_summary: str,
               observation_raw: str) -> Step:
        prompt_obs, _ = safety_truncate(observation_raw or "", max_chars=self.safety_max_chars)
        step = Step(step_id, thought, action_summary, observation_raw or "", prompt_obs)
        self.steps.append(step)
        self._maybe_compress()           # Tier 2 — reflective compression (no-op if no compressor injected)
        return step

    def _maybe_compress(self) -> None:
        if self.compressor is None:
            return
        target_idx = len(self.steps) - 1 - self.compress_delay
        if target_idx < 0:
            return
        target = self.steps[target_idx]
        already = target.observation_prompt != target.observation_raw and "[summary" in target.observation_prompt
        if already or len(target.observation_raw) < self.compress_threshold_chars:
            return
        try:
            reduced = self.compressor(target, self.steps[:target_idx])
        except Exception as exc:                                 # never break the run (spec §10)
            if self.log is not None:
                self.log.d("COMPRESS", f"compression failed, keeping raw: {exc}")
            return
        target.observation_prompt = reduced
        if self.log is not None:
            self.log.d("COMPRESS", f"step {target.step_id}: {len(target.observation_raw)} chars → summary")
            self.log.trace("compress", tier=2, target_step=target.step_id,
                           raw_chars=len(target.observation_raw), summary_chars=len(reduced),
                           summary=reduced)

    def render(self) -> str:
        if not self.steps:
            return "(no prior steps)"
        return "\n".join(
            f"{s.step_id}. [{s.action_summary}] {s.observation_prompt}" for s in self.steps
        )
