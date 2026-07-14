# REVIEW REQUEST — Unified Graph Enrichment

**You are being asked to find what is WRONG with this design.** Agreement is not the goal; a
correct verdict is. If the design is sound, say which parts you would cut anyway. If it is an
elegant solution to a problem that does not matter, say that — it is the most likely way this is
wrong, and the author cannot see it from the inside.

Everything below is measured, not asserted. Provenance is in §7. **Check it.**

---

## 1. The system under review

An LLM agent repairs a broken build script (`setup.sh`) for a Python repo, in a ReAct loop.

**Every turn is a FULL CONTAINER REBUILD.** That is the entire economics. A wasted turn is a
wasted container build; a deadlocked loop is a wasted run.

A dependency graph ("the graph") is supposed to help the agent by naming the root cause. It is
built once by *construction* (static scan + resolver + Debian build-deps prior + wheel preflight +
`ldd` probe + import probe + LLM env classifier), then handed to the repair loop, which *enriches*
it each turn from what it observes.

**Key files**
- `src/react_repair/loop.py` — the repair loop
- `src/python_deps/depgraph/graph_enrich.py` — `enrich()`, the arm's only enrichment path
- `src/python_deps/failure_classifier.py` — the regex classifier `enrich()` depends on
- `src/python_deps/depgraph/ldd_probe.py` — the artifact probe (**exists; the arm never calls it**)
- `src/python_deps/depgraph/certify.py` — `certify_all()`, per-node `check_command`
- `src/python_deps/depgraph/graph_context.py` — the render the agent actually sees
- `src/python_deps/depgraph/build.py:989` — `_python_native_obligations`, construction's probes

---

## 2. What was measured (an eval was built first; `src/eval/graph_quality/`)

Mined **111 labelled (error → fix) pairs** from real past agent runs in
`outputs/repo2run_benchmark/` — the label is the agent's *own* repair (diff the Dockerfile
before/after a failure; the packages it ADDED are, by construction, what that failure required).

| slice | n | meaning |
|---|---:|---|
| os-package (apt) | 3 | the graph's core competency |
| python-package (pip) | 11 | the LLM mostly knows these already |
| env-var (Config) | 25 | |
| **source patch** | **72** | **a dependency graph structurally CANNOT address these** |

**Headline results:**
- **enrich pre-emption: 2/14** in-scope pairs (os 1/3, python 1/11). *n=14 is too small for a rate.*
- **patch root-hit: 1/3** of the structurally-scorable injection cells (only 5 cells exist).
- **block parity: 0 divergences** over 936 nodes of 16 real Docker-minted graphs — **but the
  corpus exercises only 1 of the 4 blocking rules**; the other 3 (conflicts, missing-syslib,
  known-wheel-with-missing-tool) have ZERO instances, so a bug in them could not have been caught.
- **negative control:** 4/72 source-patch pairs produced a graph node = false positives.
- **hallucinations: 0/14 in-scope**, but the graph *does* lie: it invented a `service:rabbitmq`
  node from this line of **apt's own SUCCESS output** — `librabbitmq4` in a list of ffmpeg deps
  being installed successfully.

**Read that as: the graph is high-precision, low-recall. It rarely lies; it usually has nothing
to say.**

---

## 3. The design's central claim

> **A successful build can still be a broken environment — and error-text enrichment is
> structurally blind to it.**

`pip install opencv-python` returns **0**. `import cv2` then dies on a missing `libGL.so.1`.
There is **no failing command and no error text**, so the classifier has nothing to classify. The
fact is sitting in the `DT_NEEDED` header of an installed `.so`, and only reading the artifact
recovers it.

**Structure has three sources. The arm uses only the worst one.**

| # | source | owner attribution | can hallucinate? | uniquely sees |
|---|---|---|---|---|
| 1 | **artifact** — `ldd`/`DT_NEEDED` on installed `.so` | **exact** (the `.so` is *inside* the package) | no | **runtime `.so` on a GREEN build** |
| 2 | **experiment** — isolated `python -c "import X"` | **exact by construction** | no | missing modules; GLIBC mismatch |
| 3 | **error text** — the regex classifier | fragile | **yes** | **build-time tools** (`pg_config`) — the build failed, so no artifact exists |

**The rule:** *build succeeded → interrogate the artifact. Build failed → the error text is all
you have.* They are complementary, not redundant. The arm does only the second half.

**The proposed change:** run construction's existing probes (`ldd_probe`, `import_probe`,
`certified_import_links` — `build.py:989`) on **every turn**, not once at t=0. Plus a presence
census (`pip list` · `dpkg-query` · `ldconfig -p` · `$PATH`) replacing ~936 per-node
`check_command` execs with ~4.

