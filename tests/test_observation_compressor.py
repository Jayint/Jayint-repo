import unittest

from src.observation_compressor import (
    AgentStep,
    CompressionRecord,
    build_observation_metadata,
    extract_result_block_from_rewritten_step,
    safety_compress_observation,
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
