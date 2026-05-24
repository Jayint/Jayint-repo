import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.constants import DEFAULT_MEMORY_EMBEDDING_MODEL


DEFAULT_MEMORY_PATH = Path(__file__).resolve().parents[1] / "memory" / "long_term_memories.jsonl"
DEFAULT_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DEFAULT_RETRIEVAL_TOP_K = 5
DEFAULT_RETRIEVAL_MIN_SCORE = 0.30
DEFAULT_LINK_TOP_K = 3
DEFAULT_LINK_MIN_SCORE = 0.72


MEMORY_RELATION_JUDGE_SYSTEM_PROMPT = """You are a memory relation judge for an environment-setup agent.

You will receive one NEW memory candidate and one EXISTING memory recalled by embedding similarity.

Classify their relationship as exactly one of:
- "duplicate": They describe the same reusable problem pattern with substantially the same root cause and fix. The new memory should NOT be written as a separate node.
- "link": They are genuinely related but distinct lessons. They should remain separate memories connected by a bidirectional link.
- "unrelated": The embedding match is a false positive or too weak semantically. They should not be connected.

Use "duplicate" only when a future agent would not benefit from seeing both as separate memories.
Use "link" when the lessons share an ecosystem, failure mode, tool, dependency, or service pattern but differ in cause, fix, scope, or useful retrieval context.

Return only a strict JSON object:
{
  "decision": "duplicate | link | unrelated",
  "confidence": 0.0,
  "rationale": "one short sentence"
}
Do NOT output Markdown.
"""


MEMORY_RELATION_JUDGE_USER_PROMPT = """Embedding similarity: {similarity}

NEW MEMORY:
{new_memory}

EXISTING MEMORY:
{existing_memory}

Return only the JSON decision object.
"""


MEMORY_EXTRACTION_SYSTEM_PROMPT = """You are a long-term memory extractor for an environment-setup agent.

Your task is NOT to summarize the whole setup process. Your task is to extract reusable difficult-problem solving lessons from a setup trajectory that has already completed successfully.

A single setup trajectory may contain zero, one, or many independent memories.
Do NOT force the whole trajectory into one memory. Split the trajectory into separate memory objects when it contains distinct failure chains with different root causes or different final fixes.
For example, if one run first solved a git safe.directory failure, then solved a missing service startup failure, then solved a pytest/plugin version conflict, those are three candidate memories, not one combined memory.
Each memory must describe exactly one primary problem pattern, one root cause, and the effective fix for that problem.
Only merge multiple failures into one memory when they are symptoms of the same root cause and were solved by the same final strategy.

Only record a long-term memory when the problem satisfies all of these conditions:
- The problem went through repeated failures, or multiple ineffective/misleading attempts.
- The agent eventually found a clearly effective solution.
- There is auditable verification evidence.
- The lesson is reusable for future similar environment-setup tasks.

Do NOT record these cases:
- The problem failed only once and the fix was directly obvious from that single failure observation.
- A normal missing dependency was installed successfully right after the error.
- The problem could be solved from a simple error message without meaningful exploration.
- The problem was not ultimately solved.
- The root cause is unclear.
- There is no verification evidence.
- The issue was only a transient network failure that succeeded after retrying, without a stable reusable strategy.
- The evidence comes from model speculation, plans, or self-generated Observations rather than real system-executed observations.

Important decision rule:
If an agent could fix the problem directly from the latest failure observation without querying long-term memory, do NOT write a long-term memory.
Only write a long-term memory when the failure process shows clear exploration, misdiagnosis, or ineffective workarounds before the final effective solution was found.

The `verification` field is mainly for human audit. It records why the memory is trustworthy; it is not a runtime proof.
The `anti_patterns` field is important. It must record approaches from the trajectory that were proven ineffective, misleading, or should not be repeated.

Output must be a strict JSON array.
If there is no useful long-term memory to write, output [].
Do NOT output Markdown.
Do NOT output explanatory text.
"""


