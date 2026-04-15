import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.memory_manager import (
    LongTermMemoryManager,
    build_failure_query,
    extract_json_array,
    select_failure_lines,
)


class FakeEmbedder:
    vocabulary = [
        "setuptools",
        "canonicalize",
        "postgresql",
        "connection",
        "elasticsearch",
        "redis",
    ]

    def embed(self, text, is_query=False):
        lowered = text.lower()
        return [1.0 if token in lowered else 0.0 for token in self.vocabulary]


class FakeMemoryClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
                )
            )
        )


class MemoryManagerTests(unittest.TestCase):
    def test_build_failure_query_uses_system_context(self):
        query = build_failure_query(
            failed_command="mvn test",
            failed_observation="INFO\nERROR: connection refused on localhost:5433\nDONE",
            repo_url="https://github.com/example/repo.git",
            services=["postgresql"],
        )

        self.assertIn("Repository: https://github.com/example/repo.git", query)
        self.assertIn("Failed command: mvn test", query)
        self.assertIn("Known local services: postgresql", query)
        self.assertIn("connection refused", query)

    def test_select_failure_lines_prefers_error_context(self):
        selected = select_failure_lines(
            "\n".join(
                [
                    "line 1",
                    "line 2",
                    "Collecting package",
                    "TypeError: canonicalize_version() got an unexpected keyword argument",
                    "metadata-generation-failed",
                    "tail",
                ]
            )
        )

        self.assertIn("canonicalize_version", selected)
        self.assertIn("metadata-generation-failed", selected)

    def test_write_and_retrieve_memories_from_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "long_term_memories.jsonl"
            manager = LongTermMemoryManager(
                memory_path=memory_path,
                embedder=FakeEmbedder(),
            )

            write_result = manager.write_memories(
                [
                    {
                        "scope": "ecosystem",
                        "repo": "pallets-eco/flask-wtf",
                        "problem_signature": "old setup.py setuptools canonicalize_version failure",
                        "symptoms": ["canonicalize_version strip_trailing_zero TypeError"],
                        "root_cause": "new setuptools is incompatible with old setup.py metadata generation",
                        "successful_fix": ["install setuptools<67 before pip install -e ."],
                        "verification": ["pytest passed"],
                        "anti_patterns": ["do not only switch editable/non-editable install"],
                        "embedding_text": "setuptools canonicalize canonicalize_version metadata-generation-failed",
                        "linked_memories": [],
                        "created_at": "2026-04-14T00:00:00+08:00",
                    }
                ],
                repo_url="https://github.com/pallets-eco/flask-wtf.git",
            )

            self.assertEqual(write_result.written, 1)
            memories = manager.load_memories()
            self.assertEqual(len(memories), 1)
            self.assertNotIn("id", memories[0])
            self.assertIn("embedding", memories[0])

            results, _query = manager.retrieve(
                failed_command="pip install -e .",
                failed_observation="TypeError: canonicalize_version() got an unexpected keyword argument 'strip_trailing_zero'",
                repo_url="https://github.com/other/repo.git",
            )

            self.assertEqual(len(results), 1)
            self.assertIn("setuptools", results[0].memory["problem_signature"])

    def test_link_generation_uses_embedding_similarity_without_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "long_term_memories.jsonl"
            manager = LongTermMemoryManager(memory_path=memory_path, embedder=FakeEmbedder())
            manager.write_memories(
                [
                    {
                        "scope": "ecosystem",
                        "repo": "",
                        "problem_signature": "postgresql connection refused after service install",
                        "root_cause": "server was not started",
                        "successful_fix": ["start postgresql server"],
                        "embedding_text": "postgresql connection refused",
                    }
                ]
            )

            manager.write_memories(
                [
                    {
                        "scope": "ecosystem",
                        "repo": "",
                        "problem_signature": "postgresql connection failure on configured port",
                        "root_cause": "configured database port was not listening",
                        "successful_fix": ["start postgresql on configured port"],
                        "embedding_text": "postgresql connection refused configured port",
                    }
                ]
            )

            memories = manager.load_memories()
            self.assertEqual(len(memories), 2)
            self.assertEqual(len(memories[1]["linked_memories"]), 1)
            self.assertIn("problem_signature", memories[1]["linked_memories"][0])
            self.assertNotIn("id", memories[1]["linked_memories"][0])

    def test_extract_json_array_accepts_fenced_json(self):
        extracted = extract_json_array(
            """```json
[
  {"scope": "global", "problem_signature": "x"}
]
```"""
        )

        self.assertEqual(extracted, [{"scope": "global", "problem_signature": "x"}])

    def test_generate_memories_from_run_writes_llm_candidates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "long_term_memories.jsonl"
            content = json.dumps(
                [
                    {
                        "scope": "ecosystem",
                        "repo": "",
                        "problem_signature": "redis tests repeatedly failed until redis-server was started",
                        "symptoms": ["connection refused"],
                        "root_cause": "redis server was absent",
                        "successful_fix": ["install and start redis-server"],
                        "verification": ["tests passed"],
                        "anti_patterns": ["do not only install redis-cli"],
                        "embedding_text": "redis connection refused install start redis-server",
                        "linked_memories": [],
                        "created_at": "",
                    }
                ]
            )
            manager = LongTermMemoryManager(
                client=FakeMemoryClient(content),
                llm_model="fake-model",
                memory_path=memory_path,
                embedder=FakeEmbedder(),
            )

            result = manager.generate_memories_from_run(
                setup_log_text="failed twice, then redis-server start, tests passed",
                run_summary={"configuration_success": True},
                repo_url="https://github.com/example/repo.git",
            )

            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(result.written, 1)
            self.assertEqual(result.usage.total_tokens, 18)


if __name__ == "__main__":
    unittest.main()
