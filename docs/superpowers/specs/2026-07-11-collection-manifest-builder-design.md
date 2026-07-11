# Collection Gold-Manifest Builder — Design

**Date:** 2026-07-11 · **Status:** Design under review · **Branch:** john-v3-multi-lang

## Goal

Produce, for a repository at a pinned commit, the **maximum cleanly-collectable set of pytest node
IDs** — a fixed, reproducible, execution-independent reference set ("golden set"). The point is
*maximum*: the more legitimate tests we can make collect cleanly (by fully provisioning the
environment so nothing is hidden behind a missing dependency), the tighter the lower bound. This
golden set is the fixed denominator a downstream benchmark can score against instead of
`passed / candidate_collected` (which a broken env silently shrinks).

A **SOTA coding agent** (Claude Code, behind a swappable seam) configures the environment
to **maximize clean collection**; the **harness independently certifies** the result and emits a
signed manifest + certificate. This is a **silver-standard reference set** (a reproducible *lower
bound* on collectible tests — as large as full provisioning makes it), **not** an absolute count of
every test that theoretically exists — see §12.

**Standalone by design.** This is a self-contained module (`src/manifest_builder/`) with its own
collection plugin, docker adapter, and evals. It deliberately does **not** depend on the `bench/`
runner or any other arm. Its output (`collected-nodeids.json` per repo, canonical pytest nodeids)
is a plain, consumer-agnostic artifact; wiring it into any benchmark is that benchmark's concern.

## Architecture (three sentences)

An external coding agent is dropped into a pristine, SHA-pinned work-tree and told to edit **only
the Dockerfile** until one command — `verify` — exits 0. `verify` is host-owned: it restores the
pristine protected files, builds the Dockerfile independently, runs `pytest --collect-only -q`
twice through a collection plugin, and applies a four-clause accept predicate. Because we never
trust the agent's self-report, the harness runs that **same** `verify` itself as final
certification, and emits `collected-nodeids.json` + `collection-certificate.json` only if it
independently passes.

**Tech stack:** Python; Docker on the x86_64 VM; a self-contained module owning its collection
plugin (`collect_plugin.py`), its docker adapter, and a simple `python:{minor}-slim` seed base
(docker-CLI subprocess and hardened-flag idioms copied as *pattern* from `repo2run_repair_port.py`
/ `scripts/audit_swesmith_images.py`, not imported); an external agent CLI via a swappable
`AgentRunner`.

---

## 1. Motivation & context

Direct follow-on to the ESSR-denominator investigation (`essr-denominator-is-agent-chosen`,
`swesmith-gold-manifest-investigation`). The denominator today is **whatever the agent's own
pytest run collected**, so a more-complete env is punished with a bigger denominator while a
broken env silently shrinks its own. Two prior candidate denominators were refuted: SWE-smith's
published manifests (a lower bound for only the 70 stable repos; whitespace-param blind), and a
static AST/grep test count (700,000× spread, parametrize-blind). The remaining sound path is a
**reference-run collection at a pinned SHA in a certified environment** — exactly what this builds,
generically, for any repo, rather than recovering it from a dataset.

The key inversion from prior repair arms: **the host owns the gate, the agent only proposes.** We
do not build a ReAct loop — a SOTA agent supplies the loop. We build the pristine substrate, the
independent verifier, and the certificate.

## 2. Substrate: Docker, not a bare `.venv`

The certified artifact is built and collected in **Docker**, on the x86_64 VM. A `.venv` is
functionally sufficient for pure-Python repos but cannot deliver the artifact's core property —
reproducibility:

- **Hashable, pinnable *whole* environment.** The certificate hashes a **base-image digest**. A
  venv captures only Python packages and inherits the VM's interpreter build, apt libraries,
  locale, and system tools — none of which is pinnable or hashable. The manifest would reproduce
  only on that one VM.
- **System libraries.** The discriminating repos (native drivers, service clients, C-extensions)
  need `apt` packages. In a venv the agent would `sudo apt install` on the shared VM — global,
  un-isolated, un-reproducible, box-polluting. In Docker they are Dockerfile layers.
