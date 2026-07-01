# Graph-Fidelity Eval Loop

**North star:** make v3-core's **graph construction + initial bash-script generation** as
correct as it can be — measured on real repos — so the *first-pass* `setup.sh` handles
env-setup with near-zero LLM and no repair loop. Each iteration: run the eval, find the top
root-cause gap in the graph, fix the **constructor** (not the repo), re-verify, log.

> Scope guard: this loop is about the **graph + initial script only**. The repair loop / agent
> is explicitly OUT of scope — we nail graph generation first. Do NOT run or improve
> `repair_loop.py` here.

---

## 0. Resume first (durable progress)

Before anything, read the ledger and resume where it left off:

```
docs/superpowers/loops/graph-fidelity-LEDGER.md
```

Trust the ledger + `git log` over memory. If a harness component or fix is marked complete
there, it is done — do not redo it. Start at the first unchecked step.

---

## 1. What "correct" means (the model vs reality)

The graph is a **model M** of the true setup **T**. Gaps come in six types, on three axes:

| Gap | Axis | Detected by |
|---|---|---|
| Missing node | node recall | oracle-diff + execution failure class |
| Spurious node | node precision | (weak here; needs ablation — deferred) |
| Wrong content | node content | oracle-diff on matched nodes (apt name, pin, tier) |
| Missing edge | edge recall | wrong-order execution failure |
| Spurious edge | edge precision | (deferred to ablation) |
| Tier misclass | content/routing | oracle-diff (declared tier vs graph tier) |

Grade **three deltas**, and always attribute a failure to exactly one:

1. **graph vs T** — is the model right? (per-tier recall + content) → *construction bug*
2. **script vs graph** — does the renderer faithfully emit every `SATISFIED` node in topo
   order with content intact? (deterministic, no container) → *render bug*
3. **script vs T** — does the first-pass `setup.sh` reach green on a lean base? (the honest
   arbiter)

If (1) is clean but (3) fails → renderer. If (1) fails → construction. That split names the
module to open every time.

---

## 2. Ground truth (approach A + existing VM baselines)

**Corpus: `medlarge15`** (already stratified across complexity classes).
Dataset (SHA-pinned): `/opt/harness/datasets/rat_python_medlarge15.json` on the VM.
15 repos — 8 large (MemOS, darts, Qiskit, aiida-core, feast, pretix, Archipelago, baserow),
7 medium (typer, vizro, slither, anthropic-sdk-python, mvt, python-semantic-release,
postgres-mcp).

**Oracle = the repo's own verified recipe, held OUT of construction:**
- Construction reads **code + declared deps** (`pyproject`, `requirements*.txt`, imports,
  `.python-version`) only.
- Oracle = the **human recipe**: `Dockerfile`, `.github/workflows/*`, `docker-compose*`.
- **LEAKAGE INVARIANT:** construction in eval mode must NOT read the held-out recipe files.
  If any enrichment miner reads Dockerfile/CI/compose, disable it for the eval (or k-fold to a
  different held-out source). Grading the graph against a file you built from teaches nothing.

**Existing baselines give labels for free** (in `/opt/runs/baselines/` + `/opt/runs/radical/`):

| Baseline | Build model | Role | VM run dir | pytest cov |
|---|---|---|---|---|
| **RAT** | LIVE committed container, no rebuild | **feasibility ceiling** — NOT a head-to-head peer | `/opt/runs/baselines/rat_medlarge15-20260627-134444` | 13/15 |
| **repo2run** | fresh Dockerfile rebuild | **honest peer** (same as us: build-from-nothing) | `/opt/runs/baselines/repo2run_medlarge15-20260627-162444` | 8/15 |
| **ccdf** | fresh rebuild (Sonnet) | honest peer (+ $ cost) | `/opt/runs/baselines/ccdf_medlarge15-20260627-183205` | 11/15 |
| **radical** | our DockerAgent | prior baseline to beat | `/opt/runs/radical/radical-medlarge15-20260628-025304-20260628-025305` | 11/15 |

Scorer: `/opt/harness/scripts/compute_essr.py`. Each run's per-repo results live at
`<run_dir>/output/<org>/<repo>/run_pytest_results.json`. RAT reaching tests on only 13/15 means
**2 repos are infeasible even at the ceiling** → they drop from OUR recall denominator.

