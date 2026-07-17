# Gate-Ladder Stage 2.5 — Problem Statement: Closing the SystemLib Repair Loop

> **Status:** PROBLEM STATEMENT (not yet a design). Surfaced by the Stage-2 real-container
> e2e on 2026-06-30. Input to a future brainstorm → spec → plan cycle.
> **Predecessors:** Stage 1 (two-gate observability, landed), Stage 2 (binding dep-spine
> installability, landed `dea11ba..a1ef016`, default-off). See
> `2026-06-29-gate-ladder-outer-loop-design.md` and
> `2026-06-30-gate-ladder-stage2-binding-installability-design.md`.

## 1. What Stage 2 proved (so this problem is well-founded)

Real-container e2e (Docker 28.0.4 + OpenRouter gpt-4o) confirmed the Stage-2 mechanism works
and behaves honestly:

- **Primitives:** `reset_to_base` + `run_install_script` (ERR-trap localization) +
  `certify_reciped_only` work against a real container (install→certify SATISFIED; failed
  install→localized command + node stays MISSING).
- **microsearch (42-node real graph):** ran with no fail-fast false-trip; 40/42 resolved;
  `planner_giveup` (NOT a hollow `done`) — the dep-spine scope boundary, exactly as specified.
- **opencv (instrumented, syslibs stripped to force repair):** all three sub-systems fired —
  the 2-gate observer reported both gates, the base file rendered+ran from base 10× (all rc 0),
  and the loop re-discovered the missing syslib. Anti-hollow held: the graph distinguished
  `pkg:opencv-python` (pip-installed, SATISFIED) from `import:cv2` (MISSING — not importable),
  and did not declare success.

## 2. The problem (the bug the e2e found)

On the canonical cv2 case the loop **does not converge**: it re-discovers the missing system
library but never installs it, so `import cv2` stays broken and the loop exhausts its repair
budget and gives up.

Observed (opencv `verify_three.py`): after stripping `syslib:libgl1` + `syslib:libglib2.0-0`,
the loop re-added `syslib:libGL.so.1`, but the rendered `setup.sh`'s
`apt-line progression = [False ×10]` — **no `apt-get install` line was ever emitted for it** —
and the node stayed `MISSING` across all 10 cycles → `planner_giveup`.

### Root cause (grounded in code)

`_is_reciped` (`python_deps/depgraph/emit.py`) treats a `SYSTEM_LIB` as installable **only** if
`node.chosen_fix` starts with `"apt:"`. Otherwise `render_build_script` emits it as a `#@need`
(advisory comment, **no executable install line**). The re-discovered `syslib:libGL.so.1` never
acquired an `apt:` `chosen_fix`, so it was an inert obligation the renderer could not install.

Two **linked** gaps produce this:

- **(A) Discovery without resolution.** The node returned as a *soname* (`syslib:libGL.so.1`,
  not the apt name `syslib:libgl1`) — the signature of the runtime error-string classifier,
  which adds discovery-only nodes with **no `chosen_fix`**. Nothing maps `libGL.so.1 → apt:libgl1`,
  so the node is never reciped, never installed.
- **(B) Repair under-grounding (the dominant gap).** Stage 2 builds the install-failure
  `EvidenceBundle` only when **install** fails (`rc != 0`). The cv2 case is the *rc0-but-certify-
  fails* mode: the wheel installs fine (`rc 0`), but `import cv2` fails on the missing `.so`. The
  binding branch then passes `bundle=None`, so the repair scope carries **no failure output and
  no citable evidence id** — and PatchGate (correctly) refuses any new requirement/provider that
  cannot cite evidence. So the typed LLM repair cannot admit the `apt:libgl1` fix even if it
  "knows" the answer. **The most common system-library failure mode gives the repair path the
  least information.**

## 3. Why this matters

Autonomous repair of missing **system libraries** is arguably the headline capability of the
whole approach (it is what separates "pip installed" from "actually importable/runnable"). Today
the loop can *notice* a missing `.so` but cannot *resolve and install* it. "Discovered" ≠ "fixed."
Until this closes, binding installability cannot converge on the very class of repos it most
needs to (anything with a compiled-extension dependency: opencv, psycopg2, lxml, pyarrow, …).

This is also the precise gap that **Docker-free unit tests could not catch**: they feed canned
`InstallResult`s and fake the LLM, so the certify-fail → no-evidence → can't-repair →
non-convergence chain is invisible without a real wheel + real import failure + real PatchGate.

## 4. Scope of Stage 2.5 (problem boundary — to be designed)

In scope (the convergence fix):

1. **Capture certify-failure evidence.** When `certify_reciped_only` finds a reciped node still
   `MISSING`, surface the failing check's stderr (e.g. `libGL.so.1: cannot open shared object`)
   into the repair scope as a citable evidence id — symmetric with the install-failure
   `EvidenceBundle` already wired. (Directly fixes gap B; likely the highest-leverage change.)
2. **Resolve discovered syslibs to apt providers.** A discovered soname (`libGL.so.1`) must
   acquire an `apt:` `chosen_fix` — via the LLM proposing a provider against the now-available
   evidence, and/or a deterministic soname→package resolution step — so it becomes reciped and
   the renderer emits its install line. (Fixes gap A.)
3. **Project install** (`pip install -e .`) so project-style repos (microsearch) can reach a
   passing test gate — the other half of the dep-spine boundary microsearch hit.

Explicitly OUT of scope (other stages):

- Installability **done-gate enforcement** (block `done` while a reciped node is MISSING) — that
  is **Stage 3** (changes scheduler/termination semantics; has its own honest-success tension).
- `#@need`/`#@block` (CONFIG/SERVICE/DATA_ASSET) certification beyond what dep-spine needs.

## 5. Open questions for the brainstorm

- Should syslib resolution be deterministic (a soname→apt map / `apt-file`-style lookup) or
  LLM-proposed-with-evidence, or both (deterministic first, LLM fallback)?
- Should certify-failure evidence capture run the check a second time to grab stderr, or should
  `certify_refresh` retain per-node check output during its pass?
- How to keep anti-hollow intact: certify-failure evidence must be host-produced (the host ran
  the check), and the resolved provider must still be host-certified after install — no shortcut
  that lets the LLM assert the fix worked.

## 6. Reproduction

```
# real container + LLM
cd /Users/john/john-planner-v3
set -a; source .env; set +a
export PYTHONPATH=$PWD:$PWD/src LLM_MODEL=openai/gpt-4o
python3 <scratchpad>/sdd-stage2/verify_three.py     # opencv, syslibs stripped, gates on
# Expect today: stop_reason=planner_giveup; apt-line progression all False;
#   syslib:libGL.so.1 stays MISSING. After Stage 2.5: should converge (apt line appears,
#   import:cv2 SATISFIED, testability True).
```
