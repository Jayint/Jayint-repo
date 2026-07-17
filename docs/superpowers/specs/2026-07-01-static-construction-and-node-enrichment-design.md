# Static Construction & Node Enrichment — Discovery, Fix-Memory, Signal Reconnection

> Companion to [2026-06-30-uniform-graph-alignment-design.md](./2026-06-30-uniform-graph-alignment-design.md),
> which deferred "static-analysis / LLM-scan construction" to a later conversation.
> This is that conversation. The alignment doc reshapes the node (`setup_commands`,
> `phase`, `strength`, host-only certify); this doc covers how the graph is
> **discovered and enriched** before and around the repair loop, the **fix/attempt
> memory** carried on a node, and the **signals we already compute but discard**.

## Purpose

Make the initial graph a richer, *honest* prior for environment building — more
than just imports — without ever asserting a native obligation we cannot ground
in an authoritative source. Two motivating gaps from the gate-ladder Stage 2.5
work this closes:

- **Discovery-without-resolution** — opencv's `libGL.so.1` was discovered but
  never resolved to an apt package and installed; the need decayed into an inert
  comment. The fix is completing the *dynamic grounding path*, not predicting it.
- **Repair under-grounding** — a fix that left rc0 but failed certification
  produced no usable evidence. The fix is richer, structured fix-memory on the node.

## Guiding Principle (paper-defensible)

```text
The graph never asserts a native obligation it cannot ground in an authoritative source:
  - the package's own resolved metadata (wheel tags, sdist, Requires-External),
  - the distro's own package index (apt-file / dpkg),
  - the repo's own declarations (Dockerfile, Aptfile, CI, compose), or
  - a real, observed failure.
No benchmark-specific lookup tables. No curated package->syslib map.
```

A hand-curated `package -> syslib` table is rejected: it reads as overfitting to
the eval set and is not defensible in the paper. Every prior must be *derived*
from one of the four sources above.

## Strength Epistemics

Strength (from the alignment doc: `SOFT | HARD`) is assigned by *how the node was
discovered*, and only promoted by stronger evidence:

```text
declared by the repo (requirements/pyproject/lock)      -> hard
repo author env declaration (Dockerfile/Aptfile/CI)      -> hard-ish (high-confidence)
inferred (import without declaration, README prose, LLM)  -> soft (candidate)
runtime/gate failure that names the need                  -> promote soft -> hard
```

`SOFT` gates *blocking*, not *emission*: a soft node with `setup_commands` is
still installed proactively by the spine — it just does not block its dependents
or the gates if it turns out wrong. So a wrong soft prior degrades gracefully to
reactive grounding instead of hard-failing the build.

## Construction Methods (dynamic, layered)

In rough order of trust. Each is per-repo and authoritative — none is a table.

1. **Declared dependencies -> hard.** `requirements*.txt`, `pyproject.toml`,
   `setup.py/cfg`, `Pipfile`, `poetry.lock`, `uv.lock`, plus non-Python manifests
   when relevant. Imports without a declaration are added as **soft** candidates.

2. **Wheel-vs-sdist build oracle (proactive native-need signal).** The resolver
   already knows, per package, whether it resolved to a pure-Python wheel
   (`none-any` -> no native deps), a `manylinux` wheel (native libs auditwheel-
   bundled -> usually no system lib needed), or an **sdist** (no compatible wheel
   -> will compile -> needs toolchain + headers). This is computed today by
   `resolve_lock` and then discarded; see Node Enrichment §1. It is the legitimate,
   derived form of "predict native needs."

3. **sdist build-config mining.** For an sdist, the package's *own* files declare
   build needs: `pyproject.toml [build-system].requires`, `setup.py` `ext_modules`
   / `libraries`, a `Cargo.toml` / `meson.build` / `CMakeLists.txt`. Authoritative,
   per-package.

4. **`Requires-External` / `Requires-Dist` from wheel METADATA.** PEP-345
   `Requires-External` is the standard field for non-Python deps — rare, but ground
   truth when present.

5. **Repo env declarations as evidence (under-weighted previously).** `Dockerfile`
   `RUN apt-get install`, `Aptfile`, `binder/apt.txt`, `.devcontainer`, conda
   `environment.yml`. These are *author declarations*, not prose — high confidence,
   close to hard. Mining them is per-repo and defensible.

6. **CI as the recipe oracle.** `.github/workflows/*` usually contains the
   maintainer-verified setup: apt installs, `services:` containers, env vars, and
   the *actual* test command. Parsing the test job yields a near-ground-truth recipe.

