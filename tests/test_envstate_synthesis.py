import unittest

from src.envstate.ledger import ActionEvent, ActionLedger
from src.envstate.synthesis import build_commands_from_ledger


def _event(step, cmd, rc, mutation_class):
    return ActionEvent(step=step, task_id=None, cmd=cmd, rc=rc, stdout_path=None,
                       stderr_path=None, env_revision_before=0, env_revision_after=0,
                       mutation_class=mutation_class, container_id="c1", summary="")


class SynthesisFromLedgerTests(unittest.TestCase):
    def test_keeps_only_successful_mutating_commands_in_order(self):
        ledger = ActionLedger()
        ledger.append(_event(1, "cat README.md", 0, None))                  # read-only -> drop
        ledger.append(_event(2, "apt-get install -y libpq-dev", 0, "system_package_install"))
        ledger.append(_event(3, "pip install psycopg2==2.8.6", 1, "language_package_install"))  # failed -> drop
        ledger.append(_event(4, "pip install psycopg2==2.8.6", 0, "language_package_install"))
        ledger.append(_event(5, "pytest -q", 0, None))                      # test -> drop
        commands = build_commands_from_ledger(ledger)
        self.assertEqual(commands, ["apt-get install -y libpq-dev", "pip install psycopg2==2.8.6"])

    def test_preserves_duplicate_order_sensitive_commands(self):
        ledger = ActionLedger()
        ledger.append(_event(1, "pip install a", 0, "language_package_install"))
        ledger.append(_event(2, "pip install b", 0, "language_package_install"))
        ledger.append(_event(3, "pip install a", 0, "language_package_install"))  # re-install kept (order matters)
        commands = build_commands_from_ledger(ledger)
        self.assertEqual(commands, ["pip install a", "pip install b", "pip install a"])

    def test_distill_callback_is_applied_to_kept_commands(self):
        ledger = ActionLedger()
        ledger.append(_event(1, "pip install -r requirements.txt && pytest -q", 0, "language_package_install"))
        ledger.append(_event(2, "cat README.md", 0, None))  # dropped before distill runs

        def distill(cmd):
            return [cmd.split("&&")[0].strip()]  # mimic prefix-only distillation

        commands = build_commands_from_ledger(ledger, distill=distill)
        self.assertEqual(commands, ["pip install -r requirements.txt"])


class LedgerSynthesisAgentPathTests(unittest.TestCase):
    def _make_agent(self):
        from agent import DockerAgent
        from src.synthesizer import Synthesizer
        agent = DockerAgent.__new__(DockerAgent)
        agent.synthesizer = Synthesizer()
        agent.enable_envstate = True
        agent.action_ledger = ActionLedger()
        return agent

    def test_agent_ledger_path_strips_trailing_test_from_compound_action(self):
        agent = self._make_agent()
        agent.action_ledger.append(_event(1, "apt-get install -y libpq-dev", 0, "system_package_install"))
        agent.action_ledger.append(_event(2, "pip install -r requirements.txt && pytest -q", 0, "language_package_install"))
        ok = agent._synthesize_final_build_recipe()
        self.assertTrue(ok)
        self.assertEqual(agent.build_recipe_source, "action_ledger")
        # the trailing `&& pytest -q` must NOT appear in any build command
        self.assertTrue(all("pytest" not in c for c in agent.build_recipe["build_commands"]))
        self.assertIn("apt-get install -y libpq-dev", agent.build_recipe["build_commands"])
