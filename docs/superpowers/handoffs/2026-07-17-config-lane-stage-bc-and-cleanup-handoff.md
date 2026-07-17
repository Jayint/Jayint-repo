# Handoff — Config Lane Stage B + C, and the codebase cleanup it unlocks

**Written:** 2026-07-17. **Purpose:** carry the two-lane config-lane work into a fresh session for Stage B/C design → planning, plus the architecture cleanup the config lane enables. This doc is **self-contained** — the fable adversarial review and the three code-map surveys are inlined here because they lived in a per-session scratchpad that a new session cannot read. Verify every anchor against the code before acting; treat this as orientation, not ground truth.

---

## 0. TL;DR — where we are

We are building a **two-lane dependency-graph construction** for Python collection-scope env setup (goal = clean `pytest --collect-only`). Every scanned import routes to one of two providers, and the provider's node type IS the cure:

- **Install lane** (`import → package`): external → PyPI → pip/uv. *Largely built (the user's in-flight `import→dist` pipeline).*
- **Config lane** (`import → module`): first-party → the repo's own top-level module → editable-install + rootdir. *This is the new work; designed + hardened, not yet built.*

**Rollout = 3 stages, 2 go/no-go gates:**
- **Stage A** (planned, `docs/superpowers/plans/2026-07-17-config-lane-stage-a.md`): prove the bet — **Gate A: does editable-install + rootdir clear the collect cliff on the pilots?** — plus two trivial foundations (`FILE→MODULE` rename, `TestEnvPlan` extension). Gate A is measurable with EXISTING infra; the heavy cure runner is deferred to Stage B.
- **Stage B** (this handoff — to design+plan): build the machinery in **shadow** — the in-construction cure runner (+ the repo-mount primitive the container lacks), the pure classifier, the arbitration, the spine, the lane-aware fixpoint. **Gate B: partition sanity in shadow.**
- **Stage C** (this handoff — to design+plan): **the flip** (route-not-drop, sweep-gated point of no return) + **retire** the old drop path and Test-hub wiring.

**The measured problem this targets:** a Jul-7 50-repo run had build-succeeds 34/50 but collection-only 14/50; the dominant post-build failure is the project's OWN package not importing (`ModuleNotFoundError`: azure 453×, frappe 290×). That's a config failure, not a missing package — and the current graph structurally cannot express it (its vocabulary is `goal → import → pkg`).

---

## 1. Read first (in order)

1. `docs/superpowers/specs/2026-07-16-two-lane-causal-graph-and-import-classification-design.md` — the two-lane model + the "Blockers" section.
2. `docs/superpowers/specs/2026-07-17-two-lane-model-integration-refactor.md` — how the model slots into today's `build_dep_graph` pipeline, stage by stage.
3. `docs/superpowers/specs/2026-07-17-config-lane-module-spine-and-collision-arbitration-design.md` — **the hardened config-lane design** (the primary WHAT for Stage B/C). Every fable finding is tagged inline `[resolves review §N]`.
4. `docs/superpowers/plans/2026-07-17-config-lane-stage-a.md` — the Stage A plan (Gate A + foundations).
5. `docs/superpowers/plans/2026-07-17-two-lane-integration-foundations.md` — the 3 foundation tasks already LANDED.
6. `docs/superpowers/plans/2026-07-17-import-dist-pipeline.md` — the install lane (the user's in-flight parallel work; the OTHER lane).
7. Sections 4–8 of THIS doc (the code maps + the fable review + the Stage B/C briefs).

---

## 2. Status — landed / designed / planned / in-flight

| Item | State | Where |
|---|---|---|
| certify-by-import on the Project node | **LANDED** | commit `ba6eae21`; `build.py:_project_import_target`, `_add_project_node` |
| Drop the `tier` axis | **LANDED** | commits `d6d1fa43` + `ec771ceb` |
| `NodeType.FILE` scaffold (+ EDGE_RULES) | **LANDED** (inert; rename→MODULE pending Stage A) | commit `cc0ddf96` |
| Two-lane model spec | designed | `2026-07-16-...-design.md` |
| Integration-into-pipeline spec | designed | `2026-07-17-two-lane-model-integration-refactor.md` |
| Config-lane hardened spec | designed (fable-reviewed) | `2026-07-17-config-lane-module-spine-...md`, commit `20f3eae6` |
| Stage A plan | planned | `2026-07-17-config-lane-stage-a.md`, commit `1d63833b` |
| Install lane (`import→dist`) | **in-flight (user, parallel)** | commits `2a2e61bc`,`b1f108db`,`dfa18c38`,`2a58a9f3`,`e5c564d2`,… |
| Causal-model artifact (rev 2, module node) | published | claude.ai/code/artifact/95a2a7d5-9afb-4111-91e8-78d4da6ffece |
| Stage B, Stage C | **to design + plan** (this handoff) | — |

**This is a SHARED branch (`john-v3-multi-lang`).** The user commits in parallel; HEAD moved several times mid-session. Rules: APPEND-only (never rebase); every commit **pathspec-scoped** (`git add <exact paths> && git commit -m "..." -- <paths>` — note `-m` MUST come before `--`, or git treats the message as a pathspec and errors); no `Co-Authored-By` trailer (attribution disabled globally).

---

## 3. Locked decisions — do NOT re-open

The model and the config-lane design are settled through ~15 rounds of dialogue + an adversarial review. Do not relitigate:

- **Node model:** internal provider = a **top-level local-`Module` node** (identity = top-level import name like `app`, evidence = a tuple of `(sys_path_root, path)` pairs). NOT per-file nodes. `import app.agent` rolls up to `module(app)` (the scanner already aggregates to top-level, `import_graph.py:113`).
- **Spine:** `project → module → import → {module | package}`. The project *contains* its top-level modules; each module's imports (attributed via `source_files`) resolve internal (→module) or external (→package). The local module is the **join** between the two lanes (config-cured itself; source of install-cured externals). Declared roots unchanged (imports never generate roots); a declared-but-unimported dep keeps a direct `project → package`; **manifest is authoritative for install scoping, the spine is descriptive.**
- **Certificate = hybrid, from ONE canonical collection invocation** (`TestEnvPlan`: cwd/PYTHONPATH/importmode/rootdir/env): the overall gate is `pytest --collect-only`; the per-name arbitration is `python -c "import X"` under the *same* plan. **Exception-aware:** `ModuleNotFoundError` on the probed name ⇒ not-local (config verdict); any other exception ⇒ present-but-broken ⇒ never a fallthrough.
- **Classifier ladder:** `declared → target-stdlib (of the resolved interpreter) → repo_modules (sys.path-accurate, PEP 420 namespace-aware) → external`. Pure static pass; partitions into clear-internal / clear-external / collision-zone.
- **Collision zone** = `stem_collisions` (both a repo module AND a real PyPI dist). **Grounding CANNOT protect it** (`pip download items` confirms PyPI `items` because that wheel genuinely provides `import items`). Safety rests on **config-first ordering + a cure-verified certificate**, never grounding, never a static guess.
- **Sequencing:** classify → fixpoint installs clear-external (collisions deferred) → config-cure runs IN the scratch container → arbitrate (only if cure succeeded) → fallthrough re-enters the install lane → Phase-B native/relink re-run → certify.
- **False-green policy:** collision fallthrough installs **only if the cure succeeded**, and then carries a flag with a **named owner** (run manifest + an eval "certified-with-provisional" bucket). Cure failure → collisions stay unresolved (honest RED), never install. NOT a hard gate.
- **Module boundary:** `classify.py` = pure classifier (ladder + Module emission + deferred set as data, sole `repo_modules` consumer); `arbitrate.py` (net-new) = the container-bound phase (probes post-cure, mints fallthrough roots). Tripwire rewritten.
- **`layer` kept, `tier` dropped** (done). `NodeType` stays a superset (construction stops emitting demoted types).
- **Regression-sweep is THE gate.** Prior graph work destroyed 3 of 33 passing repos; sweep the repos that PASS before any scored run (memory `regression-sweep-is-the-gate`).

---

## 4. Code map A — the pipeline & where the config lane slots (from survey)

**The orchestrator** (`src/python_deps/depgraph/build.py`):
- `build_dep_graph` (~`:1059-1167`) → `provider.package_obligations` (`:1142-1153`) → `provider.native_obligations` (`:1157`) → `reconcile_apt_names` (`:1162`) → `certify_all` (`:1165`).
- `_python_package_obligations` (`:738-1015`): scan (`:815`) → target-env (`:830`) → declared roots (`:855`) → **Phase-A fixpoint** (`:948`) → aux-once: `_add_project_node` (`:976`), subprocess tools (`:977`), seed priors (`:978,986,996`), `project_native_obligations` (`:1008`) → returns `(graph, roots, target_env, exclude_newer)` at `:1015`.
- `_phase_a_fixpoint` (`:346`, the `while True` loop `:411-492`): each round resolve → reconcile → **`install_closure` (`:427`, PACKAGE nodes only)** → coverage → `missing` (`:431-438`) → `generate_candidates`→`choose_provider` → add root → re-resolve.
- Phase B = `_python_native_obligations` (`:1018-1056`): relink (`:1039`) → ldd_probe (`:1045`) → import_probe (`:1049`).

**The config-lane slot** (spec Stage Xa/Xb/Xc, "after clear-external install, before Phase-B"): the **tail of `_python_package_obligations`, after `project_native_obligations` (`:1008`), before the return (`:1015`)** — where `roots`/`target_env`/`exclude_newer`/`record_provider`/`host_executor`/`container_executor`/`evidence`/`uv_sources` are all in local scope. (The `build_dep_graph` seam between `:1153` and `:1157` is topologically cleaner but DISCARDS `roots`/`target_env`, so the Stage-Xc fallthrough re-resolve would have to re-thread them — prefer the package_obligations tail.)

**The lane-aware `missing` filter (Stage B, fable §4):** `_phase_a_fixpoint`'s `missing` (`build.py:431-438`) is over ALL non-optional IMPORT nodes by name. Once route-not-drop ships, first-party + deferred-collision imports would land in `missing` → inflate `bound = min(len(missing),5)`, burn repair rounds, and **feed first-party names to the LLM dist-guesser (violates the invariant).** Make `missing` exclude Module-routed and deferred imports; make deferral a first-class fixpoint input (`deferred: frozenset[str]`). Fallthrough re-entry must thread `prev_pkg_ids`/`attempted` (a fresh fixpoint call leaves both `pkg==old` and `pkg==new` nodes).

---

## 5. Code map B — the cure execution surface (from survey)

**The Executor seam** (`src/python_deps/depgraph/executor.py`): `Executor.run(cmd, *, timeout=300) -> CommandResult` (`:33-35`; `.ok`==rc0). `DockerExecutor` (`:76-163`) is a **long-lived** `docker run -d ... sleep infinity` container (`:114-123`) with `docker exec` per command (`:150-163`); it **persists across the whole `build_dep_graph` call** (`advise.py:351-356` wraps the build in one `with DockerExecutor(...) as scratch:`). So install_closure, a cure runner, and a collect-gate all run in the SAME container with the SAME installed state.

**THE BLOCKING GAP for Stage B:** the scratch container **mounts no repo source** (`executor.py:103-112` mounts only uv/pip cache volumes — no `-v` repo bind, no `docker cp`). During construction the repo lives only on the host; the container only ever gets `uv pip install --system <name>==<ver>` (packages by name, `probe.py:557-563`). So **`pip install -e .` cannot run in today's scratch container** — it needs `pyproject.toml`/`setup.py` + the package tree at cwd. **The precedent to reuse:** `_MountedContainer` (`coverage.py:555-586`) does `docker run -d -v {host}:{container} ... sleep infinity` (`:568-576`), same `run()->CommandResult` contract; its docstring says "DockerExecutor ... has no mount support, which this probe needs." **Stage B's first net-new primitive: add a repo mount (or `docker cp`) to `DockerExecutor`.**

**The build-isolation fallback chain** (spec Stage Xa, fable §3), as container commands on the mounted scratch:
- Rung 1 (isolated): `cd /repo && python3 -m pip install --break-system-packages -e .`
- Rung 2 (`--no-build-isolation`, only if rung 1 fails — legacy `setup.py` importing numpy/cython at build time can't see the Phase-A closure under isolation): ensure `setuptools`/`wheel` + declared `build-system.requires` present, then `... --no-build-isolation -e .`
- Collect-gate: `cd /repo && python3 -m pytest --collect-only -q` under the `TestEnvPlan`.
- On rc0, stamp scratch-certified state (`node.with_state(State.SATISFIED, cycle=...)`, `certify.py:92`) + a `data["scratch_certified"]=True` marker. Timeout precedent `INSTALL_TIMEOUT=900` (`probe.py:63`).

**The poison reconciliation** (fable §9): `_poison_project_certificate` (`populate.py:118-153`) strips the Project's `check_command`/`version` and forces MISSING — it runs at **render time only** (sole caller `populate_setup_commands` → `build_script.py:458`), NOT in construction. It would erase the config lane's output. **Minimal fix:** gate the one call site `populate.py:224-225` on `not node.data.get("scratch_certified")`. (Its existing `{**node.data}` spread already preserves any `scratch_*` fields.) Confirmed: **no in-container editable install exists today**; the only live `-e .` is the eval replay in a mounted container (`coverage.py:604-611`).

---

## 6. Code map C — TestEnvPlan + Gate A measurement + rename (from survey)

**`TestEnvPlan` already exists** — `invocation_resolver.resolve(repo_path) -> TestEnvPlan` (`invocation_resolver.py:113`), frozen dataclass `:90-110` with 9 fields incl. `rootdir`/`pythonpath`/`import_mode`/`project_dirs`/`layout`. Pure. **Gaps (Stage A Task 3 adds cwd+env; the rest is Stage B):**
- No `cwd` field (default = rootdir; absolute materialization is the cure-runner's job).
- No `env` field — but discovery EXISTS: `config_scan.scan_authoritative_config` (`:482`, unambiguous = one distinct value across the 4 ini sources) + `authoritative_ambiguous_vars` (`:505`). Just unwired from the resolver.
- **Two divergent config readers must be reconciled (Stage B):** `invocation_resolver._pytest_config_in_dir` (path half, precedence pytest.ini>pyproject>tox>setup.cfg, searches `["."]+project_dirs`) vs `config_scan` (env half, no precedence, **repo-root only**). A feast-style `sdk/python/tox.ini setenv` is found by the resolver but missed by config_scan. For the collect-gate and the probe to never diverge, one reader must drive both halves.
- PYTHONPATH is under-sourced (only the pytest `pythonpath` ini option; not tox `setenv PYTHONPATH` nor editable-install roots). `testpaths`/`addopts` not modeled — decide if the gate inherits them.

**Gate A measurement infra (reuse — do NOT rebuild):**
- **The live collect-cliff metric:** `bench/measure.py:126-128` runs `pytest --collect-only -q /testbed`; `parse_collect` sets `collect_clean = rc in (0,5)` (`:67-76`); `bench/metrics.py:34` aggregates `EBSR = n_collect_clean/n` (**the 14/50 number**). Stricter honest gate: `src/manifest_builder/gate.py:6-21` (exit0 ×2, non-hollow, stable node-id set).
- **Fast per-repo route:** `src/eval/build_script_eval/replay.py:47-131` `run_replay_ladder(repo_dir, image, setup_script) -> LadderResult`: rung 1 `bash setup.sh` (where `-e .` runs), then `pytest --collect-only -q` (`:78`) → `.collect_ok`. The rendered `setup.sh` capstone is `-e .` (`emit.py:156`); the renderer is `scripts/run_v3_e2e.py`.
- **Gold comparison (dormant):** `bench/gold.py` (`gold_coverage:118-163`, `EBSR_improved = |collected ∩ gold|/|gold|`, SHA-aligned via `head_sha`) — currently OFF (`bench/metrics.py:4,54-55` commented; `unified_bench.py:79-82` forces `gold=None`). Re-enabling it is the vs-gold primitive. Gold is PRODUCED by `src/manifest_builder corpus --corpus <json> --out <dir>` (`__main__.py:171-225`). No gold JSON committed (memory: certified artifacts at `/opt/manifest_out_py50`).
- **Pilots:** `datasets/pilot.json` (3 repos), `datasets/rat_python50_pinned_m3nothink.json` (the 50 behind 34/50-14/50). Field-mismatch gotcha: `manifest_builder corpus` reads `commit`; `pilot.json` uses `sha`/`base_commit`.
- **Known-answer positive:** `src/eval/graph_fidelity/edge_cases/srclayout_editable/` (`editable_required: true`) — a src-layout pkg collectable only after `pip install -e .`.

**`FILE→MODULE` rename (Stage A Task 2)** — 4 sites: `schema.py:30` (`FILE="File"`→`MODULE="Module"`), `schema.py:94` + `:96` (EDGE_RULES src/dst `"File"`→`"Module"`), `tests/depgraph/test_schema_roundtrip.py:221`. Traps verified clean: `envstate/contracts/schema.py` is a DIFFERENT `NodeType` enum (no FILE); eval `NodeType`-set assertions (`coverage.py:137`, `test_coverage.py:200`, `test_oracle.py:210`) are dynamic; `patch_gate` reconstructs from value.

---

## 7. Stage B — design + planning brief

**What Stage B is:** build the config-lane machinery **behind a flag / in shadow** — so the old drop path keeps running and the sweep stays green — and MEASURE it before the flip. Natural sub-plans (each = writing-plans → subagent-driven execution + codex review + sweep, as the foundations were done):

1. **Repo-mount primitive** — add a `-v` repo mount (or `docker cp`) to `DockerExecutor` (precedent `_MountedContainer`, `coverage.py:568-576`). Prereq for the in-construction cure.
2. **The in-construction cure runner** — at the `_python_package_obligations` tail (`build.py:1008-1015`): editable-install + build-isolation fallback chain + the canonical collect-gate, stamping scratch-certified state; + the `populate.py:224-225` poison gate.
3. **The pure classifier `classify.py`** — the ladder + **PEP 420 namespace-root handling** (extend `repo_modules._module_for`'s climb with a downward namespace check seeded from declared `packages`/`find_namespace_packages`; the current climb mints a false top-level for `src/mycompany/pkga` with no `mycompany/__init__.py` — fable §6, the hole that killed the prior module-node spec) + the four relocated `scan` drops (excluded-dir-only → collision zone, not clear-external — fable §12) + Module emission + deferred set as data.
4. **`arbitrate.py` + lane-aware fixpoint** — the exception-aware probes under the `TestEnvPlan` (fable §7), gated on cure success (fable §1); the lane-aware `missing` filter + deferral + threaded fallthrough re-entry (fable §4); Phase-B re-run over fallthroughs (fable §5); relink-vs-probe precedence (fable §11).
5. **The `TestEnvPlan` completion** — reconcile the two config readers' search scope; source PYTHONPATH fully; materialize absolute cwd/env/sys.path for the subprocess.
6. **The provisional-flag owner** — propagate to run manifest/`case_study`; eval "certified-with-provisional" bucket (fable §8).

**Gate B (go/no-go):** run the whole lane in **shadow** on the corpus (scan-without-drop → classify → cure → arbitrate → spine, all measured, real construction unchanged). Measure: partition sizes, collision-zone frequency, cure-recovery, fallthroughs, flags raised, and any false-greens. If the collision zone is huge or the classifier misroutes, rethink before the flip.

**Shadow mechanism:** the new pieces compute + get measured behind a flag but do not affect the real graph, so the sweep stays green and the design's false-green risks get MEASURED (not argued) before route-not-drop makes them load-bearing.

---

## 8. Stage C — design + planning brief

**The flip (route-not-drop) — the single sweep-gated point of no return:**
- `scan.scan_to_nodes` stops dropping first-party (`scan.py:152-153,169`); the classifier stage activates in real construction; the spine (`project→module→import`) replaces the flat `Test→Import` hub; the arbitration runs during construction.
- `_add_project_node` (`build.py:244-261`) consults post-classifier routing rather than drawing a direct edge for every runtime declared dep.
- **Retire the old drop** — route-not-drop IS the retirement (you can't route AND drop); this is one atomic sweep-gated commit.
- **Tripwire rewrite** (`tests/depgraph/test_construction_boundary.py`): structural guard → "only `classify.py` imports `repo_modules`"; behavioral guard → "a collision name is not install-accepted unless (a) the cure succeeded AND (b) the canonical-plan probe shows it doesn't resolve locally" (stubbed-certificate test). Land this as a knowing gated step.

**Retire residuals (after the flip is green):** delete the now-dead flat Test-hub wiring, the old drop helpers, and the shadow flag. Sweep-gated. Deletion is genuinely last.

**Gate:** the pass-repo sweep MUST stay green at the flip (highest-risk step — rewrites construction on every repo).

---

## 9. The codebase architecture cleanup this unlocks

**The diagnosis (measured this session):** `src/python_deps/` = **20,133 lines / 85 files**; `depgraph/` is 85%. It's not one big file — it's **three lifecycles tangled in one directory** plus a subsystem per retired node-type. By concern:

| Lines | % | Concern | In `build_dep_graph`'s path? |
|---:|---:|---|---|
| 4,918 | 24% | react-arm / repair-loop / emit | No — consumes the graph |
| 4,352 | 21% | core two-lane (project·file·import·pkg + resolver) | Yes |
| 3,935 | 19% | native overlay (soname/apt/wheel/PEP725) | Yes — preserved |
| 3,071 | 15% | demoted tiers (Service/Config/Runtime/Platform/Test) | Mostly NO |
| 1,764 | 8% | declaration reading (`evidence.py` = 1,133) | Yes |

**Three root causes:** (1) a quarter is not construction at all — the react-arm/emit layer is a *consumer* of the finished `DepGraph` (via `schema.py`) that lives in `depgraph/` for historical reasons; (2) each retired node-type grew its own subsystem (Service = 8 files ~1,180 lines, and most of it isn't even in `build.py`'s import path — Service is driven from `src/envstate/classify_services_clean.py`); (3) retired code never gets deleted (overlapping linkers `resolve.py` + `resolve_lock.py` + `resolve_link.py`[retired] + `relink.py` + `naming.py`).

**The strategic insight (already agreed with the user):** do the **model change first and let it DELETE the demoted tiers** — the model change is what classifies each file as dead / repurposed / kept; you cannot clean correctly before that classification exists, and the model change IS the classification. Concrete example: `config_scan.py` looks like a demoted "Config tier" file but the config lane REPURPOSES it (rootdir/env-var engine) — a clean-first pass would wrongly delete it.

**What the config lane's landing enables to retire** (integration spec `2026-07-17-two-lane-model-integration-refactor.md`, migration steps 4-5):
- The scan-time drop of first-party imports (retired by route-not-drop, Stage C).
- The flat `Test→Import` hub wiring (superseded by the spine).
- Stop *emitting* the demoted node types (Test/Runtime/Config/Service/Platform) — the enum members STAY (superset; `envstate`/eval/react-arm reference them — measured refs: SERVICE×60, CONFIG×48, TEST×45, RUNTIME×12, PLATFORM×2), but their construction-emission code becomes deletable.
- `resolve_link` static linker (already retired from the build path; `relink` is the sole `satisfied-by`).

**The bigger, OPTIONAL future refactor (defer; needs its own design):** split `depgraph/` into **construct / emit / repair** sub-packages. The superset-`NodeType` lock already decouples the lifecycles at the DATA boundary (emit/repair read the finished `DepGraph` through `schema.py`), so this is a pure move-refactor — but it's the highest-collision change on a shared branch and must be behavior-preserving + separately attributable from the model change. Do it LAST, after the model change has already deleted ~3k lines. Do NOT bundle it with the config lane.

---

## 10. The fable adversarial review (inlined — 3 blockers, 9 majors, 5 minors)

The config-lane design was stress-tested by a `fable` reviewer; **verdict: the two-lane model is right, but the sketch was not correct for env-setup** — it would ship silently-wrong environments via the `self-install-false-green` vector. All findings are resolved in the hardened spec; here is the catalogue so you can verify each resolution.

**Blockers (fixed in the spec; implement carefully in Stage B):**
1. **Gate fallthrough on cure SUCCESS, not "still MISSING."** Editable-install failure ≈ half of build failures (`populate.py` B5/B6 history); a failed cure makes every deferred collision fail its probe and cascade into a batch wrong-install. → cure failure ⇒ collisions unresolved, never install.
2. **"Same cured sys.path" isn't implementable via `python -c`** (conftest sys.path mutations, per-basedir insertion under `importmode=prepend`, `importmode=importlib` inserts nothing, cwd). → ONE canonical `TestEnvPlan` from the repo's config drives both the collect-gate and the probe; residuals documented + flagged.
3. **Editable-install PEP 517 build-isolation chicken-and-egg** — a legacy `setup.py` importing numpy/cython at build time can't see the Phase-A closure under isolation. → the fallback chain (isolated → `--no-build-isolation` with `build-system.requires`).

**Majors (fixed; each has a small concrete change):** §4 lane-aware `missing` + threaded fallthrough re-entry; §5 sequence fallthrough installs BEFORE Phase-B (native needs); §6 PEP 420 namespace-root classifier hole; §7 exception-aware probe verdict; §8 the flag needs a named consumer; §9 the `populate` poison vs the config-lane cert + "where does the cure execute" (nothing runs `-e .` in-container today); §10 split `classify.py` (pure) from `arbitrate.py` (container-bound); §11 relink-vs-probe precedence; §12 "scan drops nothing" relocates 4 load-bearing drops + the `SKIP_WALK_DIRS` hole (excluded-dir locals invisible to both `top_level_names` and `stem_collisions`).

**Minors (fixed):** §13 `import setup` executes `setup.py` (exclude non-importable stems from import-certing); §14 monorepo same-name Module collapse (evidence = `(root, path)` tuple); §15 spine/declared-roots edge+scope rule; §16 django-settings needs env-vars in the cure bundle; §17 target-stdlib rung needs a real source (container probe / static table, not host fallback).

---

## 11. Constraints & gotchas (carry these)

- **Shared branch:** scoped commits only; `-m` before `--`; never `git add -A`; no attribution trailer; append-only.
- **Regression-sweep is the gate** — sweep the repos that PASS before any run; the flip (Stage C) is the highest-risk step.
- **False-green is the enemy** (memories `self-install-false-green-vector`, `honest-success-def-and-branch-split`): installing PyPI `items` when the code meant local `items` passes tests against the WRONG code. Grounding cannot detect it. This is why the config lane exists and why the arbitration must be cure-verified + gated on cure success.
- **Subagent worktree isolation attaches to the wrong repo** (memory `agent-worktree-isolation-wrong-repo`) — do NOT use `isolation:"worktree"`; these are read-only surveys or scoped-commit tasks on the main branch.
- **Execution model that worked this session:** writing-plans → subagent-driven-development with **opus implementers + `codex exec --sandbox read-only -c model="gpt-5.6-terra" -c model_reasoning_effort="high"` as the reviewer** per task, + the pass-repo sweep. The codex review caught real issues (stale comments, a fixture blast-radius) — keep it.
- **Gate A needs Docker + the pilots built** — it's your benchmark environment / the VM, not a code subagent. The harness code is subagent-buildable; the RUN is yours.
- **The two config readers diverge** (path-half searches subdirs, env-half is root-only) — reconciling them is the subtle core of the `TestEnvPlan` (Stage B).

---

## 12. Index — commits, specs, plans, artifacts

**Landed code (this session):** `ba6eae21` (certify-by-import), `d6d1fa43`+`ec771ceb` (drop tier), `cc0ddf96` (FILE scaffold). Composed suite green: **1866 passed**.
**Docs committed:** `dce973ea`,`a7d059bc` (integration spec + edits), `5565dff7`,`7b10c4ec`,`4f01874e` (foundations plan + fixes), `20f3eae6` (config-lane hardened spec), `1d63833b` (Stage A plan).
**User's parallel install-lane commits:** `2a2e61bc`,`b1f108db`,`dfa18c38`,`adaaf602`,`2a58a9f3`,`e5c564d2`,`f6e9380b`.
**Specs:** `docs/superpowers/specs/2026-07-16-two-lane-causal-graph-and-import-classification-design.md`, `2026-07-17-two-lane-model-integration-refactor.md`, `2026-07-17-config-lane-module-spine-and-collision-arbitration-design.md`.
**Plans:** `docs/superpowers/plans/2026-07-17-two-lane-integration-foundations.md` (landed), `2026-07-17-import-dist-pipeline.md` (install lane, in-flight), `2026-07-17-config-lane-stage-a.md`.
**Artifact:** causal model rev 2 (module node) — https://claude.ai/code/artifact/95a2a7d5-9afb-4111-91e8-78d4da6ffece.

---

## 13. What to do in the fresh session

1. Read the "Read first" list (§1) + this doc.
2. **Design + plan Stage B** (use the brainstorming skill if any decision is genuinely open; most are locked in §3). The six sub-plans in §7 are the skeleton; the code maps §4–6 are the anchors. Resolve the two-config-reader reconciliation and the shadow-measurement mechanism concretely.
3. **Then Stage C** (the flip + retire, §8).
4. Keep the **cleanup (§9)** in view but do NOT start it — it falls out of Stage C's stop-emitting-demoted-types; the depgraph/ split is a separate later effort.
5. Gate everything on **Gate A** (§0, Stage A plan): if editable-install doesn't clear the cliff, Stage B/C are cancelled.

**Open questions to resolve in Stage B/C design:** the exact shadow-measurement harness (reuse bench's two-arm `--harvest`?); which config reader becomes the single canonical one; whether the cure runs once (project-level) or per-Module; the provisional-flag's exact schema + eval bucket wiring; and the sequencing of the lane-aware fixpoint change against the user's in-flight install-lane edits to the SAME `_phase_a_fixpoint`.