7. **Environment-discovery input — v1 raw files, v2 compact bundle (staged).**
   The LLM needs evidence from the repo's environment-describing files: `README.md`,
   `docs/*`, `docker-compose.yml`, `Dockerfile`, `.github/workflows/*`,
   `.env.example`, `pytest.ini`, `tox.ini`, `noxfile.py`, `conftest.py`,
   `settings.py`, `Makefile`, `scripts/*`, plus the dependency manifests.
   - **v1 (ship first): feed the raw file text.** Hand the model the contents of a
     **bounded allowlist** of these files (never a blind whole-repo dump), under a
     per-file byte cap and a total prompt cap. The LLM extracts soft obligations
     grounded to the **filename** it saw. Crude on tokens, but it gets coverage —
     does reading these files actually help? — without first building a parser per
     file type.
   - **v2 (optimize later): the compact extracted-signal bundle.** Replace raw text
     with extracted signals — ports, service names, env vars, fixture paths, CI
     commands, Dockerfile apt lines, README setup steps, database URLs — each a
     typed hit with file/line provenance and a **source-confidence tag**
     (declaration vs prose). This is the token/precision optimization, and it is the
     construction-time seed of the EvidenceLedger. (`static_collect` /
     `config_scan` / `service_scan` already implement this shape for services/config
     at narrow coverage — v2 is widening their coverage, not inventing the path.)

   Either way the output is **soft**, evidence-grounded, and admitted through the
   pure `patch_gate`. v2 is strictly an efficiency/precision improvement over v1, not
   a behavior change.

8. **LLM soft-candidate stage (widened to soft System/Tool).** Use the LLM for
   what deterministic tools are bad at: services, config/env vars, data assets,
   custom setup, runtime start commands, test-only artifacts — and, newly, **soft
   `SystemLib`/`Tool` hints** mined from author declarations (Dockerfile / CI
   `apt-get install`, base image). Widen `env_classifier._sanitize`'s type allowlist
   (`_KIND_PREFIX`) to admit `SystemLib`/`Tool`, still **forced soft** — so a
   declared `apt-get install libpq-dev` becomes a proactive, non-blocking system
   hint at construction (the cheap libGL-class prior). Two constraints hold firm:
   the **hard spine stays deterministic** (manifest-declared deps remain the
   resolver-pinned hard nodes; this stage is purely additive soft), and the **LLM
   may only ever propose soft** — hard system nodes come only from the deterministic
   resolver or host certification. Initial output is soft and evidence-backed
   (`env_classifier`, Slice C). Soft nodes inform repair; they never block setup.

9. **Read-only probes (raise confidence, do not write State).** Probe the python
   version, whether an import already resolves, whether an apt name exists
   (`apt-cache` after `apt-get update`), whether a referenced fixture file exists.
   These adjust `strength`/evidence only. **Exception:** a probe that runs a node's
   *actual* `check_command` legitimately certifies — if `python3 -c 'import requests'`
   already passes on the base image, the node is `SATISFIED` for free and we skip
   the install. Keep the two kinds distinct so "only the host check writes
   SATISFIED" stays literally true.

## Reactive Grounding — the dynamic native-dep path (the real libGL fix)

When a need surfaces a raw symbol, resolve it against authoritative sources, then
install and re-certify. No table.

```text
ldd on the INSTALLED artifact        -> missing sonames (e.g. libGL.so.1)   [ldd_probe]
apt-file search <soname|header>      -> the providing apt package           [apt_resolve]
build/link/import error mining       -> header (libpq-fe.h) / -lpq / soname [resolve_errors]
pkg-config --exists / --cflags --libs-> linkability as the check_command
```

**Central invariant (the Stage 2.5 fix):** a discovered **hard** need whose
provider has been resolved MUST be emitted as `setup_commands`, installed, and
re-certified — it may never be left as an inert `#@need` comment. Discovery and
resolution are one obligation, not two.

## Fix / Attempt Memory (node-level)

The graph stays a **requirement graph**; attempts are fields/events on the
requirement node, never separate "fix nodes" (which would pollute scheduling).

- **`Attempt` gains `failure_class` + `evidence_ref`.** `outcome` stays
  `{succeeded | failed | unknown}` and means *did the command run* — it is NOT a
  certification verdict. Whether the node is `SATISFIED` remains `certify.py`'s
  call, run after the attempt. (`failure_class` reuses `runtime_classify`'s
  taxonomy — see §Node Enrichment §2 for the shared vocabulary.)
