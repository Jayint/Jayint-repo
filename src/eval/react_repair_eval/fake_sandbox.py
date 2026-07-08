"""Offline 'reality' for the react mechanics eval — script-based, no Docker. Build succeeds
once the script contains every `install_token`; tests pass once it also contains every
`test_token`. A read-only probe returns scripted output."""
from __future__ import annotations

from src.react_repair.gate import TestOutcome
from src.react_repair.loop import RunResult


class FakeSandbox:
    def __init__(self, install_tokens=(), test_tokens=(), probes=None):
        self.install_tokens = tuple(install_tokens)
        self.test_tokens = tuple(test_tokens)
        self.probes = probes or {}
        self._script = ""

    def reset(self): pass

    def run_script(self, script):
        self._script = script
        missing = [t for t in self.install_tokens if t not in script]
        if missing:
            return RunResult(False, f"install {missing[0]}", f"{missing[0]}: not found")
        return RunResult(True)

    def certify(self, graph):
        return graph

    def exec_readonly(self, cmd):
        for key, out in self.probes.items():
            if key in cmd:
                return (0, out)
        return (0, "")

    def run_tests(self):
        if all(t in self._script for t in self.test_tokens):
            return TestOutcome(True, passed=5, executed=5, output="5 passed in 0.1s")
        missing = [t for t in self.test_tokens if t not in self._script]
        return TestOutcome(False, passed=0, executed=1,
                           output=f"ModuleNotFoundError: No module named '{missing[0]}'")
