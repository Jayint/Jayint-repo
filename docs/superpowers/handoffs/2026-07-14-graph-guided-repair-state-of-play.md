# Handoff — Graph-Guided Agent Repair: State of Play

**Date:** 2026-07-14. **Read this before proposing anything.** Six plausible designs were proposed
in this investigation and **five were killed by measurement**. They are listed in §4 so you do not
re-propose them. Every number below was measured, not inferred; provenance is in §8.

---

## 1. The system

An LLM agent repairs a broken build script (`setup.sh`) for a Python repo, in a ReAct loop
(`src/react_repair/`). **Every turn is a FULL CONTAINER REBUILD** (reset to base → re-run the whole
script). That is the entire economics: a rebuild is ~150–400 s; an LLM call is ~5–30 s. Rebuilds
dominate by 10–40×.

A dependency graph (`src/python_deps/depgraph/`) is supposed to help the agent by naming the root
cause. Construction builds it once (static scan → uv resolve → Debian build-deps prior → wheel
preflight → `ldd` probe → import probe → LLM env classifier). The repair loop then *enriches* it
each turn — today, from **error text only**.

Ablation rungs already exist: **G0** (compress) / **G1** (histogram — the control) / **G2**
(`REACT_GRAPH_CONTEXT=1`, render) / **G3** (`REACT_GRAPH_UPDATE=1`, render + grow). **G3 implies G2.**

---

## 2. What was built this session

`src/eval/graph_quality/` — an eval for the graph itself (commits `c1afd0f`..`6b30b16`, 2336 tests
green). Four graders, all offline after a one-off Docker mint:

- `corpus.py` — mines **111 labelled (error → fix) pairs** from `outputs/repo2run_benchmark/`
  (420 real agent runs, git-tracked). The label is the agent's **own** repair: diff the Dockerfile
  before/after a failure; the packages it ADDED are, by construction, what that failure required.
- `enrich_replay.py` — replays each failure through the real `enrich()`. Headline metric.
- `patch_localize.py` — grades whether the ★ lands on the right node (root-hit **and** star precision).
- `graph_cache.py` + `block_parity.py` — 16 real Docker-minted graphs; emit-parity, metamorphic
  properties, an independent reference oracle.
- `poc/poc_artifact_enrich.py` — a POC. **It is rigged. See §4.2.**

---

## 3. The numbers (all measured)

**Corpus shape — this is the most important table in the document:**

| what the agent's real repair actually was | n | can a dependency graph address it? |
|---|---:|---|
| os-package (apt) | **3** | yes |
| python-package (pip) | **11** | yes, but the LLM already knows these |
| env-var / PATH / Config | **25** | detect the need; only the LLM knows the value |
| **source patch** (conftest stub, mocked module, circular import) | **72** | **NO — structurally outside the model** |

**Graph quality:**
- enrich pre-emption **2/14** in-scope (os 1/3, python 1/11). *n=14 — too small for a rate.*
- patch root-hit **1/3**; **star precision 0.25**.
- block parity **0 divergences / 936 nodes** — but the corpus exercises only **1 of 4** blocking rules.
- negative control: **4/72** source-patch pairs produced a phantom node.

**The graph's entire world knowledge is 55 hardcoded entries:**
```
PROVIDER_TABLE             29 entries (only 10 sonames, ZERO CUDA)   capability → apt package
CLI_TOOL_TO_APT            11 entries (has ffmpeg; NO tesseract/poppler)
CURATED_IMPORT_TO_PACKAGE  15 entries (bs4 crypto cv2 django_filters factory fitz github lxml
                                       mysqldb openssl pil psycopg2 sklearn socketio yaml)
```
**Every measured failure traces to a miss in one of these three dicts.**

---

## 4. 🔴 IDEAS THAT WERE PROPOSED AND KILLED. Do not re-propose these.

### 4.1 "The arm is BLIND on green builds" — **FALSE**
Claim: `pip install opencv-python` → rc 0, then `import cv2` dies on `libGL.so.1`; there is no
error text, so `enrich()` is structurally blind.
**Refuted.** The loop runs **pytest on every green build** (`loop.py:282`), `enrich()` ingests
collect-phase causes (`graph_enrich.py:120`), and `failure_classifier.SONAME_RES` matches the
soname. Verified by reproduction: the pytest stream discovers `syslib:libxcb.so.1` unaided.
**pytest IS the arm's error text.**

### 4.2 The POC (`poc/poc_artifact_enrich.py`) is **RIGGED** — do not cite it
It hands "Arm A" only the *build log* and **withholds the pytest output the real loop always has**,
then reports "Arm A discovered NOTHING." That zero is manufactured by the harness. The file is kept
as a record of the error, not as evidence.