- **Node gains `invalid_commands`,** populated ONLY for *deterministic* failure
  classes (`provider_not_found`, bad syntax) — never transient ones
  (`network_timeout`), or we would permanently ban a command that would have
  worked. This is the anti-thrash memory; it consolidates `known_invalid` into the
  graph and into the repair prompt.
- **Storage: bounded on-node + full in the ledger.** Immutability means each
  attempt clones the node, so keep a bounded recent-attempts summary + the invalid
  set on the node (what scheduling and prompting need); the full blow-by-blow lives
  in the EvidenceLedger by `evidence_ref`.
- **Node-vs-attempt litmus:** *does the outcome have its own `check_command`?* If
  the fix yields an independently-certifiable persistent fact (`createdb test_db`
  -> a `data:` node checked by `psql -lqt | grep test_db`; `npm run build` -> a
  `data:dist` node checked by `test -d dist`), it is a **node**. If it is only a
  way to flip another node's check (a misspelled apt name), it is an **attempt**.
  This is exactly the alignment doc's central invariant, so no new principle.
- **Repair prompt surfaces:** the target node, its current `setup_commands`, the
  invalid prior commands *with their failure_class*, and the evidence refs.

## Node Schema Enrichment (reconnect computed-but-discarded signals)

An audit of `resolve_lock` / `apt_resolve` / `apt_verify` / `ldd_probe` /
`relink` / `resolve_errors` found several authoritative signals computed and then
thrown away. The node already has a free-form `data: dict` that the resolution/
probe modules currently never write to — most reconnections land there.

**1. Wheel platform / manylinux tags -> the build oracle (behavioral, reconnect now).**
`resolve_lock` extracts the tags to test installability, then keeps only the
filename (`Node.artifact`) and a single `build_from_source` bool. Reconnect the
tags so the graph can distinguish **pure-Python / manylinux / sdist** and decide
*whether to hunt for syslibs at all*. Land as `data["wheel_tags"]` (+ a derived
`data["native_profile"]`).

**2. Unified `failure_class` (behavioral, reconnect now).** `resolve_errors`
distinguishes registry-miss / all-yanked / python-incompatible / build-failure,
then collapses all four to bare `MISSING`. These demand different repairs (change
Python vs pin older vs add toolchain). Populate the **same `failure_class`
taxonomy** used by `Attempt` — the resolver becomes its first producer. This is
the one signal that earns a shared first-class concept (carried on `Attempt`, and
referenced on the node's resolution evidence).

**3. Provenance -> the `data` bag (no new fields).** Persist what is currently
discarded for repair evidence and reproducibility:
`data["apt_source"]` (curated vs `apt-file` vs virtual-provider vs t64 remap),
`data["wheel_tags"]`, `data["extension_so_paths"]` (the ldd input set, so repair
can re-`ldd` the exact artifacts to confirm a fix), `data["unresolved_category"]`,
`data["marker_fork_alternatives"]`.

**4. `provides` field — DROPPED for now.** Considered, then rejected as
over-engineering. In the common case it is redundant with **naming a node after
its capability** (`syslib:libGL.so.1`) + an edge: the symbol from an error already
equals the node id, so match by id and connect with an edge — no extra field.
`provides` only earns its place in the *one-provider-many-symbols* case
(`libpq-dev` provides `libpq.so.5` + `pg_config` + `libpq-fe.h`), as a
dedup/aliasing index. Defer until that dedup actually hurts. **Net new first-class
fields from this doc: only `failure_class` (shared with `Attempt`); everything
else rides in `data`.**

## Gate Nodes

Unchanged from the alignment doc and the gate-ladder decision: **no
`NodeType.GATE`**. Gate failure still promotes soft nodes — the host runs the gate
check, routes the failure to repair, repair promotes — without making gates nodes.

## What This Closes

```text
Stage 2.5 (A) discovery-without-resolution
  -> reactive grounding completes: ldd -> apt-file -> install -> re-certify,
     under the "hard need + resolved provider MUST be installed" invariant.
Stage 2.5 (B) repair under-grounding
  -> richer node evidence: failure_class (resolver + runtime), provenance in data,
     invalid_commands, bounded attempts + ledger refs in the repair prompt.
```

## Explicitly Deferred

- **v2 compact extracted-signal bundle** — the per-file-type parsers and
  source-confidence tagging that replace v1's raw-file feeding (construction method
  §7). An efficiency/precision optimization, taken only after v1 shows the coverage
  helps.