Rails (both are existing doctrine — do not violate):
- **Honest scoring only.** Re-score every baseline and our arm with `scripts/compute_essr.py`
  from raw pytest JSON. NEVER trust `rat_results.json` / `_result_row.status` / runner
  "build success". A baseline "passed repo X" ⇔ honest `pass_rate ≥ τ` (τ = 0.8).
- **RAT is the ceiling, not the bar.** RAT wins by never rebuilding; we rebuild from nothing.
  Use RAT for the **feasibility gate** — if RAT can't honestly pass a repo, drop it from OUR
  recall denominator (the repo is the problem, not the graph).
- **Empirical container-oracle is UNAVAILABLE (confirmed 2026-07-02).** The winning containers
  were pruned by the VM disk janitor (`docker ps -a` = 0). Oracle = static recipe only. To ever
  recover the empirical oracle you must re-run RAT with `docker commit` + prune disabled —
  phase-2, out of scope here.
- **Honest peers = repo2run / ccdf.** Our headline = first-pass graph-script vs these.
- **Config asymmetry, stated openly.** Baselines ran `--repair-rounds 2` + a 30-turn LLM. Our
  arm is first-pass, no repair, minimal LLM (only `choose_base_image` + `env_classifier`). The
  gap you see IS the remaining construction work — report it that way, not as matched params.

**VM contact is BOOTSTRAP-ONLY.** Fetch labels + dataset once, cache locally, then never SSH
in the hot loop (VM ssh over long loops is fragile). VM: `ssh root@167.233.64.96` (key-based).
Cache to `outputs/graph_fidelity/baseline_labels.json` and `datasets/rat_python_medlarge15.json`.
Do not echo secrets from `/opt/harness/.env`.

---

## 3. Harness to build (bootstrap phase — TDD, one component per iteration)

Build these under `scripts/eval/graph_fidelity/` (tests under `tests/eval/graph_fidelity/`).
Reuse existing modules — do not reimplement: `build_dep_graph`, `render_build_script`,
`runtime_classify`, `choose_base_image`, `compute_essr`.

1. `oracle.py` — parse held-out recipe files → declared node set per tier (apt names, python
   minor, pip closure + pins, services, env). Pure, deterministic.
2. `baseline_labels.py` — one-shot VM fetch of the 4 run dirs (§2 table) + dataset → re-score
   each `<run>/output/<org>/<repo>/run_pytest_results.json` with `compute_essr` → cache
   `{repo: {rat/repo2run/ccdf/radical: {honest_pass, pass_rate}}}` + feasibility flag (RAT
   honest_pass). No container-mining — containers are pruned (§2).
3. `scorecard.py` — per repo: `choose_base_image` (-slim) → `build_dep_graph` (repair OFF) →
   grade graph-vs-oracle → `render_build_script` → grade render-fidelity → fresh `-slim`
   container, run `setup.sh` once, capture rc + stderr, classify via `runtime_classify` →
   if rc=0 run `pytest`, score via `compute_essr` → emit per-repo JSON (schema in §6).
4. `gaps.py` — from scorecard + oracle-diff → typed gaps `{type, tier, id, stage, evidence}`,
   attributed to a constructor stage (scan / detect_target_env / select_roots /
   resolve_closure / reconcile_apt_names / certify / render).
5. `report.py` — aggregate → per-class + pooled rates, peer/ceiling comparison, and **gap
   clusters ranked by (stage, type, count)**. The top cluster is the next fix.
6. `run_eval.py` — CLI: `python -m scripts.eval.graph_fidelity.run_eval --corpus medlarge15
   [--repos ...] [--seed]`. Caches per-repo results keyed by `(repo_sha, construction_commit)`
   so unchanged repos aren't re-executed.

**Start on the seed** (`--seed` = ~8 hand-verifiable repos: typer, anthropic-sdk-python,
slither, postgres-mcp, mvt, python-semantic-release, vizro, darts). Get the harness trusted on
repos where you know the answer, then expand to all 15.

---