MEMORY_EXTRACTION_USER_PROMPT = """Extract long-term memory candidates from the following environment-setup run.

Important extraction rule:
- The input is a full setup trajectory, but your output is a list of independent solved problem lessons.
- Do not summarize the entire run as one memory by default.
- If the trajectory contains multiple independent solved failures, output multiple JSON objects.
- Each JSON object should cover one failure chain only: its symptoms, root cause, ineffective attempts, final fix, and verification.
- If a failure was fixed immediately from an obvious error message, omit it rather than merging it into a broader memory.

Repository:
{repo_name}

Was this run successful:
{configuration_success}

Final verification commands:
{verified_test_commands}

Known local service dependencies:
{required_local_services}

agent_run_summary.json:
{agent_run_summary_json}

Final setup log:
{final_setup_log_md}

Return only a strict JSON array. Each element must match this structure:

[
  {{
    "scope": "global | ecosystem | repo",
    "repo": "repo_name or null",
    "problem_signature": "one-sentence reusable problem pattern",
    "symptoms": [
      "key error signal or failure symptom that appeared in real observations"
    ],
    "root_cause": "final confirmed or highly credible root cause",
    "successful_fix": [
      "final effective fix, written as transferable steps when possible"
    ],
    "verification": [
      "human-auditable evidence, such as which command succeeded or which tests passed"
    ],
    "anti_patterns": [
      "approach from this exploration that was ineffective, misleading, or should not be repeated"
    ],
    "embedding_text": "a compact English technical retrieval text containing the problem, symptoms, root cause, fix, and ecosystem context",
    "linked_memories": [],
    "created_at": "{created_at}"
  }}
]
"""


class MemoryManagerError(RuntimeError):
    pass


class EmbeddingDependencyError(MemoryManagerError):
    pass


@dataclass
class MemoryUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class MemoryRetrievalResult:
    memory: dict[str, Any]
    score: float


@dataclass
class MemoryWriteResult:
    written: int = 0
    skipped_duplicates: int = 0
    skipped_invalid: int = 0
    relation_judged_pairs: int = 0
    relation_links_accepted: int = 0
    relation_links_rejected: int = 0
    relation_duplicates_rejected: int = 0
    relation_judge_errors: list[str] = field(default_factory=list)
    usage: MemoryUsage = field(default_factory=MemoryUsage)
    memories: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MemoryGenerationResult:
    candidate_count: int = 0
    written: int = 0
    skipped_duplicates: int = 0
    skipped_invalid: int = 0
    relation_judged_pairs: int = 0
    relation_links_accepted: int = 0
    relation_links_rejected: int = 0
    relation_duplicates_rejected: int = 0
    relation_judge_errors: list[str] = field(default_factory=list)
    usage: MemoryUsage = field(default_factory=MemoryUsage)
    memories: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


class SentenceTransformerEmbedder:
    def __init__(
        self,
        model_name: str = DEFAULT_MEMORY_EMBEDDING_MODEL,
        query_prefix: str = DEFAULT_QUERY_PREFIX,
    ):
        self.model_name = model_name
        self.query_prefix = query_prefix
        self._model = None

    def embed(self, text: str, is_query: bool = False) -> list[float]:
        model = self._load_model()
        prepared_text = f"{self.query_prefix}{text}" if is_query else text
        embedding = model.encode([prepared_text], normalize_embeddings=True)[0]
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        return [float(value) for value in embedding]

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingDependencyError(
                "Long-term memory requires `sentence-transformers` when using the "
                f"local embedding model `{self.model_name}`. Install it with "
                "`pip install sentence-transformers` or disable long-term memory."
            ) from exc

        self._model = SentenceTransformer(self.model_name)
        return self._model


