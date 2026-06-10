# bruin-data/ingestr

- DA pass-rate: 0/0 (build failed) | RAT pass-rate: 0/0 (test deficient) | bucket: BOTH_FAIL
- DA build_success: False | RAT success: True
- DA error_breakdown: docker build failed (Go not installed in base image)

## Failure stage & category

**DA**: test_execution / docker_build_failed
**RAT**: test_collection_error (no pytest tests collected, but test command executed)

## Root cause (why DA lost)

DA misclassified the repository as a **Go project** and generated a Dockerfile with `RUN cd /testbed && go run cmd/genregistry/main.go` from a Python 3.11 base image that lacks Go runtime. The build immediately failed with `go: not found` (exit code 127). The actual repository is a **hybrid Node+Go project**: the test suite is delivered via Node/Vitepress (docs build as test), while Go code exists but is not the test path. DA failed at the critical Dockerfile generation step before any tests could run, while RAT correctly identified the Node-based test path and successfully executed the test command (yielding 0 tests as expected, per repo's actual test deficiency).

## What RAT did differently

RAT correctly identified and executed:
- **Language detection**: Node.js (not Go)
- **Test framework**: `npm run docs:build` → Vitepress documentation build as the executable test path
- **Setup sequence**:
  1. Executed `npm install` (via `run-npm-install`)
  2. Discovered that bare `npm test` failed (rc=1)
  3. Identified the working test command: `npm run docs:build` (rc=0)
  4. Modified `package.json` to add `"test": "vitepress build docs"` so subsequent `npm test` invocations work
  5. Final test command: `npm run test` (which invokes the Vitepress build)

DA attempted:
- `go run cmd/genregistry/main.go` as a build setup command (from Python base, Go not installed)
- `go test -count=0 ./...` as the test command (matching the Go test pattern, but irrelevant without Go runtime)

## Evidence

**DA Dockerfile generation** (from `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/bruin-data/ingestr/bruin-data__ingestr.json`):
```dockerfile
RUN cd /testbed && go run cmd/genregistry/main.go
```

**Docker build failure** (from run.log lines 1162–1168):
```
[Self-Verify] Round 0: building clean-room image…
[Self-Verify] Build failed (rc=1).
...
go: not found
```

**DA language detection**: Language field = `"python"` (incorrect; should be Node)

**DA test command**: `['go test -count=0 ./... 2>&1']` (Go syntax, not Node)

**RAT commands executed** (from outer_commands.json):
```
run-npm-install → rc 0
python3 /home/tools/run_npm_test.py → rc 1  (initial failure)
npm run docs:build 2>&1 → rc 0  (discovered working path)
sed -i 's/"docs:preview": "vitepress preview docs"/"docs:preview": "vitepress preview docs",\n    "test": "vitepress build docs"/' /repo/package.json → rc 0
python3 /home/tools/run_npm_test.py → rc 0  (final success after modification)
```

**RAT success marker**: `"success": true` in `_result_row.json` (reached executable test phase)

**DA skip_evaluation**: `true` (eval script never generated due to build failure)

## Fix recommendation

1. **Agent language detection** (`src/synthesizer.py`): When a repository has both `go.mod` and `package.json`, prioritize the **test framework** over source language. Check CI workflows (`.github/workflows/*.yml`) for the canonical test command before assuming source language dominates.

2. **Dockerfile base image selection**: If Go is required, ensure the base image includes Go runtime or add explicit Go installation. DA selected Python 3.11 as base despite detecting Go commands—this is a base-image-selection bug independent of language detection.

3. **Build command validation during synthesis**: Before finalizing a build recipe, validate that the Dockerfile successfully compiles with the chosen base image. DA's self-verify loop caught the error but did not repair the recipe (dropped back to original broken state). Implement fallback: if `go run` is detected in verified commands but Go is not installed, either:
   - Install Go in the Dockerfile, OR
   - Drop Go-only commands if an alternative test path is available

4. **Hybrid project handling**: Implement logic to detect and prefer the actual **test execution path** (here: Vitepress docs build via npm) over **build artifacts** (here: Go registry generation). The Go code is infrastructure; the test is Node-based.