## 4. Per-iteration protocol (improve phase)

Each loop iteration does **at most ONE root-cause fix**:

1. **Run the eval** (`run_eval.py`, seed until trusted, then full 15). Read
   `outputs/graph_fidelity/<date>/report.md`.
2. **Pick the top gap cluster** — highest `(stage, type)` count across feasible repos. Not a
   single-repo symptom — a *class* (e.g. "soname→apt wrong in 6 repos @ reconcile_apt_names").
3. **Diagnose construction-bug vs render-bug** using the three deltas (§1). Locate the exact
   stage/module.
4. **Name the root cause first (write it down).** One line: the offending stage, the *invariant*
   the gap violates, and WHY the whole class exists — not the symptom. This becomes the ledger
   `Why` and must be paper-quotable. If you can't name a root cause, you're staring at a symptom —
   dig further, do not patch.
5. **Fix the constructor (TDD), paper-clean.** Write a failing test encoding the gap (real-repo
   fixture or minimal unit repro), fix the *root cause*, watch it go green. The fix MUST:
   - **generalize across the cluster** — verify it fixes *every* repo in the cluster, not one;
   - **honor the interpretability directive** ([[core-branch-paper-interpretability-priority]]):
     ONE clean path — NO flag-gates, migration fallbacks, or dead branches; a deterministic rule
     over an LLM wherever a rule suffices; immutability preserved; small focused functions;
     **delete** superseded code, don't leave both paths;
   - **read like the surrounding reference code** — this branch IS the paper's reference impl.
   Any lookup added (e.g. soname→apt) must be principled, sourced, and as complete as the class
   demands — never a per-repo hardcoded skip that papers over the symptom.
6. **Self-review against the bar (before commit).** Diff the fix and check every box: root cause
   named? generalizes across the cluster? ONE clean path, no fallback/flag/dead code?
   rule-over-LLM? superseded code deleted? reads like its neighbors? **metric NOT gamed** (§7)?
   If any box fails, fix it or revert — a green-but-hacky fix does not ship.
7. **Re-verify:** re-run the affected repos + the module's unit tests. Confirm the target metric
   moved AND no repo regressed (first-pass install/test rate must not drop). If a fix regresses
   the corpus, **revert it** and log why.
8. **Commit** (one fix per commit, conventional message, do NOT push).
9. **Log to the ledger** (§5) and continue to the next cluster.

**Target metric (maximize):** honest **first-pass testability rate** on medlarge15
(`compute_essr` pass_rate ≥ 0.8), near-zero LLM. Secondary: first-pass installability rate,
per-tier recall, render-fidelity clean rate.

### 4b. Planned enrichments — reach for these before inventing

Two enrichments are already designed (spec:
`docs/superpowers/specs/2026-07-01-construction-enrichment-clusters-1-2-design.md`). If the top
cluster is a gap-class one of these targets, implement the planned enrichment per its spec — do
NOT invent a new mechanism. They obey §7 (derived priors, no curated package→syslib table, LLM
proposes SOFT only), so implementing one is a legitimate root-cause fix.

- **Cluster 1b — declaration mining** (deterministic stage 3c): parse the repo's OWN apt
  declarations (`Dockerfile` RUN apt / `Aptfile` / `binder/apt.txt` / CI) → HARD `apt:<pkg>`
  nodes (origin `STATIC_DECLARATION`). Targets **missing SYSTEM_LIB/TOOL nodes the repo itself
  declares.**
  - ⚠️ **LEAKAGE COLLISION — must handle.** 1b mines `Dockerfile` + CI, the *same* files the
    eval holds out as oracle (§2). Enabling it in eval mode = building from the answer key.
    Resolution: **k-fold** — hold out a source 1b does NOT read (grade against CI while 1b mines
    Dockerfile, or use a hand-verified oracle), and report 1b's recall lift only on repos where
    the mined source ≠ the grader source.
- **Cluster 2 — widened LLM discovery:** feed raw prose (README etc.) to the construction
  classifier → propose **SOFT** `syslib:<name>`/`tool:<name>` (origin `CLASSIFIER`). Targets
  **syslib/tool needs declared nowhere but mentioned in prose.** Leakage-safe (reads prose, not
  the held-out recipe).
