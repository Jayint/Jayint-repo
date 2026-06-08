"""Unit tests for src.recipe_repair — deterministic + LLM recipe repair."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.recipe_repair import (
    _extract_json_object,
    _pip_requirement_name,
    _recipe_already_installs,
    build_recipe_repair_input,
    collect_project_config_context,
    extract_missing_modules,
    normalize_repaired_recipe,
    repair_recipe_for_missing_modules,
    repair_recipe_with_llm,
    requirement_for_missing_module,
)


class FakeLLMClient:
    """Minimal OpenAI-compatible client returning a fixed completion content."""

    def __init__(self, content, raise_exc=None):
        self._content = content
        self._raise = raise_exc

        def _create(model, messages, temperature=0):
            if self._raise:
                raise self._raise
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


class TestMissingModuleExtraction(unittest.TestCase):
    def test_extracts_and_dedupes_top_level(self):
        text = (
            "ModuleNotFoundError: No module named 'redis'\n"
            "ModuleNotFoundError: No module named 'redis'\n"
            "ImportError: No module named 'narwhals.stable'\n"
        )
        self.assertEqual(extract_missing_modules(text), ["redis", "narwhals"])

    def test_empty(self):
        self.assertEqual(extract_missing_modules("all good"), [])


class TestPipName(unittest.TestCase):
    def test_strips_specifiers_and_extras(self):
        self.assertEqual(_pip_requirement_name("Redis==5.0.1"), "redis")
        self.assertEqual(_pip_requirement_name("typing_extensions>=4"), "typing-extensions")
        self.assertEqual(_pip_requirement_name("uvicorn[standard]"), "uvicorn")

    def test_unnameable(self):
        self.assertEqual(_pip_requirement_name("-e ."), "")
        self.assertEqual(_pip_requirement_name("git+https://x/y.git"), "")


class TestRecipeAlreadyInstalls(unittest.TestCase):
    def test_token_level_match_not_substring(self):
        cmds = ["pip install redis-py-cluster"]
        # "redis" must NOT match "redis-py-cluster"
        self.assertFalse(_recipe_already_installs(cmds, "redis"))
        self.assertTrue(_recipe_already_installs(["pip install redis==5.0"], "redis"))

    def test_ignores_non_pip_commands(self):
        self.assertFalse(_recipe_already_installs(["apt-get install redis"], "redis"))


class TestRequirementResolution(unittest.TestCase):
    def test_prefers_declared_pin(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "requirements.txt").write_text("redis==4.6.0\nflask>=2\n")
            self.assertEqual(requirement_for_missing_module("redis", Path(d)), "redis==4.6.0")

    def test_falls_back_to_module_name(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(requirement_for_missing_module("absl", Path(d)), "absl")

    def test_known_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(requirement_for_missing_module("ppocr", Path(d)), "paddleocr==2.7.3")


class TestDeterministicRepair(unittest.TestCase):
    def test_appends_pip_install_and_is_immutable(self):
        recipe = {"build_commands": ["pip install -e ."], "test_commands": ["pytest"]}
        snapshot = copy.deepcopy(recipe)
        new_recipe, installed = repair_recipe_for_missing_modules(recipe, ["redis"], None)
        self.assertEqual(installed, ["redis"])
        self.assertIn("pip install redis", new_recipe["build_commands"])
        # original untouched (immutability rule)
        self.assertEqual(recipe, snapshot)

    def test_skips_already_installed(self):
        recipe = {"build_commands": ["pip install redis==5.0"], "test_commands": ["pytest"]}
        new_recipe, installed = repair_recipe_for_missing_modules(recipe, ["redis"], None)
        self.assertEqual(installed, [])
        self.assertEqual(new_recipe["build_commands"], ["pip install redis==5.0"])

    def test_uses_declared_pin(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "requirements.txt").write_text("narwhals==1.2.3\n")
            new_recipe, installed = repair_recipe_for_missing_modules(
                {"build_commands": [], "test_commands": ["pytest"]}, ["narwhals"], Path(d),
            )
            self.assertEqual(installed, ["narwhals==1.2.3"])

    def test_dedupes_within_call(self):
        new_recipe, installed = repair_recipe_for_missing_modules(
            {"build_commands": [], "test_commands": ["pytest"]}, ["redis", "redis"], None,
        )
        self.assertEqual(installed, ["redis"])


class TestJsonExtraction(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(_extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_brace_balanced_with_prose(self):
        self.assertEqual(_extract_json_object('Here: {"a": {"b": 2}} done'), {"a": {"b": 2}})

    def test_invalid(self):
        self.assertIsNone(_extract_json_object("no json here"))


class TestNormalizeRepairedRecipe(unittest.TestCase):
    def test_overlays_and_clamps_confidence(self):
        current = {"build_commands": ["a"], "test_commands": ["pytest"], "confidence": "high"}
        candidate = {"build_commands": ["a", "b"], "test_commands": ["pytest -k x"],
                     "confidence": "BOGUS", "rationale": "fix"}
        result = normalize_repaired_recipe(candidate, current)
        self.assertEqual(result["build_commands"], ["a", "b"])
        self.assertEqual(result["confidence"], "low")  # invalid clamps to low
        self.assertEqual(result["rationale"], "fix")

    def test_rejects_when_test_commands_emptied(self):
        current = {"build_commands": ["a"], "test_commands": ["pytest"]}
        self.assertIsNone(normalize_repaired_recipe({"test_commands": []}, current))

    def test_non_dict(self):
        self.assertIsNone(normalize_repaired_recipe(None, {"test_commands": ["pytest"]}))


class TestLLMRepair(unittest.TestCase):
    def test_success(self):
        client = FakeLLMClient(
            '{"build_commands": ["pip install -e ."], "test_commands": ["pytest"], '
            '"runtime_preparation_commands": [], "post_test_patch_commands": [], '
            '"excluded_commands": [], "rationale": "add editable install", "confidence": "high"}'
        )
        recipe = {"build_commands": [], "test_commands": ["pytest"]}
        repaired, record = repair_recipe_with_llm(
            client, "model-x", recipe, {"stderr": "ModuleNotFoundError"}, None,
        )
        self.assertIsNotNone(repaired)
        self.assertIn("pip install -e .", repaired["build_commands"])
        self.assertIsNone(record["error"])
        self.assertEqual(record["usage"]["total_tokens"], 30)

    def test_client_error_is_captured_not_raised(self):
        client = FakeLLMClient("", raise_exc=RuntimeError("boom"))
        repaired, record = repair_recipe_with_llm(
            client, "model-x", {"test_commands": ["pytest"]}, {}, None,
        )
        self.assertIsNone(repaired)
        self.assertEqual(record["error"], "boom")


class TestProjectConfigContext(unittest.TestCase):
    def test_collects_known_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("[project]\nname='x'\n")
            (Path(d) / "irrelevant.md").write_text("ignore me")
            ctx = collect_project_config_context(Path(d))
            self.assertIn("pyproject.toml", ctx)
            self.assertNotIn("irrelevant.md", ctx)

    def test_build_repair_input_shape(self):
        payload = build_recipe_repair_input(
            {"test_commands": ["pytest"]},
            {"stage": "test", "missing_modules": ["redis"], "stdout": "x", "stderr": "y"},
            None, repo_url="https://example/r",
        )
        self.assertEqual(payload["repo_url"], "https://example/r")
        self.assertEqual(payload["failure"]["missing_modules"], ["redis"])


if __name__ == "__main__":
    unittest.main()
