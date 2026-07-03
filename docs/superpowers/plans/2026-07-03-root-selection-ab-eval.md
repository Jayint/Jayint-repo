# Plan — A/B eval: imports-as-generator vs imports-as-verifier root selection

## Question
Two candidate architectures for the Python-language layer's **root selection**:

- **Generator (current):** `roots = declared ∪ mapped-scanned-imports`. An undeclared
  import that maps to a dist (curated table / declared-match) is **added** to the install set.
- **Verifier (proposed):** `roots = declared only`. Imports never enter roots; they feed the
  demand/alignment check and post-install relink. Undeclared imports are flagged (or handled
  by an explicit, separate repair phase), never auto-added.

Which is "more correct"? We measure, not argue.

## Key realization — zero production change
`select_roots` (roots.py) already **tags** every root: declared deps carry `import_id=None`,
scan-gap-fill imports carry the import node id. So the two architectures are derivable from ONE
unchanged `select_roots` call:

- generator roots = full output
- verifier roots  = `[dist for (import_id, dist) in roots if import_id is None]`
- **divergence**   = `[(import_id, dist) for ... if import_id is not None]` = exactly the
  packages the generator adds and the verifier omits.

No flag-gate in the paper path (honours "one clean path"). The A/B lives entirely in the eval.

## Where the two can even differ
Both install `closure(declared)`. They diverge **only** on the divergence set above:
`imported ∧ undeclared ∧ maps-to-a-dist`. Everywhere a repo declares its deps, the roots — and
the produced env — are identical. So a raw pass_rate comparison mostly TIES; the discriminating
signal is (A) adjudicating the divergence set against gold labels [pure, no Docker], and
(B) fault injection on the divergence cell [container].

## Track A — divergence adjudication (this deliverable; pure, no Docker)
For each repo in a hand-labelled known-answer corpus (the 16 live-probed repos), run
`select_roots`, take the divergence set, and classify each add against gold:

- **good add** — a genuinely-needed runtime dep the repo under-declared and that is not
  otherwise present ⇒ generator's *benefit* (verifier would miss it without repair).
- **bad add** — benign: optional/guarded, TYPE_CHECKING-only, local, tooling, transitive-only,
  or alias-of-an-already-declared dist ⇒ generator's *cost* (over-install / wrong).

Verdict: if `bad ≥ good` the verifier (cleaner + honest) wins with no loss; if `good > bad` the
generator's auto-add earns its keep. Also report the **unresolved** externals (mapped by
neither) split by gold label — a gap NEITHER architecture catches motivates the completeness gate.

Gold labels come from the 4 live-probe subagent reports (each under/over item hand-classified
with source evidence). Only NON-declared externals need a label; declared imports are covered by
definition.

## Track B — fault injection (spec only, next deliverable; container)
Under-declaration is rare in the wild, so manufacture it: delete one declared dep from a
well-declared repo's manifest and measure against the known ground truth (the deleted dep):
detection recall, recovery correctness (generator curated auto-add vs. verifier+repair), and
precision (nothing else added/flagged). Run with an alias-named dep (`cv2`/opencv) and an
identity-named one (`tqdm`) — the two cells where curated-table vs honest-flag behaviour splits.
Arbiter: fresh `-slim` replay (reuse `coverage.run_execution_probe`).

## Falsifiability
The eval must be able to show the CURRENT design wins: if injected under-declarations are common
and curated auto-add reliably rescues them with few bad adds, imports-as-generator is the better
engineering choice and "cleaner" loses on the metric that matters.

## Files
- `scripts/eval/graph_fidelity/root_selection_ab.py` — pure partition + adjudication + runner (new).
- `scripts/eval/graph_fidelity/ab_gold_labels.py` — the 16-repo gold labels + provenance (new).
- `tests/eval/graph_fidelity/test_root_selection_ab.py` — unit tests for the pure logic (new).
- Corpus clones: reuse the live-probe clones; a manifest allows re-clone for reproducibility.

## Constraints
Construction-only (no build-agent phase). Commit locally only, never push. TDD.