**Three axes, three update rules, never crossed:**
- **STRUCTURE** (nodes/edges) — accumulates monotonically.
- **STATE** (SATISFIED/MISSING/UNKNOWN) — **re-derived every turn, never carried forward** (the
  script re-runs *from base*, so state is a pure function of the *current* script).
- **ATTEMPTS** — accumulate, and **gate the recommendation** (a failed fix must never be
  re-proposed).

State needs **two** inputs, not one — a census alone cannot distinguish these:

```
present?   attempted?     →  state
no         yes            →  MISSING   (we tried; it isn't there)
no         no             →  UNKNOWN   (the build died at line 12 and never reached it)
```

---

## 4. The POC (real Docker, real wheel, real ELF headers)

`src/eval/graph_quality/poc/poc_artifact_enrich.py` — uses the repo's own `ldd_probe`, unmodified.

```
$ pip install opencv-python      → rc 0                 THE BUILD IS GREEN
$ python -c "import cv2"         → ImportError: libxcb.so.1: cannot open shared object file

ARM A (today): the real enrich(), fed the real build log → discovered NOTHING.
               Correctly so: the build SUCCEEDED. No failing command, no error text.

ARM B (design): the real ldd_probe() on the artifact → FOUR missing SystemLibs, each with the
               correct apt fix and a requires-edge from the OWNING package:

    pkg:opencv-python==5.0.0.93 --requires--> syslib:libxcb.so.1          apt:libxcb1
                                --requires--> syslib:libGL.so.1           apt:libgl1
                                --requires--> syslib:libgthread-2.0.so.0  } apt:libglib2.0-0
                                --requires--> syslib:libglib-2.0.so.0     }

    4 nodes → 3 distinct actions (two sonames share one apt package)
    apt-get install -y libgl1 libglib2.0-0 libxcb1   → rc 0
    python -c "import cv2"                            → rc 0, prints 5.0.0
```

A reactive loop finds these **one at a time** — fix `libxcb`, rebuild, discover `libGL`,
rebuild… **four container rebuilds**. The artifact probe found all four in **one probe, zero
rebuilds**.

---

## 5. 🔴 ATTACK THESE. This is what the review is for.

**5.1 Is the problem REAL or RARE?** The POC is **one case**. The corpus says **65% of repairs
are source patches** the graph cannot touch. Quantify the "green build, broken environment" class
in `outputs/repo2run_benchmark/` if you can. **If it is rare, this design is an elegant fix to a
problem that does not matter, and you should say so bluntly.** This is the most likely way the
design is wrong.

**5.2 Is the POC honest, or is it theatre?** Read it closely. Does the seed graph unfairly
handicap Arm A? **Would a competent LLM, shown the raw `ImportError: libGL.so.1`, simply have
written `apt install libgl1` on its own?** If so, the graph adds *nothing* here and the POC proves
only that a probe can find what a model already knows. (Note: the eval found the graph does
*relatively better* on the OS tier — os 1/3 vs python 1/11 — which is the argument that the OS
tier is where models are weakest. Is that argument strong enough to carry this?)

**5.3 Is the COST model right?** The design *adds* a probe to every turn to *save* rebuilds. On a
repo with **815 package nodes**, how expensive is: `ldd` on every installed extension; an isolated
`python -c "import X"` per Import node (164 nodes × ~50 ms)? Could the probe cost more than the
rebuild it saves? The design asserts the census makes this cheap (936 execs → ~4). **Verify or
refute.**

**5.4 What does it BREAK?** Demoting `certify_all` and deriving state from a census — where can
that produce a **FALSE SATISFIED** (the worst error in this system: the graph says "installed" and
the agent stops looking)? The design admits **GLIBC symbol-version mismatch** (`ldd` resolves the
soname, but the symbol version is wrong, so the import still dies). **Find the others.** Note the
mitigation claimed: *function beats presence* — the import probe must override the census, never
the reverse. Is that sufficient?