class LongTermMemoryManager:
    def __init__(
        self,
        client=None,
        llm_model: Optional[str] = None,
        memory_path: Optional[str | Path] = None,
        embedding_model: str = DEFAULT_MEMORY_EMBEDDING_MODEL,
        embedder=None,
        clock=None,
        log_dir: Optional[str | Path] = None,
        relation_log_dir: Optional[str | Path] = None,
    ):
        self.client = client
        self.llm_model = llm_model
        self.memory_path = Path(memory_path) if memory_path else DEFAULT_MEMORY_PATH
        self.embedding_model = embedding_model
        self.embedder = embedder or SentenceTransformerEmbedder(model_name=embedding_model)
        self.clock = clock
        self.log_dir = Path(log_dir) if log_dir else None
        self.relation_log_dir = Path(relation_log_dir) if relation_log_dir else None
        self._relation_log_counter = 0

    def _log_memory_generation(self, call_type: str, data: Any):
        if not self.log_dir:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.log_dir / "memory_generation.md"

        if call_type == "input":
            with open(log_file, "w", encoding="utf-8") as file_obj:
                file_obj.write("##### LLM INPUT (long-term memory generation) #####\n")
                file_obj.write("================================ Human Message =================================\n\n")
                for message in data:
                    role = message.get("role", "unknown")
                    content = message.get("content", "")
                    if role == "system":
                        file_obj.write(f"[{role.upper()}]\n{content}\n\n")
                    elif role == "user":
                        file_obj.write(f"{content}\n\n")
                    else:
                        file_obj.write(f"[{role.upper()}]\n{content}\n\n")
            return

        result = data.get("result")
        usage = result.usage if result else MemoryUsage()
        with open(log_file, "a", encoding="utf-8") as file_obj:
            file_obj.write("================================ AI Message =================================\n\n")
            file_obj.write(f"{data.get('content') or ''}\n\n")
            file_obj.write("================================ Parsed Memory Candidates =================================\n\n")
            file_obj.write("```json\n")
            file_obj.write(json.dumps(data.get("candidates"), ensure_ascii=False, indent=2))
            file_obj.write("\n```\n\n")
            file_obj.write("================================ Write Result =================================\n\n")
            file_obj.write("```json\n")
            file_obj.write(json.dumps(self._memory_generation_result_to_dict(result), ensure_ascii=False, indent=2))
            file_obj.write("\n```\n\n")
            file_obj.write("================================ Metadata =================================\n\n")
            file_obj.write(f"- Model: {self.llm_model}\n")
            file_obj.write(f"- Prompt Tokens: {usage.input_tokens}\n")
            file_obj.write(f"- Completion Tokens: {usage.output_tokens}\n")
            file_obj.write(f"- Total Tokens: {usage.total_tokens}\n")

    def _next_memory_relation_log_file(self) -> Optional[Path]:
        if not self.relation_log_dir:
            return None
        self.relation_log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.relation_log_dir / f"{self._relation_log_counter}.md"
        self._relation_log_counter += 1
        return log_file

    def _log_memory_relation_input(
        self,
        messages: list[dict[str, Any]],
        new_memory: dict[str, Any],
        existing_memory: dict[str, Any],
        similarity: float,
    ) -> Optional[Path]:
        log_file = self._next_memory_relation_log_file()
        if not log_file:
            return None

        with open(log_file, "w", encoding="utf-8") as file_obj:
            file_obj.write("##### LLM INPUT (long-term memory relation judge) #####\n\n")
            file_obj.write("================================ Candidate Pair =================================\n\n")
            file_obj.write("```json\n")
            file_obj.write(
                json.dumps(
                    {
                        "similarity": round(similarity, 4),
                        "new_memory": memory_for_relation_judge(new_memory),
                        "existing_memory": memory_for_relation_judge(existing_memory),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            file_obj.write("\n```\n\n")
            file_obj.write("================================ Messages =================================\n\n")
            for message in messages:
                role = str(message.get("role") or "unknown").upper()
                file_obj.write(f"[{role}]\n")
                file_obj.write(f"{message.get('content') or ''}\n\n")
        return log_file

    def _log_memory_relation_output(
        self,
        log_file: Optional[Path],
        content: str,
        parsed: Optional[dict[str, Any]],
        decision: str,
        usage: MemoryUsage,
        error: Optional[str] = None,
    ):
        if not log_file:
            return

        with open(log_file, "a", encoding="utf-8") as file_obj:
            file_obj.write("================================ AI Message =================================\n\n")
            file_obj.write(f"{content or ''}\n\n")
            file_obj.write("================================ Parsed Relation Decision =================================\n\n")
            file_obj.write("```json\n")
            file_obj.write(json.dumps(parsed or {}, ensure_ascii=False, indent=2))
            file_obj.write("\n```\n\n")
            file_obj.write("================================ Metadata =================================\n\n")
            file_obj.write(f"- Model: {self.llm_model}\n")
            file_obj.write(f"- Decision: {decision or 'unknown'}\n")
            file_obj.write(f"- Prompt Tokens: {usage.input_tokens}\n")
            file_obj.write(f"- Completion Tokens: {usage.output_tokens}\n")
            file_obj.write(f"- Total Tokens: {usage.total_tokens}\n")
            if error:
                file_obj.write(f"- Error: {error}\n")

    def _memory_generation_result_to_dict(self, result: Optional[MemoryGenerationResult]) -> dict[str, Any]:
        if result is None:
            return {}

        return {
            "candidate_count": result.candidate_count,
            "written": result.written,
            "skipped_duplicates": result.skipped_duplicates,
            "skipped_invalid": result.skipped_invalid,
            "relation_judged_pairs": result.relation_judged_pairs,
            "relation_links_accepted": result.relation_links_accepted,
            "relation_links_rejected": result.relation_links_rejected,
            "relation_duplicates_rejected": result.relation_duplicates_rejected,
            "relation_judge_errors": result.relation_judge_errors,
            "error": result.error,
            "written_memories": [
                self._redact_memory_for_log(memory)
                for memory in result.memories
            ],
        }

    def _redact_memory_for_log(self, memory: dict[str, Any]) -> dict[str, Any]:
        redacted = dict(memory or {})
        if "embedding" in redacted:
            embedding = redacted.pop("embedding") or []
            redacted["embedding_omitted"] = f"{len(embedding)} dimensions"
        return redacted

    def retrieve(
        self,
        failed_command: str,
        failed_observation: str,
        repo_url: str = "",
        services: Optional[list[str]] = None,
        top_k: int = DEFAULT_RETRIEVAL_TOP_K,
        min_score: float = DEFAULT_RETRIEVAL_MIN_SCORE,
    ) -> tuple[list[MemoryRetrievalResult], str]:
        memories = self.load_memories()
        if not memories:
            query = build_failure_query(failed_command, failed_observation, repo_url, services)
            return [], query

        query = build_failure_query(failed_command, failed_observation, repo_url, services)
        query_embedding = self.embedder.embed(query, is_query=True)

        scored: list[MemoryRetrievalResult] = []
        for memory in memories:
            memory_embedding = memory.get("embedding")
            if not memory_embedding:
                memory_embedding = self.embedder.embed(memory_to_embedding_text(memory), is_query=False)

            score = cosine_similarity(query_embedding, memory_embedding)
            if score >= min_score:
                scored.append(MemoryRetrievalResult(memory=memory, score=score))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k], query

    def format_retrieval_results(self, results: list[MemoryRetrievalResult]) -> str:
        if not results:
            return (
                "Retrieved Long-Term Memories:\n"
                "No relevant long-term memory was found for the last failure. "
                "Continue diagnosing from the current command output."
            )

        rendered = [
            "Retrieved Long-Term Memories:",
            "Use these as suggestions only. You must verify any fix with real commands/tests.",
            "",
        ]
        for index, result in enumerate(results, start=1):
            memory = result.memory
            rendered.append(
                f"{index}. score={result.score:.3f} "
                f"scope={memory.get('scope', '')} repo={memory.get('repo', '')}"
            )
            rendered.append(f"Problem: {memory.get('problem_signature', '')}")
            rendered.append(f"Root cause: {memory.get('root_cause', '')}")
            rendered.append("Symptoms: " + "; ".join(_as_string_list(memory.get("symptoms"))[:3]))
            rendered.append("Successful fix: " + "; ".join(_as_string_list(memory.get("successful_fix"))[:4]))
            anti_patterns = _as_string_list(memory.get("anti_patterns"))
            if anti_patterns:
                rendered.append("Avoid: " + "; ".join(anti_patterns[:3]))
            rendered.append("")

        return "\n".join(rendered).strip()

    def load_memories(self) -> list[dict[str, Any]]:
        if not self.memory_path.exists():
            return []

        memories = []
        with open(self.memory_path, "r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                try:
                    memory = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(memory, dict):
                    memories.append(memory)
        return memories

    def write_memories(
        self,
        candidate_memories: list[dict[str, Any]],
        repo_url: str = "",
    ) -> MemoryWriteResult:
        existing = self.load_memories()
        existing_keys = {_dedupe_key(memory) for memory in existing}
        result = MemoryWriteResult()
        to_write = []
        needs_rewrite = False

        for candidate in candidate_memories:
            memory = normalize_memory(candidate, repo_url=repo_url, created_at=self._now_iso())
            if not is_valid_memory(memory):
                result.skipped_invalid += 1
                continue

            key = _dedupe_key(memory)
            if key in existing_keys:
                result.skipped_duplicates += 1
                continue

            embedding = self.embedder.embed(memory_to_embedding_text(memory), is_query=False)
            memory["embedding"] = embedding
            memory["embedding_model"] = self.embedding_model
            link_matches = self._find_link_matches(memory, existing)
            link_matches, is_duplicate = self._evaluate_relation_matches(
                memory,
                link_matches,
                result,
            )
            if is_duplicate:
                result.skipped_duplicates += 1
                continue

            memory["linked_memories"] = [
                self._make_link(target, score)
                for target, score in link_matches
            ]
            for target, score in link_matches:
                needs_rewrite = self._upsert_link(target, memory, score) or needs_rewrite
            to_write.append(memory)
            existing.append(memory)
            existing_keys.add(key)

        if to_write:
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            if needs_rewrite:
                self._write_all_memories(existing)
            else:
                with open(self.memory_path, "a", encoding="utf-8") as file_obj:
                    for memory in to_write:
                        file_obj.write(json.dumps(memory, ensure_ascii=False))
                        file_obj.write("\n")

        result.written = len(to_write)
        result.memories = to_write
        return result

    def generate_memories_from_run(
        self,
        setup_log_text: str,
        run_summary: dict[str, Any],
        repo_url: str,
        max_setup_log_chars: int = 160_000,
    ) -> MemoryGenerationResult:
        if not self.client or not self.llm_model:
            raise MemoryManagerError("Memory generation requires an LLM client and model.")

        summary_text = json.dumps(_compact_run_summary(run_summary), indent=2, ensure_ascii=False)
        setup_log_text = truncate_middle(setup_log_text or "", max_setup_log_chars)
        created_at = self._now_iso()
        messages = [
            {"role": "system", "content": MEMORY_EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": MEMORY_EXTRACTION_USER_PROMPT.format(
                    repo_name=repo_url,
                    configuration_success=run_summary.get("configuration_success"),
                    verified_test_commands=json.dumps(
                        run_summary.get("verified_test_commands") or [],
                        ensure_ascii=False,
                    ),
                    required_local_services=json.dumps(
                        run_summary.get("required_local_services") or [],
                        ensure_ascii=False,
                    ),
                    agent_run_summary_json=summary_text,
                    final_setup_log_md=setup_log_text,
                    created_at=created_at,
                ),
            },
        ]
        self._log_memory_generation("input", messages)

        response = self.client.chat.completions.create(
            model=self.llm_model,
            messages=messages,
            temperature=0,
        )

        usage = MemoryUsage(
            input_tokens=getattr(response.usage, "prompt_tokens", 0),
            output_tokens=getattr(response.usage, "completion_tokens", 0),
            total_tokens=getattr(response.usage, "total_tokens", 0),
        )
        content = response.choices[0].message.content or ""
        try:
            candidates = extract_json_array(content)
        except MemoryManagerError as exc:
            result = MemoryGenerationResult(usage=usage, error=str(exc))
            self._log_memory_generation(
                "output",
                {
                    "content": content,
                    "candidates": None,
                    "result": result,
                },
            )
            return result

        for memory in candidates:
            if isinstance(memory, dict):
                memory.setdefault("created_at", created_at)

        try:
            write_result = self.write_memories(candidates, repo_url=repo_url)
        except Exception as exc:
            result = MemoryGenerationResult(
                candidate_count=len(candidates),
                skipped_invalid=len(candidates),
                usage=usage,
                error=f"Failed to write generated memories: {exc}",
            )
            self._log_memory_generation(
                "output",
                {
                    "content": content,
                    "candidates": candidates,
                    "result": result,
                },
            )
            return result

        combined_usage = MemoryUsage(
            input_tokens=usage.input_tokens + write_result.usage.input_tokens,
            output_tokens=usage.output_tokens + write_result.usage.output_tokens,
            total_tokens=usage.total_tokens + write_result.usage.total_tokens,
        )

        result = MemoryGenerationResult(
            candidate_count=len(candidates),
            written=write_result.written,
            skipped_duplicates=write_result.skipped_duplicates,
            skipped_invalid=write_result.skipped_invalid,
            relation_judged_pairs=write_result.relation_judged_pairs,
            relation_links_accepted=write_result.relation_links_accepted,
            relation_links_rejected=write_result.relation_links_rejected,
            relation_duplicates_rejected=write_result.relation_duplicates_rejected,
            relation_judge_errors=write_result.relation_judge_errors,
            usage=combined_usage,
            memories=write_result.memories,
        )
        self._log_memory_generation(
            "output",
            {
                "content": content,
                "candidates": candidates,
                "result": result,
            },
        )
        return result

    def _build_links(
        self,
        new_memory: dict[str, Any],
        existing_memories: list[dict[str, Any]],
        top_k: int = DEFAULT_LINK_TOP_K,
        min_score: float = DEFAULT_LINK_MIN_SCORE,
    ) -> list[dict[str, Any]]:
        return [
            self._make_link(memory, score)
            for memory, score in self._find_link_matches(
                new_memory,
                existing_memories,
                top_k=top_k,
                min_score=min_score,
            )
        ]

    def _find_link_matches(
        self,
        new_memory: dict[str, Any],
        existing_memories: list[dict[str, Any]],
        top_k: int = DEFAULT_LINK_TOP_K,
        min_score: float = DEFAULT_LINK_MIN_SCORE,
    ) -> list[tuple[dict[str, Any], float]]:
        new_embedding = new_memory.get("embedding") or []
        if not new_embedding or not existing_memories:
            return []

        matches = []
        for existing in existing_memories:
            existing_embedding = existing.get("embedding")
            if not existing_embedding:
                continue
            score = cosine_similarity(new_embedding, existing_embedding)
            if score < min_score:
                continue
            matches.append((existing, score))

        matches.sort(key=lambda item: item[1], reverse=True)
        return matches[:top_k]

    def _evaluate_relation_matches(
        self,
        new_memory: dict[str, Any],
        link_matches: list[tuple[dict[str, Any], float]],
        result: MemoryWriteResult,
    ) -> tuple[list[tuple[dict[str, Any], float]], bool]:
        if not link_matches:
            return [], False

        # Offline/manual memory writes should still work. In that case the
        # embedding threshold remains the relation decision fallback.
        if not self.client or not self.llm_model:
            return link_matches, False

        accepted_links: list[tuple[dict[str, Any], float]] = []
        for existing_memory, score in link_matches:
            try:
                decision, usage = self._judge_memory_relation(
                    new_memory,
                    existing_memory,
                    score,
                )
                result.relation_judged_pairs += 1
                add_usage(result.usage, usage)
            except Exception as exc:
                result.relation_judge_errors.append(str(exc))
                accepted_links.append((existing_memory, score))
                result.relation_links_accepted += 1
                continue

            if decision == "duplicate":
                result.relation_duplicates_rejected += 1
                return [], True
            if decision == "link":
                accepted_links.append((existing_memory, score))
                result.relation_links_accepted += 1
            else:
                result.relation_links_rejected += 1

        return accepted_links, False

    def _judge_memory_relation(
        self,
        new_memory: dict[str, Any],
        existing_memory: dict[str, Any],
        similarity: float,
    ) -> tuple[str, MemoryUsage]:
        messages = [
            {"role": "system", "content": MEMORY_RELATION_JUDGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": MEMORY_RELATION_JUDGE_USER_PROMPT.format(
                    similarity=f"{similarity:.4f}",
                    new_memory=json.dumps(
                        memory_for_relation_judge(new_memory),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    existing_memory=json.dumps(
                        memory_for_relation_judge(existing_memory),
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            },
        ]
        log_file = self._log_memory_relation_input(
            messages,
            new_memory,
            existing_memory,
            similarity,
        )
        usage = MemoryUsage()
        content = ""
        parsed: Optional[dict[str, Any]] = None
        decision = ""
        try:
            response = self.client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                temperature=0,
            )
            usage = MemoryUsage(
                input_tokens=getattr(response.usage, "prompt_tokens", 0),
                output_tokens=getattr(response.usage, "completion_tokens", 0),
                total_tokens=getattr(response.usage, "total_tokens", 0),
            )
            content = response.choices[0].message.content or ""
            parsed = extract_json_object(content)
            decision = str(parsed.get("decision") or "").strip().lower()
            if decision not in {"duplicate", "link", "unrelated"}:
                raise MemoryManagerError(f"Invalid memory relation decision: {decision!r}")
        except Exception as exc:
            self._log_memory_relation_output(
                log_file,
                content,
                parsed,
                decision,
                usage,
                error=str(exc),
            )
            raise

        self._log_memory_relation_output(
            log_file,
            content,
            parsed,
            decision,
            usage,
        )
        return decision, usage

    def _make_link(self, target: dict[str, Any], score: float) -> dict[str, Any]:
        return {
            "problem_signature": target.get("problem_signature", ""),
            "scope": target.get("scope", ""),
            "repo": target.get("repo", ""),
            "similarity": round(score, 4),
        }

    def _upsert_link(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        score: float,
        top_k: int = DEFAULT_LINK_TOP_K,
    ) -> bool:
        link = self._make_link(target, score)
        link_key = _link_key(link)
        links = _as_link_list(source.get("linked_memories"))
        changed = False

        for index, existing_link in enumerate(links):
            if _link_key(existing_link) == link_key:
                if existing_link.get("similarity") != link["similarity"]:
                    links[index] = link
                    changed = True
                break
        else:
            links.append(link)
            changed = True

        links.sort(key=lambda item: float(item.get("similarity") or 0), reverse=True)
        pruned_links = links[:top_k]
        if len(pruned_links) != len(links):
            changed = True

        if changed:
            source["linked_memories"] = pruned_links
        return changed

    def _write_all_memories(self, memories: list[dict[str, Any]]):
        temp_path = self.memory_path.with_suffix(self.memory_path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as file_obj:
            for memory in memories:
                file_obj.write(json.dumps(memory, ensure_ascii=False))
                file_obj.write("\n")
        temp_path.replace(self.memory_path)

    def _now_iso(self) -> str:
        if self.clock:
            return self.clock().isoformat()
        return datetime.now(timezone.utc).astimezone().isoformat()


def build_failure_query(
    failed_command: str,
    failed_observation: str,
    repo_url: str = "",
    services: Optional[list[str]] = None,
    max_chars: int = 4000,
) -> str:
    selected_observation = select_failure_lines(failed_observation or "")
    sections = [
        f"Repository: {repo_url or 'unknown'}",
        f"Failed command: {failed_command or 'unknown'}",
        "Known local services: " + (", ".join(sorted(services or [])) or "unknown"),
        "Failure observation:",
        selected_observation,
    ]
    return truncate_middle("\n".join(sections), max_chars)


def select_failure_lines(observation: str, max_lines: int = 48) -> str:
    lines = [line.rstrip() for line in (observation or "").splitlines()]
    if not lines:
        return ""

    patterns = (
        r"\berror\b",
        r"\bfailed\b",
        r"\bfailure\b",
        r"\bexception\b",
        r"\btraceback\b",
        r"\bconnection refused\b",
        r"\bunmet dependenc",
        r"\bnot found\b",
        r"\bno such file\b",
        r"\bpermission denied\b",
        r"\btimed? ?out\b",
        r"\bcannot\b",
        r"\bcould not\b",
        r"\baddress already in use\b",
    )
    selected = []
    for index, line in enumerate(lines):
        lower_line = line.lower()
        if any(re.search(pattern, lower_line) for pattern in patterns):
            start = max(0, index - 2)
            end = min(len(lines), index + 3)
            selected.extend(lines[start:end])
            if len(selected) >= max_lines:
                break

    if not selected:
        selected = [line for line in lines[-max_lines:] if line.strip()]

    deduped = []
    seen = set()
    for line in selected:
        key = line.strip()
        if not key or key in seen:
            continue
        deduped.append(line)
        seen.add(key)
        if len(deduped) >= max_lines:
            break

    return "\n".join(deduped)


def truncate_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 100:
        return text[:max_chars]
    marker = "\n... (middle truncated) ...\n"
    remaining = max_chars - len(marker)
    head = remaining // 2
    tail = remaining - head
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def normalize_memory(memory: dict[str, Any], repo_url: str, created_at: str) -> dict[str, Any]:
    normalized = dict(memory or {})
    normalized.pop("id", None)
    normalized["scope"] = normalized.get("scope") if normalized.get("scope") in {"global", "ecosystem", "repo"} else "global"
    normalized["repo"] = str(normalized.get("repo") or (repo_url if normalized["scope"] == "repo" else ""))
    normalized["problem_signature"] = str(normalized.get("problem_signature") or "").strip()
    normalized["symptoms"] = _as_string_list(normalized.get("symptoms"))
    normalized["root_cause"] = str(normalized.get("root_cause") or "").strip()
    normalized["successful_fix"] = _as_string_list(normalized.get("successful_fix"))
    normalized["verification"] = _as_string_list(normalized.get("verification"))
    normalized["anti_patterns"] = _as_string_list(normalized.get("anti_patterns"))
    normalized["embedding_text"] = str(normalized.get("embedding_text") or "").strip()
    normalized["linked_memories"] = _as_link_list(normalized.get("linked_memories"))
    normalized["created_at"] = str(normalized.get("created_at") or created_at)
    if not normalized["embedding_text"]:
        normalized["embedding_text"] = memory_to_embedding_text(normalized)
    return normalized


def is_valid_memory(memory: dict[str, Any]) -> bool:
    return bool(
        memory.get("problem_signature")
        and memory.get("root_cause")
        and memory.get("successful_fix")
        and memory.get("embedding_text")
    )


def memory_to_embedding_text(memory: dict[str, Any]) -> str:
    parts = [
        str(memory.get("problem_signature") or ""),
        "Symptoms: " + "; ".join(_as_string_list(memory.get("symptoms"))),
        "Root cause: " + str(memory.get("root_cause") or ""),
        "Successful fix: " + "; ".join(_as_string_list(memory.get("successful_fix"))),
        "Anti-patterns: " + "; ".join(_as_string_list(memory.get("anti_patterns"))),
    ]
    return " ".join(part for part in parts if part.strip()).strip()


def memory_for_relation_judge(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": memory.get("scope", ""),
        "repo": memory.get("repo", ""),
        "problem_signature": memory.get("problem_signature", ""),
        "symptoms": _as_string_list(memory.get("symptoms"))[:6],
        "root_cause": memory.get("root_cause", ""),
        "successful_fix": _as_string_list(memory.get("successful_fix"))[:6],
        "verification": _as_string_list(memory.get("verification"))[:3],
        "anti_patterns": _as_string_list(memory.get("anti_patterns"))[:6],
        "embedding_text": truncate_middle(str(memory.get("embedding_text") or ""), 1200),
        "source": memory.get("source", ""),
    }


def add_usage(total: MemoryUsage, increment: MemoryUsage):
    total.input_tokens += increment.input_tokens
    total.output_tokens += increment.output_tokens
    total.total_tokens += increment.total_tokens


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


def extract_json_array(text: str) -> list[dict[str, Any]]:
    candidate = _strip_fenced_block(text or "")
    json_blob = _extract_first_json_array(candidate)
    if not json_blob:
        raise MemoryManagerError("Memory extraction response did not contain a JSON array.")
    try:
        parsed = json.loads(json_blob)
    except json.JSONDecodeError as exc:
        raise MemoryManagerError(f"Failed to parse memory extraction JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise MemoryManagerError("Memory extraction response was not a JSON array.")
    return [item for item in parsed if isinstance(item, dict)]


def extract_json_object(text: str) -> dict[str, Any]:
    candidate = _strip_fenced_block(text or "")
    json_blob = _extract_first_json_object(candidate)
    if not json_blob:
        raise MemoryManagerError("Memory relation response did not contain a JSON object.")
    try:
        parsed = json.loads(json_blob)
    except json.JSONDecodeError as exc:
        raise MemoryManagerError(f"Failed to parse memory relation JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise MemoryManagerError("Memory relation response was not a JSON object.")
    return parsed


def _extract_first_json_array(text: str) -> Optional[str]:
    start = text.find("[")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _extract_first_json_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _strip_fenced_block(text: str) -> str:
    stripped = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _as_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _as_link_list(value) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dedupe_key(memory: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(memory.get("scope") or "").lower(),
        str(memory.get("repo") or "").lower(),
        str(memory.get("problem_signature") or "").strip().lower(),
    )


def _link_key(link: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(link.get("scope") or "").lower(),
        str(link.get("repo") or "").lower(),
        str(link.get("problem_signature") or "").strip().lower(),
    )


def _compact_run_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    compact = {
        "repo_url": summary.get("repo_url"),
        "configuration_success": summary.get("configuration_success"),
        "verified_test_commands": summary.get("verified_test_commands"),
        "verified_runtime_preparation_commands": summary.get("verified_runtime_preparation_commands"),
        "verification_bundle": summary.get("verification_bundle"),
        "required_local_services": summary.get("required_local_services"),
        "compression_stats": summary.get("compression_stats"),
        "error": summary.get("error"),
    }
    steps = summary.get("steps")
    if isinstance(steps, list):
        compact["steps"] = steps[-80:]
    return compact