### 4.3 "2/14 is a plumbing gap fixable by per-turn `ldd_probe`" — **FALSE**
All 12 in-scope misses were decomposed: **~6 are label noise** (SSL/network/404 failures), **4 are
collect-phase `ModuleNotFoundError`s that die on `name=None`** (`map_import_to_package` can't name
the dist → **no node is minted at all**), **1** is a `summarize()` parse gap, **1** a resolver gap.
**Zero have a missing-`.so` signature.** Per-turn `ldd_probe` would convert **none** of them.

### 4.4 "The graph's value is the OS tier, where LLMs are weakest" — **FALSE**
Measured: **41/420 repos** ever hit a missing `.so`; **597 of the occurrences are `libGL.so.1`**;
**31/41 final Dockerfiles already contain the canonical apt fix, found graph-free from error text.**
LLMs know `libGL.so.1 → libgl1` and `tesseract → tesseract-ocr` cold. **A 55-entry dict does not.**

### 4.5 The AUTONOMOUS TIER (graph applies fixes itself, no LLM) — **NET NEGATIVE. Killed by three independent reviewers.**
- **Reach: 1/111 repairs (0.9%).** And that one case (`libGL`) already has the soname in the error text.
- **Error rate: 2 of 3 applies are WRONG — and BOTH CERTIFY GREEN.** Example: real error is
  `/root/.local/bin/poetry: not found` (a **PATH** bug); tier does `apt install python3-poetry`;
  `command -v poetry` → rc 0 → **SATISFIED**. The revert gate never fires. The real fix was
  `ENV PATH=...`. **A false-SATISFIED by provider substitution.**
- **Economics: the LLM batches; the tier cannot.** In 9 rounds the LLM added **195 packages in 9
  rebuilds** (one round added 61). A one-fix-per-rebuild loop needs up to 195 rebuilds. ~21× worse.
- **It bypasses the anti-cheat gates.** `narrowing_reason` / `added_self_install_reason` live in
  `_classify_action` — **the LLM's edit path only**. An autonomous script author walks straight
  through them, and there is already a **real false-green incident** on record (agent
  `pip install`ed the project from PyPI instead of `-e .` → 297/297 green against code not in the repo).
- The hallucinated `service:rabbitmq` node **has a live provisioning recipe**
  (`service_tables.py:20`), so under autonomy the lie *executes*.

### 4.6 "`ldd_probe` produces phantoms" — **FALSE, and the truth is worse.** See §5.1.

---

## 5. What actually SURVIVED (all measured, all real)

### 5.1 🔴 EVERY SystemLib node in the corpus is a PHANTOM (10/10)
The 16 real graphs contain exactly 10 SystemLib nodes — all `libarrow*.so.2400`, all in `pillow`.
```
discovered_by = RESOLVER   provenance = 'wheel:pyarrow'   state = MISSING
pkg:pyarrow==24.0.0  → SATISFIED        import:pyarrow → SATISFIED   ← it imports FINE
```
They come from **`wheel_preflight`**, which reads a wheel's `DT_NEEDED` *before* installing it — at
which point `libarrow.so.2400` isn't on the system **because it ships inside the wheel**. Nothing
ever corrects this: `ldd_probe` only *appends* `=> not found` nodes and never **revokes** a false
prediction, and `certify`'s check is `ldconfig -p | grep libarrow.so.2400`, which fails forever
because the lib lives in `pyarrow/`, not the loader cache.
**Verified:** a live container test shows `ldd` on pyarrow's extension resolves cleanly and
`import pyarrow` works.
**Fix:** a SATISFIED import of package P **proves** every `DT_NEEDED` soname of P's extensions
resolved. *Function beats presence.* Let a successful import **revoke** the phantoms beneath it.

### 5.2 🔴 The attempt log is DEAD CODE — the only defect that deadlocks the *run*
`graph_context.py:384` renders `tried turn 2: apt-get install -y libpq-dev → FAILED`, with the
comment *"agents re-retry disproven fixes because their memory is lossy prose."*
**`with_attempt` has ZERO call sites in `src/react_repair/`.** The field is always empty. The graph
will re-propose a fix the agent already watched fail — forever.

### 5.3 Capability nodes are not deduplicated by ACTION → star precision 0.25
Construction mints them in **five** aliasing id spaces (`binary:` `aptdep:` `tool:` `syslib:`
`linker:`). `pygraphviz` predicts **four** MISSING nodes that **one** `apt-get install
libgraphviz-dev` satisfies. The renderer stars all four. Collapse the frontier by **action**, not
by symptom name.

### 5.4 The graph DISCARDS what it observed, because it couldn't resolve it
```
libcgraph.so.6 discovered ✅ → not in the 29-entry table → chosen_fix=None → root with NO ACTION
import torch   discovered ✅ → not in the 15-entry table → name=None       → NO NODE AT ALL
```
**Principle: never discard an observation for lack of a resolution.** Mint the node; say
`fix: UNKNOWN — you name it`. The LLM knows.