- **`provides` field** — until the one-package-many-symbols dedup bites.
- **EvidenceLedger full implementation** — refs are introduced here; the ledger's
  storage/format (inline `evidence` -> structured refs) is its own change.
- **Exhaustive / distro-generated mapping and conda-forge recipe cross-reference**
  — out of scope; the dynamic methods above are sufficient and more defensible.

## Review Corrections (2026-07-01)

A code-grounded review surfaced fixes that **supersede the body where they conflict**.

- **The alignment doc is a HARD PREREQUISITE.** "forced soft" and the proactive-syslib
  goal are unenforceable until `Node.strength` exists AND `emit` gates on
  `setup_commands` (alignment-doc keystone). Land the alignment doc first; the widening
  here is unsafe before then.
- **An LLM-proposed SystemLib/Tool must reach an installable command, or it backfires.**
  As-is, `_sanitize` drops providers and `NodeSpec` has no `chosen_fix`, so a proposed
  `syslib:` node gets `chosen_fix=None` → `emit._is_emittable` rejects it → it becomes a
  hard-blocking frontier node (the exact Stage 2.5 bug). Fix: after admit, a deterministic
  step derives `chosen_fix = apt:<name>` from the node id (`syslib:libpq-dev` →
  `apt:libpq-dev`), the populator fills `setup_commands`, and it stays SOFT. Combined with
  the alignment keystone, the node is then emittable AND non-blocking.
- **The widening is a SYSTEM-PROMPT change, not `_KIND_PREFIX`.** `_KIND_PREFIX` already
  contains `syslib:`/`tool:`. Update `env_classifier._SYSTEM_PROMPT` to add `SystemLib`/`Tool`
  to the allowed types AND extend layer guidance (`SystemLib → system`, `Tool → toolchain`;
  the prompt currently says only `{services,config}`). Consider a type/layer consistency
  check in `validate_proposal`.
- **v1 raw-file evidence needs synthetic ids.** `_sanitize` grounds via
  `evidence_ref in bundle_ids` where ids are synthetic (`ci.00`, …). Raw-file v1 must
  assign each file a synthetic id and pass the filename→id map to the model so it cites the
  id; grounding to a bare filename drops every node.
- **`failure_class` is a NEW enum to DEFINE, not reuse.** No shared taxonomy exists
  (`runtime_classify` uses `failure_type` strings; `resolve_errors` uses a typed
  `ResolverDiagnosis`). Define `FailureClass` in `schema.py` with at least:
  `provider_not_found`, `version_conflict`, `python_incompatible`, `build_failure`,
  `native_library_missing`, `module_not_found`, `tool_not_found`, `config_missing`,
  `network_timeout`. Map resolver categories + runtime strings into it.
- **Wheel tags are a NEW parser, not a reconnect.** `resolve_lock` does string inspection
  (`endswith("-none-any.whl")`), never decomposing PEP 425 tags. Add a parser
  (`packaging.utils.parse_wheel_filename`, naive split fallback) to produce
  `data["wheel_tags"]` / `data["native_profile"]`.
- **Reactive-grounding invariant is conditional — close the escape hatch.** When
  `resolve_soname_apt` returns `(None, "unresolved")` the libGL bug recurs. Promote the
  lazy `apt-file` path (`ldd_probe` option B) so unknown sonames still resolve, as part of
  this work.
- **`invalid_commands` vs `repair_loop.known_invalid`.** `known_invalid` stays the
  call-local cache; the per-node `invalid_commands` is the durable authority the loop seeds
  `ki` from at start. Only deterministic failure classes populate it.
- **`node.data` is frozen** (`MappingProxyType`). All enrichments use
  `replace(node, data={**node.data, ...})`.
- **Phase-3 entry condition — `_graph_hash` must include `setup_commands` before LLM patches write
  custom commands.** Phase 1 (uniform-graph populator/renderer) deliberately left
  `build_script._graph_hash` hashing only `(id, version, chosen_fix)`. That is safe while
  `populate_setup_commands` is the SOLE writer of `setup_commands` — it never changes a hashed field,
  so equal-hash graphs still render identically. The node-enrichment / LLM-patch work in THIS doc is
  what first lets a caller supply arbitrary `setup_commands`; at that point two nodes identical in
  `(id, version, chosen_fix)` but differing in `setup_commands` collide to one manifest hash and
  stale-artifact detection misses the change. Add `setup_commands` to the hash payload as the first
  step of that work. (Cross-ref: alignment spec "Phase 1 Landed — Phase 2/3 Entry Conditions".)