- **Clean-room collection.** `--network none --cap-drop ALL` (audit-tool flags) needs a container.

**venv's legitimate role:** the agent's *private scratch inner-loop* for iteration speed (pip
install + test in a throwaway venv or live container, then freeze what worked into the Dockerfile).
The **certificate always comes from an independent fresh `docker build` + clean-room collect** —
never from the scratch env.

**Base image:** a simple `python:{minor}-slim` seed (minor from `requires-python` if trivially
present, else a default) — no dependency on the shared `ImageSelector`; the agent adjusts the base
and adds `apt`/`pip` layers on top, and the certified base is digest-pinned at certification.
BuildKit **layer caching** keeps per-cycle builds cheap (base + apt cached; only the changed tail
rebuilds).

## 3. Core flow

```
prepare_workspace(repo_url, sha):
    clone at sha → pristine work-tree
    PROTECTED = all files tracked at sha, except the Dockerfile             # §6
    hash PROTECTED on host → pristine_hashes
    seed starter Dockerfile (python:{minor}-slim, WORKDIR /src, COPY . /src, pip install -e .)
    write verify entrypoint (wraps §4)

run_agent(workspace, task_prompt, verify):                                    # §9
    AgentRunner.run(cwd=workspace, prompt=task_prompt, autonomous=True)
    # the agent edits ONLY the Dockerfile, loops itself against `verify`, returns a transcript

certify(workspace):                    # host-owned; identical code to `verify`  # §4
    restore_pristine(PROTECTED)                     # wipe any agent edits to tests/config
    image, build_log, digest = docker_build(workspace/Dockerfile)
    in_image_hashes = hash PROTECTED inside image
    r1 = collect(image)                             # pytest --collect-only -q, plugin
    r2 = collect(image)                             # second run for stability
    protected_ok = (in_image_hashes == pristine_hashes)
    verdict = gate.accept(r1, r2, protected_ok)     # §5
    return build_certificate(verdict, r1, r2, hashes, digest, ...)             # §7

main:
    prepare
    for _ in range(attempts):                    # each attempt = one independent agent run
        reset Dockerfile to seed (fresh start)   # §6 — independent maximization samples
        run_agent → certify                      # §4
    keep the highest-collected_count CERTIFIED attempt (pick_best; else best-effort reject)  # §5
    emit the WINNING attempt's artifacts (§8)
```

There is no ReAct loop in our code. The agent's loop is its own; ours is `prepare → (agent →
certify) × attempts → keep-best`. We run **all** `attempts` (default 3), each an independent
fresh-seed sample, and certify the largest clean-and-stable-and-pristine collection — guarding
against a single agent run under-collecting.

## 4. The `verify` path (one code path, two roles)

`verify` **is** `certify` minus artifact emission. The **agent runs it as its success oracle**
("make `verify` exit 0"); the **harness runs it as the certifier** at the end, distrusting whatever
the agent claimed. Because both restore pristine protected files first, the agent directly observes
that editing a test never helps — the only durable lever is the Dockerfile. This closes the
raw-`rc==0` loophole: the agent optimizes against the real four-clause gate, not bare pytest exit.

`collect()` runs, in a locked-down container
(`--network none --cpus 2 --memory 4g --pids-limit 512 --security-opt no-new-privileges
--cap-drop ALL`, from `audit_swesmith_images.py`):

```
pytest --collect-only -q -p no:cacheprovider -p manifest_collect_plugin
```

Crucially **without** `--continue-on-collection-errors`, so pytest's own exit semantics hold:
`0` = clean collection, `2` = collection error, `5` = no tests. The plugin
(`collect_plugin.py`, §11) captures node IDs from `pytest_collection_finish`,
plus collect-time skipped/failed collectreports and deselections, into structured JSON.

## 5. Accept predicate (the pure, tested heart — `gate.py`)

```python
def accept(r1: CollectionResult, r2: CollectionResult, protected_ok: bool) -> Verdict:
    reasons = []
    if r1.exit_code != 0:                    reasons.append(f"run1 exit {r1.exit_code} != 0")
    if r2.exit_code != 0:                    reasons.append(f"run2 exit {r2.exit_code} != 0")
    if r1.collected_count == 0:              reasons.append("no items collected (hollow)")
    if set(r1.collected) != set(r2.collected): reasons.append("node-id set unstable across runs")
    if not protected_ok:                     reasons.append("protected files modified")
    accepted = not reasons
    manifest = sorted(set(r1.collected)) if accepted else None
    return Verdict(accepted=accepted, reasons=reasons, manifest=manifest)