**5.5 Is the DIAGNOSIS of the 2/14 self-serving?** The design says 2/14 is a *plumbing gap* (the
arm has 1 of construction's 7 instruments), **not** a verdict on graph guidance. That is a
convenient conclusion for someone who wants to keep building the graph. **What would falsify it?**
The design's own stated falsifier: *"wiring `ldd_probe` into the per-turn loop does not move
pre-emption above 2/14."* Is that a fair test?

**5.6 A structural argument the author finds compelling — check it.** `ldd_probe` can only inspect
`.so` files that **exist**. A package that failed to install has none. **The agent's entire job is
to make the failing packages install.** So the packages the agent works on are, by definition, the
ones construction never probed. (Corroborating measurement: the `import:X --requires--> pkg:X`
edge exists **113/164 = 69%** of the time — and that 69% *is the install success rate*, because
`certified_import_links` can only certify dists that actually installed.) **Is this reasoning
sound, or is it a just-so story?**

**5.7 What is the design MISSING ENTIRELY?** What would you do instead?

---

## 6. Known defects the design does NOT fix (deliberately out of scope — challenge that too)

1. **The anti-thrash field is dead code.** `graph_context.py:384` renders
   `tried turn 2: apt-get install -y libpq-dev → FAILED` — and **nothing in the react loop ever
   calls `with_attempt`**. So the graph re-proposes a fix the agent already watched fail. **This is
   the only defect that produces a DEADLOCK rather than a wasted turn.** The design ranks it #1 to
   fix but does not address it.
2. **Capability nodes are not deduplicated by ACTION.** Construction mints them in **five** id
   spaces (`binary:` `aptdep:` `tool:` `syslib:` `linker:`) that alias each other. `pygraphviz`
   predicts **four** MISSING nodes that one `apt-get install libgraphviz-dev` satisfies. The
   renderer stars all four. **This is why star precision measured 0.25.**
3. **`blocks()` is narrower than its own spec** — it blocks only on SystemLib/Tool deps;
   Package/Import/Project/Runtime fall through a bare `return False`. Consequence in **16/16
   graphs**: the Test goal node renders ACTIONABLE while its own `project:` node is UNCERTIFIED
   (Project has **no `check_command` at all**).
4. **`dlopen` is invisible to `DT_NEEDED`.** `ctypes.CDLL("libfoo.so")` declares nothing in the ELF
   header. (`import_probe` is the claimed backstop — verify it actually is.)
5. **The e2e demo is contaminated.** `tests/react_repair/test_graph_arm_e2e.py` **hand-builds** the
   `import:X → pkg:X` edge in its seed graph — an edge real construction produces only 69% of the
   time. Its headline result ("G3 solves in one turn; the G1 control gives up after ten") is
   therefore **weaker than it looks**, and its control agent was deliberately insight-free. **Do
   not credit that demo.**

---

## 7. Provenance — every number above, and where it came from

Measured or read from source during the 2026-07-13/14 session. **Nothing here is inferred.**

- `ldd_probe`'s docstring calls itself *"the primary authoritative source for run-time native-lib
  nodes"*; single call site `build.py:1016` (inside `_python_native_obligations`, `build.py:989`);
  **zero call sites in `src/react_repair/`**.
- `check_command` shapes per tier (Package `pip show`, Import `python -c "import X"`, SystemLib
  `ldconfig -p | grep`, Tool `dpkg -s`/`command -v`, **Project: NONE**): read from the 16 committed
  graphs in `src/eval/graph_quality/graphs/`.
- Node census across those 16 graphs: Package 815, Import 164, Tool 111, Project 16, Runtime 16,
  Test 16, SystemLib 10 (1148 nodes / 2088 edges).
- `import:X → pkg:X` edge coverage **113/164**; 8 packages with no inbound edge: same corpus.
- Capability id-space census (`aptdep` 86, `tool` 11, `binary` 10, `syslib` 10, `linker` 4) and the
  `pygraphviz` four-nodes-one-action case: same corpus.
- Corpus label distribution (3 / 11 / 25 / 72 of 111) and pre-emption 2/14: `src/eval/graph_quality/`.
- The `librabbitmq4` hallucination: `line__lighthouse` round 1, real captured stderr.
- The "last `Collecting X`" owner heuristic returning `pip` / `setuptools` / `mdurl`: measured over
  59 build-stream pairs. (Rejected as unsound.)
- POC output: `src/eval/graph_quality/poc/poc_artifact_enrich.output.txt`.

**Full design:** `docs/superpowers/specs/2026-07-14-unified-graph-enrichment-design.md`
**The eval that produced the numbers:** `docs/superpowers/specs/2026-07-13-graph-quality-eval-design.md`

---

## 8. What a useful review looks like

Cite `file:line`. Quote real data. **Do not flatter.** End with a blunt one-paragraph verdict:
**would you build this, and what would you change first?**

The author's own biggest worry, stated plainly so you can confirm or dismiss it:
**this may be an elegant, well-evidenced fix to a class of failure that is too rare to move the
benchmark — while the two defects that actually make the graph *worse than nothing* (the deadlock
in §6.1 and the four-stars-one-action in §6.2) go unfixed.**