- Adjacent — **cluster 1a wheel-oracle prior**: no compatible wheel ⇒ sdist ⇒ compiler ⇒ seed
  build-essential. Leakage-safe; targets missing-compiler / build-essential gaps.

### 4c. Execution model — thin orchestrator, Sonnet subagents, file handoffs

The `/loop` session is a **thin state machine**: it dispatches a fresh Sonnet subagent for every
token-heavy step and keeps only small structured returns. Heavy artifacts (container logs, pytest
dumps, scorecards, full diffs, source reads) live in the subagent's context and on disk — **never
in the orchestrator's context.** That is what keeps the main session from filling up.

Per-iteration subagent pipeline (maps to §4 steps) — inputs are passed **by path, never pasted**:

| §4 step | Subagent (fresh each time) | Model | Inputs (by path) | Returns to orchestrator (small) |
|---|---|---|---|---|
| 1 run eval | eval-runner | Sonnet | runs `run_eval.py`; writes scorecards + `report.md` | `{test_rate, install_rate, top3_clusters:[{stage,type,count,repos}], report_path}` |
| 3–4 diagnose | root-cause researcher (read-only) | Sonnet | `report.md` + the cluster's scorecards + depgraph source | `{stage, invariant, why_class, construction\|render, approach, files, planned_enrichment?, leakage_risk}` (also written to a brief file) |
| 5 fix (TDD) | implementer | Sonnet | the diagnosis **brief file** + §7 bar | `{status, commit_sha, test_summary, concerns}` (full report → file) |
| 6 review | reviewer (independent) | Sonnet | `review-package` **diff file** + §4.6/§7 checklist | `{verdict, findings[]}` |
| 6 fix findings | fixer | Sonnet | findings + report file | `{status, commit_sha}` |

The orchestrator's own footprint per iteration = chosen cluster + one-line diagnosis + commit sha
+ verdict → **one ledger line.** Nothing else.

**Context-hygiene rules (the levers):**
- The orchestrator **never `Read`s** scorecards, logs, pytest output, or full diffs directly — it
  asks a subagent for a summary/verdict. Its own `Read` is limited to the ledger and small briefs.
- **Every handoff is a file path** in the dispatch prompt, never pasted content. Materialize
  briefs/diffs with the SDD scripts (`task-brief`, `review-package`) into
  `outputs/graph_fidelity/iter-N/`.
- Tell every subagent: *"your final message is DATA for the orchestrator — return ONLY the listed
  fields; write logs / full analysis / the report to `<path>`."*
- Dispatch prompts are self-contained: task + interfaces + §7 constraints. **No pasted session
  history** — that is what re-bloats context.
- One cluster per iteration → every subagent's scope stays small.

**Model tiering:** Sonnet for research / implement / review (judgment). Haiku only for pure
mechanics (launch the harness, capture a git sha) if trimming cost. Reserve the most-capable model
for the **periodic whole-branch review** or a `BLOCKED` escalation — not the routine steps.

Because the orchestrator is thin and every artifact is on disk + in the ledger, **compaction is
survivable**: a re-invoked loop rebuilds state from §0 (ledger + `git log`), not memory.

---

## 5. Ledger (durable, committed)

`docs/superpowers/loops/graph-fidelity-LEDGER.md`. Every iteration appends one block:

```
## Iteration N — <date>
- Observation: <the top cluster + the metric before>
- Why: <root cause — offending stage, the invariant it violates, why the whole class exists; paper-quotable>
- What: <the one fix; commit <sha7>; what superseded code was deleted>
- Verification: <metric after; repos re-run; regressions checked; interpretability self-review passed>
```

The ledger is the recovery map after compaction — the commits it names exist in git even if
context forgets them.

---

## 6. Scorecard schema (per repo)