```

Four clauses:

1. **`exit_code == 0`** on both runs — clean collection. Subsumes the original spec's separate
   `collection_errors == 0` (pytest returns 2 on any collection error; we don't pass
   `--continue-on-collection-errors`).
2. **`collected_count > 0`** — anti-hollow (a zero-collect env cannot certify an empty manifest).
3. **`set(run1) == set(run2)`** — node-ID set is stable across two independent runs (compared as
   sets, so ordering noise is tolerated; content drift, e.g. from `pytest-randomly` or nondeterministic
   parametrization, is rejected).
4. **`protected_ok`** — every protected file in the built image byte-matches the pristine host tree.

**The objective is maximization, not just a pass.** The gate above is a *floor* (is this a valid,
clean, honest collection?); the *goal* is the **largest** such collection. Every clean candidate is
scored by `collected_count`, and the builder **keeps the best** — the certified manifest is the
highest-count clean-and-stable-and-pristine collection seen across all agent attempts (never a
smaller one, mirroring the react arm's keep-best-on-`executed`). A candidate that collects 42
cleanly beats one that collects 40 cleanly, because the extra 2 are real tests a fuller environment
un-hid. Concretely, `build_one` runs **all** `attempts` agent invocations (default 3), each an
**independent** sample starting from the seed Dockerfile (fresh start, no cross-attempt carry-over),
certifies each, and selects the highest-`collected_count` accepted result via `pick_best`. The
winning attempt's Dockerfile is what the certificate and artifacts record (not merely the last
attempt executed).

**Deliberately *not* gated (per project decision), only *recorded* (§7):** author skips
(`@pytest.mark.skip`, `importorskip`) and deselections. Author-level skips are part of the suite's
real shape; because protected config is pristine (§6), any deselection is author-intent, not agent
gaming. Recording-not-gating keeps the completeness signal visible without rejecting legitimate
suites. This is the one conscious deviation from the original spec's `collection_skips == 0` /
`unexpected_deselected == 0` clauses — subsumed by "protected files pristine + harness owns the
collect command (no `-k/-m/--deselect`)".

`accept` is pure: fabricated `CollectionResult`s exercise every clause with no Docker and no LLM
(§10).

## 6. Protected files & anti-gaming

The single hard anti-gaming mechanism (cheap, non-rigid): **restore pristine + hash-verify.**

**PROTECTED set = the entire pristine tree — every file tracked at the pinned SHA — except the
`Dockerfile`.** This is deliberately broader than the original spec's "source, tests, `conftest.py`,
pytest config" (a subset, called out below): the whole tree is protected so there is no
chicken-and-egg (no need to know which files are tests before collecting) and no un-covered edit
surface. Notably included, because they are the gaming vectors: every `conftest.py`, and pytest
config (`pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`).

**AGENT-EDITABLE = the `Dockerfile`, and nothing else.** No sidecar files: all configuration is
inlined into the Dockerfile (`RUN pip install …`, `RUN apt-get install …`, heredocs for any
constraints file). The agent never edits `pyproject.toml`, so protecting the config costs it
nothing and removes the `addopts` / `--ignore` / `collect_ignore` gaming vectors by construction.

Two enforcement layers:
1. **`restore_pristine`** — `git checkout -- .` (all tracked files) + `git clean -fd -e Dockerfile`
   (remove every untracked file except the Dockerfile) before each verifying build. Wipes any
   agent edit to source/tests/config and any cheat sidecar; leaves only the Dockerfile.
2. **In-image hash check** — hash the protected tree *inside the built image* vs the pristine host
   hashes. Catches build-time mutation the restore can't see (`RUN sed -i tests/…`, `RUN rm
   tests/…`). The harness hashes files at their repo-relative paths under the build's source root
   (starter Dockerfile fixes `WORKDIR /src`, `COPY . /src`); if it cannot locate the pristine tree
   in the image (e.g. the agent relocated the source), integrity is **unverifiable → reject**. A
   byte mismatch fails clause 4.

The certificate anchors integrity on `source_tree_sha256` (a sorted-file hash over the whole
protected tree) and additionally records individual hashes for the sensitive subset (conftest,
config) as human-readable evidence.

Together: every collection-fixing change must live in the environment, not the tree — the agent
cannot win by touching what it measures.

**Injection guard (third enforcement layer).** The hash check above covers *tracked* files
(modified → mismatch; deleted → missing key → reject). It does not, by itself, catch a *new
untracked* file the Dockerfile writes into the source tree after `COPY . /src` — an injected
`conftest.py` with `collect_ignore` (silently suppresses a would-be collection error), a
`.pth`/`sitecustomize.py` (import-time hooks), or an injected `test_*.py` (inflates the manifest
with fake node-ids). So certification also scans the built image (`find <src_root>`, `.git` pruned)
and **rejects if any untracked collection-affecting file** — the pytest config/hook names
(`conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`, `sitecustomize.py`,
`usercustomize.py`), any `*.pth`, or a default-named test module (`test_*.py`/`*_test.py`) — is
present that is **not** in the pristine tracked set. This **fails closed**: if the scan itself
cannot run (missing `findutils`, permission error, or an adversarial removal of `find`), integrity
is unverifiable → reject. Offending paths are recorded in the certificate's `completeness`. Known
residual (recorded, not yet gated): an injected test under a repo-*customized* `python_files`
pattern, and symlinked hooks (`-type f` skips them) — both require a cooperative-agent assumption
or a follow-up node-id-path cross-check; deferred pending validation against real repos.

## 7. Certificate & completeness recording

`collection-certificate.json` (pure `certificate.py`, deterministic):

```jsonc
{
  "status": "CERTIFIED" | "REJECTED",
  "repo_url": "...", "commit_sha": "<pinned>",
  "base_image": "python:3.10-slim", "base_image_digest": "sha256:...",
  "collect_command": "pytest --collect-only -q -p no:cacheprovider -p manifest_collect_plugin",
  "accepted": true, "reject_reasons": [],
  "runs": [{"exit_code": 0, "collected_count": 42}, {"exit_code": 0, "collected_count": 42}],
  "manifest_size": 42,
  "completeness": {                     // recorded, NOT gated (§5)
    "collected_count": 42,
    "skipped_modules": ["tests/test_optional.py"], "n_skipped_modules": 1,
    "deselected": [], "n_deselected": 0
  },
  "hashes": {
    "source_tree_sha256": "...",        // pristine source tree, Dockerfile excluded
    "protected_files": {"tests/test_x.py": "sha256:...", "conftest.py": "sha256:..."},
    "dockerfile_sha256": "...",
    "image_id": "sha256:...", "image_digest": "sha256:...",
    "collect_command_sha256": "...",
    "manifest_sha256": "..."            // hash of collected-nodeids.json
  },
  "agent": {"runner": "claude code", "model": "opus", "transcript": "agent-transcript.jsonl"},
  "tool_version": "manifest_builder/0.1"
}
```

The `completeness` block is the residual-signal channel. Because the objective is **maximum**
collection (§5), the agent actively tries to eliminate `importorskip`-hiding by installing the
missing dependency so the hidden module collects. `skipped_modules` therefore records what remains
import-skipped **after the agent's best effort** — genuinely-optional or unresolvable deps — and a
larger residual means a less-maximal (lower-`collected_count`) manifest. It is a quality signal on
how complete the golden set is, not a reject reason.

## 8. Artifacts

Written under `artifacts/<repo>/<sha>/`:
- `Dockerfile` — the final certified environment.
- `collected-nodeids.json` — the manifest (sorted node-ID list). **The deliverable.**
- `collection-certificate.json` — §7.
- `build.log` — docker build output.
- `collect-run1.json`, `collect-run2.json` — raw plugin output for both runs.
- `agent-transcript.jsonl` — the external agent's session log.

## 9. AgentRunner seam & Claude Code binding

```python
class AgentRunner(Protocol):
    def run(self, *, cwd: str, prompt: str, autonomous: bool) -> AgentResult: ...
    # AgentResult = (transcript_path, claimed_done: bool, raw_stdout)
