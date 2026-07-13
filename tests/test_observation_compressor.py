import unittest

from src.observation_compressor import (
    AgentStep,
    CompressionRecord,
    build_observation_metadata,
    extract_compressed_result_from_response,
    extract_result_block_from_rewritten_step,
    safety_compress_observation,
    serialize_context_for_compression,
    serialize_window_for_reflection,
    should_apply_compression,
)


class ObservationCompressorHelpersTests(unittest.TestCase):
    def test_build_observation_metadata_detects_test_and_install_markers(self):
        metadata = build_observation_metadata(
            "\n".join(
                [
                    "============================= test session starts =============================",
                    "collected 74 items",
                    "Successfully installed pytest==9.0.2",
                ]
            )
        )

        self.assertTrue(metadata["has_test_markers"])
        self.assertTrue(metadata["has_install_markers"])
        self.assertGreater(metadata["raw_tokens_est"], 0)

    def test_extract_result_block_unescapes_xml_entities(self):
        rewritten = (
            '<step id="3">\n'
            "<think>t</think>\n"
            '<call tool="bash">a</call>\n'
            "<result>\nline 1 &amp; line 2\n</result>\n"
            "</step>"
        )

        extracted = extract_result_block_from_rewritten_step(rewritten)

        self.assertEqual(extracted, "line 1 & line 2")

    def test_extract_result_block_rejects_wrong_target_step(self):
        target_step = AgentStep(
            step_id=2,
            thought="install deps",
            action="pip install -e .",
            success=False,
            exit_code=1,
            mutates_environment=True,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="target install output",
            observation_prompt="target install output",
        )
        rewritten = (
            '<step id="4" target="true">\n'
            "<think>run tests</think>\n"
            '<call tool="bash">pytest tests/</call>\n'
            "<result>\nModuleNotFoundError: No module named 'pytest_django_test'\n</result>\n"
            "</step>"
        )

        extracted = extract_result_block_from_rewritten_step(
            rewritten,
            target_step=target_step,
        )

        self.assertIsNone(extracted)

    def test_extract_result_block_requires_unchanged_thought_and_action(self):
        target_step = AgentStep(
            step_id=2,
            thought="install deps",
            action="pip install -e .",
            success=True,
            exit_code=0,
            mutates_environment=True,
            env_revision_before=0,
            env_revision_after=1,
            observation_raw="target install output",
            observation_prompt="target install output",
        )
        rewritten = (
            '<step id="2" target="true">\n'
            "<think>changed thought</think>\n"
            '<call tool="bash">pip install -e .</call>\n'
            "<result>\nshort install output\n</result>\n"
            "</step>"
        )

        extracted = extract_result_block_from_rewritten_step(
            rewritten,
            target_step=target_step,
        )

        self.assertIsNone(extracted)

    def test_extract_result_block_accepts_valid_target_step(self):
        target_step = AgentStep(
            step_id=2,
            thought="install deps",
            action="pip install -e . && pip install -r requirements.txt",
            success=True,
            exit_code=0,
            mutates_environment=True,
            env_revision_before=0,
            env_revision_after=1,
            observation_raw="target install output",
            observation_prompt="target install output",
        )
        rewritten = (
            '<step id="2" target="true">\n'
            "<think>install deps</think>\n"
            '<call tool="bash">pip install -e . &amp;&amp; pip install -r requirements.txt</call>\n'
            "<result>\nSuccessfully installed pytest-django\n</result>\n"
            "</step>"
        )

        extracted = extract_result_block_from_rewritten_step(
            rewritten,
            target_step=target_step,
        )

        self.assertEqual(extracted, "Successfully installed pytest-django")

    def test_extract_compressed_result_rejects_wrong_target_id(self):
        response = (
            '<compression target_step_id="4">\n'
            "<compressed_result>\nwrong result\n</compressed_result>\n"
            "</compression>"
        )

        extracted = extract_compressed_result_from_response(
            response,
            target_step_id=2,
        )

        self.assertIsNone(extracted)

    def test_extract_result_block_accepts_compression_protocol(self):
        target_step = AgentStep(
            step_id=2,
            thought="install deps",
            action="pip install -e .",
            success=True,
            exit_code=0,
            mutates_environment=True,
            env_revision_before=0,
            env_revision_after=1,
            observation_raw="target install output",
            observation_prompt="target install output",
        )
        response = (
            '<compression target_step_id="2">\n'
            "<compressed_result>\nSuccessfully installed pytest-django\n</compressed_result>\n"
            "</compression>"
        )

        extracted = extract_result_block_from_rewritten_step(
            response,
            target_step=target_step,
        )

        self.assertEqual(extracted, "Successfully installed pytest-django")

    def test_serialize_window_marks_target_step(self):
        step1 = AgentStep(
            step_id=1,
            thought="t1",
            action="echo 1",
            success=True,
            exit_code=None,
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="obs1",
            observation_prompt="obs1",
        )
        step2 = AgentStep(
            step_id=2,
            thought="t2",
            action="echo 2",
            success=True,
            exit_code=None,
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="obs2",
            observation_prompt="obs2",
        )

        serialized = serialize_window_for_reflection([step1, step2], target_step_id=2)

        self.assertIn('<step id="2" target="true">', serialized)
        self.assertIn("<trajectory>", serialized)
        self.assertIn("</trajectory>", serialized)

    def test_serialize_window_uses_prompt_observation_not_raw(self):
        step = AgentStep(
            step_id=1,
            thought="t1",
            action="echo 1",
            success=True,
            exit_code=None,
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="raw observation that should stay off the reflection prompt",
            observation_prompt="compressed prompt observation",
        )

        serialized = serialize_window_for_reflection([step], target_step_id=1)

        self.assertIn("compressed prompt observation", serialized)
        self.assertNotIn("raw observation that should stay off the reflection prompt", serialized)

    def test_serialize_context_for_compression_excludes_target_step(self):
        step1 = AgentStep(
            step_id=1,
            thought="t1",
            action="echo 1",
            success=True,
            exit_code=None,
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="obs1",
            observation_prompt="obs1",
        )
        step2 = AgentStep(
            step_id=2,
            thought="target",
            action="pip install -e .",
            success=False,
            exit_code=1,
            mutates_environment=True,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="target install output",
            observation_prompt="target install output",
        )
        step3 = AgentStep(
            step_id=3,
            thought="t3",
            action="pytest tests/",
            success=False,
            exit_code=1,
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="pytest output",
            observation_prompt="compressed pytest output",
        )

        serialized = serialize_context_for_compression([step1, step2, step3], target_step_id=2)

        self.assertIn("CONTEXT STEP 1", serialized)
        self.assertIn("CONTEXT STEP 3", serialized)
        self.assertIn("compressed pytest output", serialized)
        self.assertNotIn("CONTEXT STEP 2", serialized)
        self.assertNotIn("target install output", serialized)

    def test_should_apply_compression_respects_benefit_threshold(self):
        step = AgentStep(
            step_id=1,
            thought="t",
            action="a",
            success=True,
            exit_code=None,
            mutates_environment=False,
            env_revision_before=0,
            env_revision_after=0,
            observation_raw="x" * 2000,
            observation_prompt="x" * 2000,
        )
        record = CompressionRecord(
            eligible=True,
            reduced_chars=100,
            reduced_tokens_est=100,
            original_tokens_est=600,
            saved_tokens_est=500,
        )

        apply_ok, reason = should_apply_compression(
            step,
            record,
            compress_threshold_chars=1500,
            benefit_threshold_tokens=300,
        )

        self.assertTrue(apply_ok)
        self.assertEqual(reason, "applied")

    def test_safety_compress_observation_reduces_huge_output_and_keeps_summary(self):
        observation = "\n".join(
            ["[INFO] Scanning for projects..."]
            + [f"Progress (1): {index}/500" for index in range(500)]
            + [
                "[INFO] Reactor Summary for Demo 1.0:",
                "[INFO] demo-core ..................................... SUCCESS [ 10.000 s]",
                "[INFO] demo-api ...................................... SUCCESS [ 20.000 s]",
                "[INFO] BUILD SUCCESS",
                "[INFO] Total time:  00:30 min",
                "[INFO] Finished at: 2026-03-20T08:33:45Z",
            ]
        )

        compressed, applied = safety_compress_observation(
            observation,
            threshold_chars=1000,
            target_chars=600,
        )

        self.assertTrue(applied)
        self.assertLess(len(compressed), len(observation))
        self.assertIn("[Safety Compression Applied]", compressed)
        self.assertIn("[INFO] BUILD SUCCESS", compressed)
        self.assertIn("[INFO] Total time:  00:30 min", compressed)

    def test_safety_compress_observation_is_noop_below_threshold(self):
        observation = "short output\nall good"

        compressed, applied = safety_compress_observation(
            observation,
            threshold_chars=1000,
            target_chars=600,
        )

        self.assertFalse(applied)
        self.assertEqual(compressed, observation)


if __name__ == "__main__":
    unittest.main()
