import unittest

from src.planner import Planner


class PlannerManagedHistoryTests(unittest.TestCase):
    def test_replace_observation_updates_only_target_step(self):
        planner = Planner(client=None)
        planner.init_managed_history("https://github.com/example/repo.git")

        planner.append_step(1, "Thought: t1\nAction: a1", "obs1")
        planner.append_step(2, "Thought: t2\nAction: a2", "obs2")

        replaced = planner.replace_observation(1, "obs1-compressed")

        self.assertTrue(replaced)
        obs1_index = planner.managed_step_to_history_index[1]["observation"]
        obs2_index = planner.managed_step_to_history_index[2]["observation"]
        self.assertEqual(
            planner.managed_history[obs1_index]["content"],
            "Observation: obs1-compressed",
        )
        self.assertEqual(planner.managed_history[obs2_index]["content"], "Observation: obs2")

    def test_trim_rebuilds_index_for_recent_steps(self):
        planner = Planner(client=None)
        planner.init_managed_history("https://github.com/example/repo.git")

        for step_id in range(1, 15):
            planner.append_step(
                step_id,
                f"Thought: t{step_id}\nAction: a{step_id}",
                f"obs{step_id}",
            )

        self.assertNotIn(1, planner.managed_step_to_history_index)
        self.assertIn(14, planner.managed_step_to_history_index)

        replaced = planner.replace_observation(14, "obs14-compressed")
        self.assertTrue(replaced)
        obs_index = planner.managed_step_to_history_index[14]["observation"]
        self.assertEqual(
            planner.managed_history[obs_index]["content"],
            "Observation: obs14-compressed",
        )


if __name__ == "__main__":
    unittest.main()
