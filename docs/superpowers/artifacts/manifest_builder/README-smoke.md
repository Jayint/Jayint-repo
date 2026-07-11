# Collection Gold-Manifest Builder — VM Smoke (Task 9)

**Date:** 2026-07-12 · **VM:** `root@167.233.64.96` ("Research-1", x86_64, Docker 29.5.3) · **Status:** ✅ PASSED

End-to-end validation of `src/manifest_builder/` against two digest-pinned ground-truth repos
(`iniconfig` → 42, `tomli` → 16, from the swesmith gold-manifest investigation). Run in two phases:
**B** (no-agent harness validation) then **A** (live Claude Code agent).

## Result

| Repo | Commit | Phase | Status | manifest_size | Ground truth | Match |
|---|---|---|---|---|---|---|
| `hukkin/tomli` | `443a0c1b` | B (no-agent) | CERTIFIED | 16 | 16 | ✅ |
| `pytest-dev/iniconfig` | `16793ead` | A (live agent) | CERTIFIED | 42 | 42 | ✅ |
| `hukkin/tomli` | `443a0c1b` | A (live agent) | CERTIFIED | 16 | 16 | ✅ |

Both certificates are legitimate clean/stable/pristine: both `--collect-only` runs exit 0 with equal
counts, no skips/deselects, **zero injected files**, protected tree byte-pristine.

## VM setup (one-time)

- Module synced to isolated `/opt/manifest_builder` via subtree rsync (macOS stock `/usr/bin/rsync`,
  no `--delete`; touches no existing box data). Corpus at `/opt/manifest_builder/datasets/`.
- **Pure suite: 43/43 passes on the VM's Python 3.10.12** (`cd /opt/manifest_builder &&
  python3 -m pytest tests/manifest_builder`) — module is 3.10-compatible.
- Node v20.18.1 + **Claude Code 2.1.197** installed isolated under `/opt/node` (no apt changes).
- **Agent invocation env** (winning config): `PATH=/opt/node/bin:$PATH`,
  `CLAUDE_CODE_OAUTH_TOKEN` from `/opt/harness/.env`, **`IS_SANDBOX=1`** (lets
  `--dangerously-skip-permissions` run as root), `--model opus`. Wrapper: `run_agent.sh`.
  - Root + `--dangerously-skip-permissions` is blocked by Claude Code unless `IS_SANDBOX=1`.
  - The `/opt/harness/.env` OAuth token had to be **refreshed** (`claude setup-token`) — the prior
    one returned `401 Invalid bearer token`.

## What the agent produced (provisioning reasoning)

**iniconfig** — the hard case. It is itself a pytest dependency, so a naive `pip install pytest`
pulls a PyPI `iniconfig` that clobbers the checked-out source (`ImportError: _ParsedLine`), AND its
`hatchling` build fails metadata generation (no reachable version). The agent solved both:
```dockerfile
FROM python:3.11-slim
WORKDIR /src
COPY . /src
ENV SETUPTOOLS_SCM_PRETEND_VERSION=1.0.0   # fixes the version → build-metadata failure
RUN pip install --no-cache-dir pytest        # pytest first (pulls PyPI iniconfig as a dep)
RUN pip install --no-cache-dir -e .          # then editable-install LOCAL iniconfig → wins
```

**tomli** — trivial:
```dockerfile
FROM python:3.11-slim
WORKDIR /src
COPY . /src
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir pytest
```

## Key findings

- **The gate correctly rejects broken environments** — the no-agent B driver's naive `pip install
  pytest` produced an exit-2 collection (and later a build failure) for iniconfig; the harness
  REJECTED both (never a false CERTIFY).
- **iniconfig is an ideal agent test, a poor no-agent test** — its self-dependency + `hatchling`
  build is exactly the provisioning reasoning the agent exists for; the no-agent driver couldn't
  crack it, the live agent did in one attempt.
- **Injection guard is inert on clean repos** — `n_injected: 0` for both (no false positives).

## How to reproduce

```bash
# sync (from Mac repo root)
/usr/bin/rsync -a src/__init__.py src/manifest_builder root@167.233.64.96:/opt/manifest_builder/src/
/usr/bin/rsync -a tests/manifest_builder root@167.233.64.96:/opt/manifest_builder/tests/
# no-agent (B):    python3 smoke_b.py            (FakeRunner scripts a working Dockerfile)
# live agent (A):  bash run_agent.sh <iniconfig|tomli> [attempts]   (real ClaudeRunner)
```
Scripts (`smoke_b.py`, `smoke_a.py`, `run_agent.sh`) live in `/opt/manifest_builder/` on the VM.

## Deferred (not blocking; before the broad ~50-repo corpus run)

- Node-id-path cross-check (custom `python_files`); symlinked-hook; `.dockerignore`; `-e /verify`
  anchor. See `.superpowers/sdd/progress-manifest-builder.md`.
- Corpus run: `python3 -m src.manifest_builder corpus --corpus datasets/rat_python_hard_subset.pinned.json`
  (attempts=3, per-repo isolation already in place).
