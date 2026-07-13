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

ZERO COLLECTION ERRORS — THERE IS NO PARTIAL CREDIT. The gate requires pytest to exit 0 on BOTH runs. \
ONE un-importable test module rejects the ENTIRE repository, no matter how many thousands of tests \
collected around it. So never stop at the first traceback: after each `./verify`, enumerate EVERY \
distinct error and fix them all. A big repo routinely hides a dozen unrelated missing distributions \
behind the first one, and a run that fixes nine of ten scores exactly the same as one that fixes none.

HARD LIMITS OF THE COLLECTION SANDBOX. `./verify` collects inside a locked-down container: NO NETWORK \
(`--network none`), 2 CPUs, 4 GB RAM, 512 processes, all capabilities dropped. Design the Dockerfile \
around this:
- Everything a module needs AT IMPORT must be baked in at BUILD time. Nothing can be fetched during \
collection — no pip, no model weights, no fixture data, no NLTK/HuggingFace caches. Pre-fetch it in a \
RUN layer.
- Importing a heavyweight framework (torch, tensorflow, jax) can exhaust the 4 GB cap; the OOM \
surfaces as a confusing collection error or a killed process, not an ImportError. Prefer CPU-only \
wheels (`tensorflow-cpu`, torch's `+cpu` index) — they import in a fraction of the memory.
- A module that opens a socket at import time cannot be satisfied (no network). Install its client \
library anyway; that is often enough, because the connection is usually built lazily inside a fixture.

STABILITY — THE NODE-ID SET MUST BE IDENTICAL ACROSS THE TWO RUNS. If `./verify` reports the set \
unstable, something is nondeterministic: parametrize IDs derived from set/dict iteration, a timestamp, \
a uuid, a tempfile name, or filesystem glob order — or an installed plugin that generates tests \
dynamically. `ENV PYTHONHASHSEED=0` fixes the most common case (hash-order-dependent IDs). A repo can \
collect tens of thousands of tests cleanly and STILL be rejected on this clause alone, so if you see \
it, fix it — it is not cosmetic.
"""


# Per-repo hints. These describe the EXACT clause a prior attempt failed on, so the agent spends its
# budget on the real blocker instead of rediscovering it. They add information, never permission:
# every repo still faces the identical 5-clause host gate (exit 0 x2, non-hollow, stable node-id set,
# pristine protected files), so a hint cannot lower the bar — only aim the search.
REPO_HINTS: dict[str, str] = {
    "mlflow/mlflow": """\

REPO-SPECIFIC (from your last attempt in this harness). You collected ~16,300 tests but were REJECTED \
because pytest exited 2 on BOTH runs: collection ERRORS remained. Collecting a lot is worthless here \
without exit 0. mlflow's suite spans many ML "flavor" modules (sklearn, pytorch, tensorflow, xgboost, \
lightgbm, statsmodels, spark, ...) and each flavor test module imports its framework at module level, \
so each missing one is a hard collection error. Work the FULL error list, install from the repo's own \
`requirements/*.txt` (test/dev/skinny) plus the flavor frameworks, and use CPU-only wheels to stay \
inside the 4 GB import budget.
Your attempt before that died a different way: `docker build` itself ran past its wall-clock cap while \
installing the ML stack. The build is capped, so an image that never finishes building scores ZERO — \
worse than a lean one. Keep it economical: prebuilt wheels only (never a source build), CPU-only \
variants, `--no-cache-dir`, and a small number of merged RUN layers. Install what the failing imports \
actually name — not the whole ML ecosystem on spec.""",
    "Checkmk/checkmk": """\

REPO-SPECIFIC (from your last attempt in this harness). You were REJECTED for `protected files \
modified` — you edited files in the repository to force collection. EVERY tracked file except \
`Dockerfile` is hashed before and after; any edit is reverted and auto-rejects the repo, so that path \
can never succeed no matter how good the result looks. It also exited 1 on both runs (collection \
errors). Treat the Dockerfile as your only tool: install what the failing imports need. If a module \
genuinely cannot be made importable through the environment alone, this repo cannot certify — that is \
an acceptable outcome, and far better than an edit that guarantees rejection.""",
    "PostHog/posthog": """\

REPO-SPECIFIC. Your previous attempts died to harness infrastructure faults, not to your work — you \
are NOT starting from a failed design. Known: this repo collects ~77,000 node-IDs with zero \
import-skips, so the dependency side is essentially solvable. The blocker to expect is the STABILITY \
clause (the two collection runs disagreeing on the node-ID set), not missing packages. Set \
`ENV PYTHONHASHSEED=0` early, and if the set still differs, hunt the nondeterministic parametrize ID. \
It is a large Django repo: expect to need `DJANGO_SETTINGS_MODULE` and the django/pytest plugin stack \
installed for modules to import at all.""",
}


def prompt_for(repo_url: str) -> str:
    """TASK_PROMPT plus the repo's hint, when one exists. Matched on `owner/repo` so a
    `.git` suffix or trailing slash in the corpus URL cannot silently miss the hint."""
    key = repo_url.rstrip("/")
    if key.endswith(".git"):
        key = key[:-4]
    for full_name, hint in REPO_HINTS.items():
        if key.lower().endswith("/" + full_name.lower()):
            return TASK_PROMPT + hint
    return TASK_PROMPT


@dataclass(frozen=True)
class AgentResult:
    transcript_path: str | None
    claimed_done: bool
    raw_stdout: str


class AgentRunner(Protocol):
    def run(self, *, cwd: str, prompt: str, autonomous: bool) -> AgentResult: ...


def _default_run(argv, timeout=None, cwd=None):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        # Soft failure: a timed-out agent run becomes a failed attempt (rc 124 → claimed_done
        # False) rather than crashing build_one; keep-best over the other attempts still works.
        # On timeout, subprocess may hand back partial output as BYTES on one stream while the
        # other is None; decode each independently BEFORE concatenating, or `bytes + ""` raises
        # `TypeError: can't concat str to bytes` (hit on heavy repos nexent/feast that time out).
        def _text(x):
            if x is None:
                return ""
            return x.decode(errors="replace") if isinstance(x, bytes) else x
        out = _text(e.stdout) + _text(e.stderr)
        return 124, out + f"\n[agent timed out after {timeout}s]"


class ClaudeRunner:
    def __init__(self, *, model="opus", argv_template=None, run=None, timeout=None):
        env = os.environ.get("MANIFEST_AGENT_CMD")
        self.argv_template = argv_template or (env.split() if env else DEFAULT_CLAUDE_ARGV)
        self.model = model
        self._run = run or _default_run
        # Per-agent-run wall-clock cap. Default 1h; raise via $MANIFEST_AGENT_TIMEOUT (seconds)
        # for heavy repos whose dependency install/build can't finish in the default budget.
        self.timeout = timeout if timeout is not None else int(
            os.environ.get("MANIFEST_AGENT_TIMEOUT", "3600"))

    def run(self, *, cwd, prompt, autonomous):
        # Record the prompt for provenance; pass it inline via -p.
        with open(os.path.join(cwd, ".manifest_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
        argv = [a.format(cwd=cwd, prompt=prompt, model=self.model) for a in self.argv_template]
        # Run IN the workspace so the agent edits the Dockerfile / runs ./verify in place.
        rc, out = self._run(argv, timeout=self.timeout, cwd=cwd)
        transcript = os.path.join(cwd, ".manifest_agent_transcript.jsonl")
        with open(transcript, "w", encoding="utf-8") as f:
            f.write(out)   # --output-format stream-json => JSONL event stream
        return AgentResult(transcript_path=transcript, claimed_done=(rc == 0), raw_stdout=out)


class FakeRunner:
    def __init__(self, edit_fn=None):
        self.edit_fn = edit_fn

    def run(self, *, cwd, prompt, autonomous):
        if self.edit_fn:
            self.edit_fn(cwd)
        return AgentResult(transcript_path=None, claimed_done=True, raw_stdout="fake")
