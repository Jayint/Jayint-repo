# Graph-Guided Execute Agent

For the complete Chinese workflow, implementation mapping, experiment commands,
and smoke evidence, see `docs/graph_execute_agent_workflow_zh.md`.

This document describes the canonical incremental v3 execution path implemented
by `scripts/run_v3_e2e.py --execution-mode incremental`.

## Core separation

- **DepGraph** states what must hold. Nodes carry obligations, dependencies,
  providers, host checks, and evidence.
- **Execution Plan** states how to attempt it. It is a deterministic projection
  of the DepGraph and governed manual blocks into ordered `Block` objects.
- **GraphExecuteAgent** diagnoses one failed execution packet. It can issue
  read-only probes and propose one typed `PatchProposal`; it cannot mutate the
  container, certify a node, or declare success.
- **PatchGate** validates and admits graph/provider/block changes.
- **Host certifier** is the only writer of `SATISFIED`/`MISSING` node state.
- **Fresh replay** is the terminal reproducibility certificate.

Repairs are substitutions, not an append-only command history. A graph-derived
block is corrected by overriding its provider, which invalidates the compiled
command. A governed manual block is corrected with `replace_block` at the same
block id. The next plan therefore contains one active strategy for the failed
obligation, and checkpoint invalidation starts at the causal change.

## Workflow

1. Build and host-certify the initial DepGraph.
2. Compile the complete graph-linked Execution Plan. The plan and final
   `setup.sh` share command generation and layer order.
3. Execute blocks in order. After each block, host-certify its target nodes.
4. At layer boundaries, periodic boundaries, and expensive blocks, re-certify
   the executed prefix and commit a named Docker checkpoint.
5. On failure, construct an execution packet containing:
   - the target obligation and local requirement slice;
   - failed block id, wave, commands, targets, providers, and checks;
   - localized command output and citable evidence;
   - rejected providers/commands and environment constraints.
6. Let GraphExecuteAgent use bounded read-only diagnostics and propose a typed
   patch. PatchGate either rejects it with structured errors or admits it.
7. Recompile the plan. Compare semantic block signatures and restore the
   longest checkpoint whose prefix is still identical. Drop invalid suffix
   checkpoints, then execute only the changed suffix.
8. When the graph frontier and tests are green, discard search state as proof:
   reset to the raw base image, replay the complete rendered `setup.sh`,
   host-certify all reciped nodes, and rerun the anti-hollow test gate.

## Checkpoint validity

A checkpoint signature includes block id, wave, commands, targets, providers,
checks, and mutation semantics. It intentionally excludes dynamic evidence:
a failed host check may update diagnostic evidence without changing the build
plan, and that must not invalidate an otherwise identical prefix.

Checkpoints optimize search only. No successful result is reported without a
raw-base full replay. This preserves the reproducibility invariant while
removing repeated prefix work from repair iterations.

## Trace evidence

`RunTrace.incremental` records each search execution:

- plan hash and total block count;
- reused prefix length;
- executed block ids;
- restored and newly created checkpoints;
- failed block id and setup return code.

`RunTrace.replays[-1]` remains the final proof record: setup return code,
host-certified and unsatisfied nodes, and verified test result.

Useful efficiency metrics for experiments are:

- `reuse_ratio = sum(reused_blocks) / sum(total_blocks)`;
- avoided block executions versus the `fresh` ablation;
- wall-clock time and LLM turns to terminal certificate;
- checkpoint storage overhead and invalidated suffix length;
- final RATBench `pytest_pass_rate` and build success.

## Ablation

The historical per-cycle full replay remains available as:

```bash
python scripts/run_v3_e2e.py REPO --execution-mode fresh
```

The canonical graph-guided method is:

```bash
python scripts/run_v3_e2e.py REPO \
  --execution-mode incremental \
  --max-cycles 30
```