### 5.5 One-line hygiene bugs
- `service_scan.py:154` — `re.compile(r"amqp|...|rabbitmq", re.I)` is an **unanchored substring**.
  Every other service requires connection-error context. It fires on **8 repos** (matching
  `librabbitmq4` in **apt's own SUCCESS output**); **real AMQP failures in the entire corpus: 0.**
  **Precision 0.00.**
- `CLI_TOOL_TO_APT` lacks `tesseract` / `pdftoppm` — **2 of the 4 real apt names in the corpus**,
  and structurally invisible to `ldd` (a subprocess-invoked binary has no ELF entry).
  `subprocess_scan` is already wired; this is a *table* gap.
- `project:<repo>` has **no `check_command`** → UNKNOWN in 16/16 graphs → the Test goal node renders
  **ACTIONABLE** in every repo.
- `failure_classifier.MODULE_NOT_FOUND_RE` uses `.search()`, not `.finditer()` → only the FIRST
  `ModuleNotFoundError` in a collection blob is ever seen.

---

## 6. 🔴 THE CURRENT THESIS (the conclusion the evidence actually supports)

> **The graph's value is NOT in telling the agent what is broken. The error log does that better,
> and the model already knows the fixes.**
>
> **Its value is telling the agent what is NOT broken, and what a fix would be worth.**

Score the graph against a plain error-log agent, turn by turn:

| the situation | graph adds |
|---|---|
| `ImportError: libGL.so.1` | **≈0** — the LLM writes the canonical apt line from memory (31/41 repos did) |
| `fatal error: graphviz/cgraph.h` | **0** — the error text already names the header. The graph *restates* it. |
| the agent retries a failed fix | **modest** — the ReAct history already has it as prose; matters on long runs / after elision |
| `ModuleNotFoundError: 'comfy'` | **REAL and STRUCTURAL** |

**Two things a log structurally cannot do:**
1. **Prove a negative.** A log only shows what *failed*. It can never say *"everything else is
   fine."* A graph that has certified 84/84 packages **can** — and that is the answer to **72/111**
   repairs, where the whole failure mode is the agent burning rebuilds hunting a package that was
   never missing. **This is completely unmeasured and is the biggest untested upside in the system.**
2. **Blast radius.** `ImportError` doesn't say whether the root blocks **47 tests or 2**. That is
   the ranking signal for which failure to spend your one rebuild on.

**Corollary: the graph should LOCALIZE; the model should RESOLVE.** The graph knows what the model
cannot (what's inside *this* container, who needs it, what it blocks, what already failed). The
model knows what a 55-entry dict never will (which Debian package ships an arbitrary `.so`).

---

## 7. Proposed order (argue with it)

1. **Attempt log** (§5.2) — dead code; the only defect that deadlocks the run.
2. **Import revokes phantom syslibs** (§5.1) — the SystemLib tier is currently **100% false positive**.
3. **Never discard an unresolved observation** (§5.4) — one principle, both tiers.
4. **Fix-keyed collapse** (§5.3) — 4 ★s → 1 action.
5. **One-line hygiene** (§5.5).
6. **THE NEGATIVE VERDICT** — *"environment certified; this is not an environment failure."*
   Biggest slice (65%), unmeasured, and the only thing a log cannot say.
7. **Then the ablation** — and note the metric must change. "Pre-emption" scores **resolution**, a
   game we are deliberately conceding to the model. The right metric is **turns-to-green /
   rebuilds saved**, sliced by failure family.

**Open question for you:** is (6) real? It is the largest claim in this document and has **zero**
measurements behind it. Design the experiment that would falsify it.

---

## 8. Provenance & working discipline

Every number: `src/eval/graph_quality/` (the graders), the 16 committed graphs in
`src/eval/graph_quality/graphs/`, and `outputs/repo2run_benchmark/` (420 real runs, git-tracked).

**Specs:** `docs/superpowers/specs/2026-07-13-graph-quality-eval-design.md`,
`2026-07-14-unified-graph-enrichment-design.md` (**contains the refuted thesis — read §4 first**),
`2026-07-14-REVIEW-REQUEST-unified-enrichment.md` (the adversarial review packet).

**The discipline that actually worked, and you should keep:**
- **Every headline was WRONG on first implementation.** An adversarial reviewer (gpt-5.6-terra)
  caught a real defect in **every one of 7 tasks**, including a grader that scored *hand-built
  fixtures it had authored itself*, and a grader that fed pytest failures down the wrong enrich
  stream and thereby *slandered the graph*.
- **A bad measured number is a RESULT, not a failure to tune away.**
- **Never edit the code under test to make an eval pass.** That is a finding, not a licence.
- **Verify the plan against the source.** Claims here that were asserted-then-refuted:
  a "guaranteed" `import:X → pkg:X` edge (it exists **113/164 = 69%** — and that 69% *is the install
  success rate*, since `certified_import_links` can only certify dists that installed);
  and `owner_anchored=0/14`, which is a **corpus artifact** (repo2run batches its installs; our arm
  renders one `pip install` per line).
- `python` is NOT on PATH — use `python3`. Never `git add -A`; the user commits in parallel on this
  shared branch.
