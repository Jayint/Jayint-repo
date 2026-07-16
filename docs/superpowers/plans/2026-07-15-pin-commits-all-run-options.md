# Integrate the 50 pinned commits into ALL benchmark run options

**Date:** 2026-07-15 · **Branch:** `john-v3-multi-lang`
**Goal:** every run option builds and tests each repo at a *fixed commit* (the M3-baseline SHA),
instead of default-branch HEAD, so a new run is reproducible against the M3 baseline and against itself.
**Approach (user-chosen):** put `commit` on each dataset entry; each model reads
`pin_sha = instance.get("commit")` and threads it to its clone(s).

Pinned dataset already exists: `datasets/rat_python50_pinned_m3nothink.json`
(`{"_provenance":…, "repos":[{full_name, clone_url, default_branch, size, commit}]}`) — a valid
`--repos-json` as-is (`load_repos` returns `d["repos"]`).

---

## The one architectural constraint

`predict(full_name: str)` is the **universal contract** across all three models
(`dockeragent_model.py:34`, `rat_model.py:46`, `repo2run_model.py:76`). Only the repo *name*
crosses that boundary — the runner calls `model.predict(full_name)` at `run_rat_benchmark.py:215`
and, in scheduler mode, spawns children with `--only <full_name>` (`_child_cmd`). So each model
re-synthesizes its own clone from `full_name` alone, at **independent clone sites**, all cloning HEAD.

The commit therefore has to reach each model through a channel that (a) is keyed by `full_name` and
(b) survives the `--only full_name` child hop.

**That channel already exists:** `_child_cmd` forwards `--repos-json` to every worker
(`run_rat_benchmark.py:406`), and the dataset rows carry `commit`. So the commit and the repo list are
the *same file* — no second pin-map, no divergence risk.

### Design decision

- **Single source of truth:** the commit lives in the run's `--repos-json`. Thread `repos_json` into
  `_make_model` → each model stores it → a shared resolver `pins.commit_for(repos_json, full_name)`.
- **Rejected — separate `RAT_PINS` env var:** a second file that can disagree with `--repos-json`;
  and env-var provenance side-channels have burned us before.
- **Rejected — change `predict(full_name)` → `predict(full_name, commit)`:** breaks the
  `BaseEvalModel` contract, all three models, and their tests, for no gain over the resolver.
- **Backward compatible:** a dataset row with no `commit` → resolver returns `None` → current
  `--depth=1` HEAD behavior. Existing datasets are unaffected; this is a strict superset.

---

## Clone-site map (what actually has to change)

Every run option has an **agent clone** (what the graph/agent analyzes) and an **eval clone**
(what the tests run against). **Both must pin to the same commit** or construction and measurement
drift apart.

| Run option | agent clone | eval clone | state today |
|---|---|---|---|
| `dockeragent --arm v3` (john-planner-v3) | `_clone` @115 `--depth=1` | `_render_dockerfile` @221 `--depth=1` | **no pin** |
| `dockeragent --arm react` (john-react) | `_clone` @130 (pin_sha) | `_render_dockerfile` @351 (pin_sha) | pin exists, **gated behind `V3_REPAIR_ABLATION`** |
| `dockeragent --arm arm0/v1` (rat-bench-integration adapter) | `DockerAgent(base_commit=…)` | Dockerfile `git checkout {base_commit}` @487 | machinery exists (`instance.get("base_commit")` @697), **unfed** |
| `rat` | clone inside `download_repo()` (`rat_model.py:72`) | recipe/Env Dockerfile clone | **no pin** |
| `repo2run` | `git clone --depth=1` (`repo2run_model.py:104`) | repo2run harness bakes its own clone | **no pin** |
| `bench.unified_bench` (MEASURE) | — (harvests produced Dockerfile, `harvest.py:17`) | inherits produced Dockerfile | **inherits** produce-side pin |

Key subtlety, already handled correctly in react: **you cannot `git checkout` an arbitrary SHA in a
`--depth=1` clone.** Pinning must switch to a **full** clone (`_clone` @139-142, `_render_dockerfile`
@353 drop `--depth`). That is slower/heavier per repo but *also fixes a latent bug* — VCS-versioned
installs (setuptools_scm / hatch-vcs) need reachable tags, absent from a shallow clone.

---

## Shared infrastructure (Phase 1)

1. **Resolver** — new `eval/models/_pins.py` in the RAT harness (all three models import from there):
   ```python
   import json
   _cache = {}
   def commit_for(repos_json: str | None, full_name: str) -> str | None:
       """Pinned commit for full_name from the run's --repos-json, or None (→ HEAD)."""
       if not repos_json:
           return None
       if repos_json not in _cache:
           data = json.load(open(repos_json))
           rows = (data.get("repos", data) if isinstance(data, dict) else data) or []
           _cache[repos_json] = {(r.get("full_name") or "").lower(): r.get("commit")
                                 for r in rows if isinstance(r, dict)}
       return _cache[repos_json].get(full_name.lower())
   ```
2. **Thread `repos_json` into the model factory** (`run_rat_benchmark.py:105`):
   `_make_model(model_name, root_path, timeout, llm, num_turn, repos_json)` → each model stores
   `self.repos_json = repos_json`. `repos_json` is already in scope at every call site and already
   forwarded to children, so no new plumbing.