```json
{
  "repo": "org__name", "sha": "…", "complexity_class": 0,
  "base_image": "python:3.11-slim",
  "graph": {"nodes_by_tier": {"SYSTEM_LIB": ["…"], "PACKAGE": ["…"], "…": []}, "edges": 0},
  "oracle": {"declared_by_tier": {"SYSTEM_LIB": ["…"]}, "source": ["Dockerfile","ci"], "held_out": true},
  "grade": {
    "recall_by_tier": {"SYSTEM_LIB": 0.0, "PACKAGE": 0.0, "RUNTIME": 0.0},
    "content_by_tier": {"SYSTEM_LIB": {"apt_name_ok": 0.0}, "RUNTIME": {"minor_ok": true}},
    "render_fidelity": {"missing_nodes": [], "misordered": []},
    "first_pass_install": {"rc": 0, "error_class": null, "gap": null},
    "first_pass_test": {"pass_rate": 0.0, "collected": 0, "passed": 0}
  },
  "baseline_labels": {"rat": {"honest_pass": true}, "repo2run": {"honest_pass": false},
                      "ccdf": {"honest_pass": true}, "radical": {"honest_pass": false}},
  "feasible": true,
  "gaps": [{"type": "missing_node", "tier": "SYSTEM_LIB", "id": "libGL",
            "stage": "reconcile_apt_names", "evidence": "ldd: libGL.so.1 not found"}]
}
```

---

## 7. Guardrails / invariants

- **Run fully autonomously.** Make every routine decision from this doc's defaults — NEVER pause
  to ask the human for input or confirmation between steps or iterations. The only halts are the
  §8 stop conditions (and a true `BLOCKED` the doc cannot resolve). On any halt, STOP and write
  the summary — do not block waiting for a reply. Default reviewer cadence = **independent
  Sonnet review per fix** (§4c); do not ask whether to run it.
- **No repair loop, no agent.** First-pass artifact only. Minimal LLM (`choose_base_image` +
  `env_classifier`); everything else deterministic. (You may optionally ablate those two to
  report a "fully deterministic" recall number.)
- **Leakage invariant** (§2): construction must not read held-out recipe files.
- **Honest scoring only** (§2): `compute_essr` from raw pytest; never runner status.
- **RAT = ceiling, not peer** (§2). Feasibility gate before counting a repo as our miss.
- **Run on `-slim`** (leanest base) so pre-shipped headers don't mask recall gaps.
- **Immutability**: graph stages return new `DepGraph`; never mutate in place.
- **Root-cause, not whack-a-mole**: fix the stage, not the repo. One fix per iteration.
- **Paper-clean, or it doesn't ship.** Every fix honors
  [[core-branch-paper-interpretability-priority]]: ONE clean path, no flag-gates/fallbacks/dead
  code, rule-over-LLM, delete-what-you-supersede, reads like the reference impl. Code quality is
  a merge gate equal to the metric — this branch is the paper's reference implementation.
- **No metric-gaming (hard rule).** NEVER raise the metric by per-repo special-casing, hardcoded
  skips, weakening the oracle, or loosening the gate. A fix that lifts the number but adds a hack
  is a REJECT — it fails the §4.6 self-review even when green. The metric is a *proxy* for a
  correct graph; optimizing the proxy while corrupting the graph or the measurement is failure.
- **Methodology stays paper-honest.** Held-out oracle (no leakage), `compute_essr` from raw
  pytest, feasibility gate, stratified per-class reporting, pass-by-luck flagged. Do not "fix" a
  gap by corrupting the measurement.
- **Never edit tests to pass**; fix the implementation (unless the test is provably wrong).
- **Do NOT push.** Commit locally only.
- **VM is bootstrap-only.** No SSH in the hot loop. Never echo `.env` secrets.
- Record **Observation → Why → What → Verification** per change (ledger).

---

## 8. Stop conditions

Run continuously until one of these holds, then STOP and write the summary (do NOT block waiting
for input — these are halts, not questions):
- First-pass testability rate plateaus (no cluster improves the metric for **2 consecutive
  iterations**).
- All feasible repos reach first-pass green, OR all gap clusters resolved.
- A fix requires the repair loop / agent (out of scope) → log it as a phase-2 item and stop.
- A step would require a forbidden action (push / destructive VM op / secret exposure) → do NOT
  do it; stop and summarize what was blocked.

On stop, write a final ledger block with the before/after metric table (our first-pass rate vs
repo2run / ccdf peers and the RAT ceiling) — that comparison is the deliverable.
