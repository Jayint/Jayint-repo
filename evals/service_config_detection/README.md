# Service/Config detection — isolated eval

Measures how well the service/config detection module identifies **services** and
**config bindings**, exercising it *by itself* — no Docker, no `certify`, no repair loop,
no build-phase agent. The two functions under test are the whole seam:

- `static_collect.collect_static_evidence(repo_path, graph)` — pure, deterministic.
- `env_classifier.classify(graph, repo_path)` — the LLM classifier, driven here by a
  **canned** `complete_fn` so its deterministic guards are measured without a real model.

## The two levels (both LLM-free, CI-runnable)

**Level 1 — evidence layer** (`EVIDENCE_CASES`). Each case materializes a tiny repo and
asserts which evidence hits must / must not appear (`service_binding`, `dev_mock`,
`dev_testcontainers`, `compose_service`, `ci_service`). Fully deterministic → exact
precision/recall. This is the raw-signal recall of the scanners.

**Level 2 — guard layer** (`GUARD_CASES`). Each case feeds a *crafted* LLM proposal (the
proposal cites evidence by kind; the runner resolves the real evidence id) through
`classify` and asserts the resulting nodes/edges. This measures the judgment logic — the
three deterministic guards — with zero model cost:
- guard #1: recipes rendered from `(kind, params)`; `start_recipe` only for `confirmed`;
  `binding` flagged only when a bind step renders.
- guard #2: a `confirmed` claim is downgraded to `inferred` on weak evidence or a
  same-kind `dev_mock`.
- guard #3: (kind comes from the DSN scheme; adopted by the LLM — see the spec).

The corpus is **adversarial-first**: built around the four real-world failure modes the
research surfaced — the mock trap, DSN-scheme ambiguity, the testcontainers YAML blind
spot, and sidecar-missed creds (a documented gap).

## Run it

```bash
# full run, writes one raw JSON artifact per case + a summary (analyze downstream)
python3 evals/service_config_detection/harness.py

# summary only
python3 evals/service_config_detection/harness.py --quiet

# as a CI regression gate
python3 -m pytest tests/evals/test_detection_corpus.py -q
```

Artifacts land in `evals/service_config_detection/artifacts/` (git-ignored): raw
`{expected, predicted, matched, missing, spurious}` per case, for your own tooling to
aggregate — no conclusions are baked in.

A `known_gap:` case encodes *current* (limited) behavior on purpose; when the gap is
closed, its assertion flips and should be tightened — that flip is the signal.

## Level 3 — real repos, real model (`level3.py`)

Runs the SAME seam (`collect_static_evidence` -> `classify`) on real repo checkouts, with
a real model, **still no build agent**. `level3_labels.py` holds hand ground-truth (with
provenance) for the repos we can honestly label; `level3.py` also has an optional LLM-judge
lens (diagnostic, never the headline).

```bash
# zero-cost: deterministic evidence layer only, on real repos (what each repo surfaces)
python3 evals/service_config_detection/level3.py --repos ~/rat-bench-integration/workplace --evidence-only

# wiring smoke (canned empty LLM, no API)
python3 evals/service_config_detection/level3.py --repos <dir> --mock

# real model (spends tokens; reads env — never hard-code the key):
OPENROUTER_API_KEY=... OPENROUTER_API_BASE=https://openrouter.ai/api/v1 \
  python3 evals/service_config_detection/level3.py --repos <dir> \
    --model anthropic/claude-sonnet-4.5 --judge
```

**Ground-truth honesty.** The seeded labels are the research's *service-tagged-but-mocked*
repos (`proxy_pool`, `LibreTranslate`, `memU-server`), so `services_truly_required` is empty
for all three — this set probes **precision + the guard-#2 mock-downgrade calibration**, not
recall. A recall probe needs repos whose tests genuinely dial a live service (e.g.
`n8n-autoscaling` — add a label once verified). Other ground-truth options not yet wired:
a held-out compose oracle, or a run-once network-capture gold label (what the tests actually
dial — the strongest signal). Level-1/2 fixtures are synthetic (exact but unrealistic); Level 3
is realistic but its ground truth is only as good as the labels/judge.