3. **Dataset schema:** `commit` field (already present in the pinned file). Document it in the
   dataset README as optional.

---

## Per-run-option changes (phased)

**Phase 2 — v3 graph construction (the immediate need for the construction-only run):**
- `dockeragent_model.py:50-52` — inject the commit into the synthesized instance dict:
  `{"instance_id":…, "repo_url":…, "language":"python", "commit": _pins.commit_for(self.repos_json, full_name)}`.
- **john-planner-v3 adapter** — port react's pin handling: `process_single_instance` reads
  `pin_sha = instance.get("commit")`; thread into `_clone` (full clone + `checkout --detach`) and
  `_render_dockerfile` (full clone + checkout). This is a near-verbatim lift from john-react
  `multi_docker_eval_adapter.py` @130-145, @351-357.

**Phase 3 — react parity:**
- Change react's pin source from `_seed_head_sha` (ablation-only) to
  `instance.get("commit") or self._seed_head_sha(full_name)` so the normal path pins too, and the
  repair-ablation path still works. One line at `multi_docker_eval_adapter.py:104`.

**Phase 4 — baselines (needed only when re-baselining rat/repo2run on the pins):**
- `repo2run_model.py:104` — full clone + `git -C {repo_path} checkout {commit}` when commit present;
  repo2run's generated Dockerfile clone gets the same checkout.
- `rat_model.py` — pass commit into `download_repo(...)` (checkout after its clone) and into the
  recipe/Env Dockerfile clone. (Requires reading `download_repo`'s clone site in the RAT harness.)
- `arm0/v1` old adapter — feed `instance["base_commit"] = commit`; the `git checkout {base_commit}`
  path (@487, @697) already exists, so this is a one-line feed in the model.

**Phase 5 — measure guard + standalone (optional):**
- `bench` MEASURE inherits the pin automatically (it rebuilds the produced Dockerfile). Optionally
  **re-enable `gold.py`'s `sha_misaligned` check** as a *guard* — it compares each row's captured
  `_meta.json` `head_sha` against the pinned sha and flags drift instead of silently scoring. It is
  DORMANT today (`unified_bench.py:81` forces `gold=None`).
- `scripts/run_v3_e2e.py --construction-only` — add `--commit <sha>` so a single-repo standalone
  debug run can pin too.

---

## Correctness invariants to hold

- **Both clone sites, same commit.** A repo whose agent-clone pins but eval-clone doesn't (or vice
  versa) is worse than no pin — construction analyzes tree X, tests run tree Y. Verify per repo.
- **`head_sha` must equal the pin.** Every produce path already captures HEAD post-hoc
  (`dockeragent_model.py:95` rev-parse; `_meta.json`). After pinning, `_meta.json.head_sha` MUST
  equal the dataset `commit` — that's the cheapest verification and what the gold guard checks.
- **Full clone when pinned.** Never emit `--depth=1` together with a checkout.
- **None → HEAD.** Absent commit must not error; it falls back to today's behavior.

---

## Deployment loci (the real risk — three places, not one)

1. **RAT harness** `/opt/runanything/src/eval/models/` — the three models + new `_pins.py`. This dir
   is **VM-vendored, outside git**; `deploy.sh` patches it via a dedicated `RAT_ROOT_BOX` block, not
   rsync. Model edits are applied on the VM (or through that block).
2. **Agent checkout** `/opt/agents/john-planner-v3` (and `/opt/agents/john-react`) — the adapter +
   `run_rat_benchmark.py`. These are **git checkouts on the VM** (`git fetch` + `checkout`; the VM
   can't push — relay-push from local).
3. **rsync target** `/opt/rat-bench-integration` — the working-tree mirror `deploy.sh` writes. The
   M3 run did **not** run from here (it ran commit `c3dcaed` from an `/opt/agents/*` checkout).

**Before implementing, confirm which checkout the next run will execute from**, and make the adapter
+ runner edits in *that* checkout. Mismatch here is the most likely way this silently no-ops.

---

## Validation

1. **2-repo smoke, construction-only, pinned:** run v3 on e.g. `pallets/itsdangerous` +
   `fastapi/typer` from the pinned dataset. Assert per repo: `_meta.json.head_sha == dataset.commit`,
   and the produced `eval_build/Dockerfile` contains `git clone` **without** `--depth=1` **and** a
   `checkout <sha>`.
2. **Negative control:** same two repos with an *unpinned* dataset → resolver returns None → Dockerfile
   is `--depth=1` HEAD, `head_sha` is whatever HEAD is. Proves backward-compat and that the pin path
   is actually firing (not incidental).
3. **Then the full 50** construction-only, and confirm 50/50 `head_sha == commit`. Any mismatch is a
   drift bug, surfaced by the re-enabled gold guard rather than hidden.

---

## Minimal path to the construction-only run you want now

Phases 1 + 2 only (shared resolver + `_make_model` threading + `dockeragent_model` inject +
john-planner-v3 adapter pin). That makes `dockeragent --arm v3` reproducible against the M3 pins.
Phases 3–5 are for the other arms and the optional guard, and can follow once the v3 numbers are in.