```

Default binding: **`ClaudeRunner`** — shells out to Claude Code headlessly:
`claude -p "<prompt>" --dangerously-skip-permissions --model opus --output-format stream-json
--verbose`, run with the subprocess CWD set to the workspace (Claude Code has no `--cwd` flag, so
the agent edits the Dockerfile and runs `./verify` in place). `-p` = single-prompt headless;
`--dangerously-skip-permissions` = fully autonomous (bypasses all permission prompts, may run
docker/pip); `stream-json` + `--verbose` = JSONL transcript. Model/argv overridable via constructor
or `$MANIFEST_AGENT_CMD`. Swappable for `codex` / `grok` unchanged. The VM already has Claude Code
credentials, so no extra auth setup.

**Task prompt (given to the agent)** — intention stated correctly so anti-gaming rests on
description + the gate, not rigid rules:
> Configure the environment by editing **only the `Dockerfile`** so that running `./verify`
> exits 0. `verify` builds your Dockerfile and runs `pytest --collect-only`. Goal: make collection
> succeed AND **maximize the number of collected tests** — install every optional and test-time
> dependency so that no module is skipped at import (`importorskip`). Do **not** edit tests,
> `conftest.py`, or pytest configuration; those are restored before verification and changes will
> be rejected.

The harness treats `claimed_done` as advisory — §4 re-certifies independently.

## 10. Testing strategy

**The five required cases all target the pure `gate`/`certificate` with fabricated
`CollectionResult`s — no Docker, no LLM, Mac-native:**

1. **partial collection, node IDs but nonzero exit** → `accept(exit=2, collected=[...])` rejects
   on clause 1 despite a non-empty list.
2. **protected-file modification** → `protected_ok=False` rejects on clause 4; separately,
   `restore_pristine` reverts a dirtied test file and `hash` detects an in-image `sed`.
3. **unstable node-IDs across runs** → `set(r1) != set(r2)` rejects on clause 3.
4. **deselected tests** → plugin captures them; certificate records + attributes them (author
   config is pristine); not a reject reason.
5. **importorskip hides a module at exit 0** → `accept` returns CERTIFIED (per §5), and the
   certificate's `completeness.skipped_modules` records the hidden module with a reduced
   `collected_count`. Test asserts **recorded & visible**, not rejected.

**Unit:** protected-set computation; `restore_pristine`; host vs in-image hashing; certificate
determinism + hash fields; `CollectionResult` parsing from plugin JSON.

**Live smoke (VM, x86_64):** two ground-truth repos we already have digest-pinned — `iniconfig`
(known clean-collect 42) and `tomli` (16). Run the full prepare→agent→certify pipeline starting
from `python-slim` and assert the certified manifest equals the known pristine collection. The
swesmith reference images additionally validate the `collect`/plugin path against a known answer.

## 11. Self-contained; what's referenced vs owned

This module is deliberately standalone — **no imports from `bench/`, `src/react_repair/`, or the
run harness.** Everything it needs, it owns, so it can be read, tested, and moved on its own.

**Owned (written fresh in `src/manifest_builder/`):**
- `collect_plugin.py` — its own pytest collection plugin (structured node-IDs + collect-time
  skipped/deselected via hooks). Modeled on the shape of `scripts/swesmith_audit_plugin.py` but
  collection-only and owned here (no cross-module dependency).
- `collect.py` — its own docker adapter (thin `docker` CLI subprocess: build / run / exec / cp /
  rm / image-digest), using the hardened flags (`--network none --cap-drop ALL`, …) as the pattern.
- `workspace.py`, `protected.py`, `gate.py`, `certificate.py`, `runner.py`, `__main__.py` (§13).
- Seed base image = a simple `python:{minor}-slim` (minor from `requires-python` if trivially
  present, else a default). The agent adjusts the base as needed, so no dependency on the shared
  `ImageSelector` / `choose_base_image`.

**Referenced only as pattern (copied idioms, not imported):** the docker-CLI subprocess shape from
`repo2run_repair_port.py`; the hardened run flags + `docker cp` + registry-digest resolution from
`scripts/audit_swesmith_images.py`.

**NOT built:** a ReAct loop / planner / actions / history (the external agent supplies these).

## 12. Non-goals & honest scope

- **Not an absolute test count.** The manifest is a reproducible *lower bound on collectible
  tests* under one certified environment — a silver standard, not proof of every test that exists.
- **Not a pass/fail oracle.** `--collect-only` never executes test bodies; the manifest bounds the
  denominator, it does not measure passing.
- **Not a whole-suite guarantee.** `importorskip`-hidden modules are recorded, not resolved; a
  more-complete env yields a larger manifest, which the completeness block makes comparable.
- **Python/pytest only** in this slice (matches the ESSR corpus).

## 13. Package layout

```
src/manifest_builder/
  __init__.py         # module docstring
  __main__.py         # argparse entry: python -m src.manifest_builder --repo-url … --sha …
  workspace.py        # prepare_workspace, PROTECTED computation, restore_pristine, seed Dockerfile
  collect_plugin.py   # OWN pytest collection plugin (structured node-IDs + skipped/deselected)
  collect.py          # OWN docker adapter (build/run/exec/cp/rm/digest) + locked-down collect ×2
  protected.py        # host + in-image hashing, comparison
  gate.py             # accept(r1, r2, protected_ok) -> Verdict + keep-best-by-count (pure heart)
  certificate.py      # build_certificate, collected-nodeids.json  (pure)
  runner.py           # AgentRunner protocol + ClaudeRunner adapter (swappable)
