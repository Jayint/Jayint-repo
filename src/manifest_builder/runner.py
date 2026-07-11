from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Protocol

# Claude Code headless invocation. Placeholders substituted per run: {prompt}, {model}
# ({cwd} is honored too if a $MANIFEST_AGENT_CMD override includes it). The subprocess runs
# with CWD set to the workspace (Claude Code has no --cwd flag), so the agent edits the
# Dockerfile and runs ./verify in place. --dangerously-skip-permissions = fully autonomous
# (bypasses ALL permission prompts, may run docker/pip); stream-json + --verbose = JSONL
# transcript. Overridable via $MANIFEST_AGENT_CMD (space-split).
#   claude -p "<prompt>" --dangerously-skip-permissions --model <model>
#          --output-format stream-json --verbose
DEFAULT_CLAUDE_ARGV = ["claude", "-p", "{prompt}", "--dangerously-skip-permissions",
                       "--model", "{model}", "--output-format", "stream-json", "--verbose"]

TASK_PROMPT = """\
You are configuring a reproducible test-COLLECTION environment for a Python repository. Your ONLY \
editable file is `Dockerfile` in this directory. Your goal: make the repository's full pytest suite \
collect cleanly and maximally.

SUCCESS CRITERION. Run `./verify`. It builds your Dockerfile from scratch, runs `pytest \
--collect-only` inside the image twice, and reports:
- `accepted: true` when collection is clean (no collection errors) and stable across both runs. REQUIRED.
- `collected=N` — number of tests collected. MAXIMIZE this.
- `import_skipped=[modules]` — modules pytest skipped at import time, usually a missing optional \
dependency hiding real tests.
You are done when `./verify` reports accepted AND `collected` is as high as it will go — i.e. \
`import_skipped` contains only modules that are genuinely optional or whose dependency truly cannot \
be installed. A clean collection that hides half the suite behind missing deps is a FAILURE, not a pass.

THE ONLY LEVER IS THE ENVIRONMENT. `pytest --collect-only` works by IMPORTING every test module, so \
collection fails or shrinks only because the environment is missing something: a collection error \
(ImportError/ModuleNotFoundError) means a dependency isn't installed; an import-skipped module means \
an optional dep behind importorskip(...) isn't installed. The fix is always to install the missing \
dependency in the Dockerfile — read the failing import in the traceback, find the PyPI or apt package \
that provides it, and add it.

SERVICES (databases, brokers, etc.). `pytest --collect-only` only imports modules — it never runs \
tests or fixtures — so tests needing a live service (Redis, Postgres, RabbitMQ, ...) to PASS still \
collect fine without that service running. Install the service's Python CLIENT LIBRARY (e.g. redis, \
psycopg2-binary, pika) when a module fails to import it, but do NOT try to start the actual \
database/broker: it isn't needed for collection, and collection runs with NO network access. The one \
exception is a module that opens a connection at import time (top-level code, not inside a test or \
fixture) — live services can't be provided during collection, so leave those in import_skipped, \
install the client library, and move on.

RULES.
- Edit ONLY the `Dockerfile`. Do NOT touch tests, conftest.py, pyproject.toml, pytest.ini, setup.cfg, \
tox.ini, or any source file. The harness restores all of these to their pinned originals and \
hash-checks them before certifying — any edit you make is reverted and rejected, so it cannot help you.
- Do NOT fake a clean collection by hiding tests: no --ignore/-k/-m/--deselect, no \
collect_ignore/norecursedirs, no deleting or emptying test files or narrowing paths. All rejected. \
The only path that works is installing dependencies.
- Do NOT install anything that randomizes collection (e.g. pytest-randomly); the node-ID set must be \
identical across both runs.
- Do NOT run test bodies — only collection matters; tests never need to pass, only to be importable.
- The Dockerfile must build cleanly from scratch; no reliance on host state.

SUGGESTED WORKFLOW. Run `./verify` -> read the first traceback -> install the missing import in the \
Dockerfile, preferring the repo's DECLARED test/dev groups first (`pip install -e .[test]`/`[dev]`/\
`[all]`, requirements-dev.txt, test-requirements.txt) and `apt-get install` for C-library imports -> \
re-run. Repeat until accepted and `import_skipped` is minimal. Declared dependency groups usually \
close most collection gaps at once.
"""


@dataclass(frozen=True)
class AgentResult:
    transcript_path: str | None
    claimed_done: bool
    raw_stdout: str


class AgentRunner(Protocol):
    def run(self, *, cwd: str, prompt: str, autonomous: bool) -> AgentResult: ...


def _default_run(argv, timeout=None, cwd=None):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


class ClaudeRunner:
    def __init__(self, *, model="opus", argv_template=None, run=None):
        env = os.environ.get("MANIFEST_AGENT_CMD")
        self.argv_template = argv_template or (env.split() if env else DEFAULT_CLAUDE_ARGV)
        self.model = model
        self._run = run or _default_run

    def run(self, *, cwd, prompt, autonomous):
        # Record the prompt for provenance; pass it inline via -p.
        with open(os.path.join(cwd, ".manifest_prompt.txt"), "w") as f:
            f.write(prompt)
        argv = [a.format(cwd=cwd, prompt=prompt, model=self.model) for a in self.argv_template]
        # Run IN the workspace so the agent edits the Dockerfile / runs ./verify in place.
        rc, out = self._run(argv, timeout=3600, cwd=cwd)
        transcript = os.path.join(cwd, ".manifest_agent_transcript.jsonl")
        with open(transcript, "w") as f:
            f.write(out)   # --output-format stream-json => JSONL event stream
        return AgentResult(transcript_path=transcript, claimed_done=(rc == 0), raw_stdout=out)


class FakeRunner:
    def __init__(self, edit_fn=None):
        self.edit_fn = edit_fn

    def run(self, *, cwd, prompt, autonomous):
        if self.edit_fn:
            self.edit_fn(cwd)
        return AgentResult(transcript_path=None, claimed_done=True, raw_stdout="fake")