tests/manifest_builder/
  test_gate.py            # the 5 required cases + boundaries + keep-best
  test_protected.py       # restore + hashing
  test_certificate.py     # determinism + hash fields
  test_collect_parse.py   # plugin-JSON → CollectionResult
```

Entry: `python -m src.manifest_builder --repo-url <url> --sha <commit> [--runner claude] [--attempts N]`.
Batch over the pinned corpus: `--corpus datasets/rat_python_hard_subset.pinned.json` (reads
`clone_url` + `commit` per repo), writing one artifacts dir per repo.

## 14. Global constraints

- **Host owns the gate** — the agent never self-certifies; `verify == certify`.
- **Dockerfile is the only durable state the agent controls**; protected files are restored +
  hashed every cycle.
- **Reproducible** — fresh `docker build` + clean-room collect for certification; digest-pinned
  base; every input hashed into the certificate.
- **Additive** — a new `src/manifest_builder/` package; touches no existing arm.

## 15. Open items

- ~~Exact agent binary/flags for the runner~~ **DONE (2026-07-11)** — agent switched from grok to
  **Claude Code** (VM already has Claude Code credentials): `claude -p "<prompt>"
  --dangerously-skip-permissions --model opus --output-format stream-json --verbose`, run with the
  subprocess CWD set to the workspace. Model/argv overridable via `$MANIFEST_AGENT_CMD` /
  `ClaudeRunner(model=...)`.
- Whether to hand the agent a live container/venv scratch space explicitly, or let it drive Docker
  itself for its inner loop (both work; `verify` is authoritative either way).
- Multi-arch: pilots are amd64; ARM parity is out of scope for this slice.

**Corpus SHA-pinning — DONE (2026-07-11).** The python-50 corpus
(`datasets/rat_python_hard_subset.json`) shipped with `default_branch` only (no commit). Frozen to
full 40-char SHAs via `git ls-remote` (default_branch → HEAD fallback) into
`datasets/rat_python_hard_subset.pinned.json` (`commit` per repo + `_pinned_at` stamp; 50/50
resolved, 1 HEAD-fallback, 0 errors). This is the builder's `--sha` corpus input. `medlarge15` was
already SHA-pinned; the `iniconfig`/`tomli` pilots are pinned by SHA **and** image digest.
