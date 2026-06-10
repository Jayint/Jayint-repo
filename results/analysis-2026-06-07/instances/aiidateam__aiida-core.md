# aiidateam/aiida-core

- DA pass-rate: 0% (0/0 tests, no Dockerfile) | RAT pass-rate: 79.49% (2888/3739 tests) | bucket: DA_LOSS
- DA build_success/test_success: False/False | error_breakdown: no_dockerfile
- DA PR: N/A | RAT PR: N/A

## Failure stage & category
**Stage**: docker_build
**Category**: scoring_or_infra_artifact

## Root cause (why DA lost)

DockerAgent's execution halted mid-build due to Docker daemon disk exhaustion. During Step 1 (environment configuration), after running `pytest --collect-only` to verify the test suite, the containerd daemon attempted to commit the container state with `docker commit`. The commit failed with "no space left on device" in `/var/lib/containerd/io.containerd.content.v1.content/ingest/`, preventing the container snapshot from being saved. This cascaded to Step 2 (Dockerfile extraction) failing to retrieve any Dockerfile, leaving the agent with no artifact to generate test commands from, so the evaluation was skipped entirely.

## What RAT did differently

RAT succeeded by running in an environment with sufficient disk space. Its strategy was straightforward and resilient:
- `pip install -e ".[dev]"` (cmd 14) to install the package in editable mode with dev dependencies
- Iteratively installed test dependencies as import errors surfaced (sphinx, pgtest, flask-restful, pytest-cov, pytest-flaky, etc. in cmds 16–29)
- Successfully ran `pytest --collect-only` and then full `pytest` execution, achieving 79.49% pass rate

No agent-level logic difference between RAT and DA; both would have followed the same install-and-test flow if DA's container had not run out of disk space.

## Evidence

**DA run.log** (lines 1338–1345):
```
An error occurred during execution: 500 Server Error for http+docker://localhost/v1.54/commit?container=5de5600890fde8706e50c8653f561dd3310c5c8238417bc4dfd5c2264421fd54&pause=True: Internal Server Error ("failed to export layer: CreateDiff: mount callback failed on /var/lib/containerd/tmpmounts/containerd-mount2444579359: mount callback failed on /var/lib/containerd/tmpmounts/containerd-mount1213539182: failed to write compressed diff: failed to create diff tar stream: failed to copy: /var/lib/containerd/tmpmounts/containerd-mount1213539182/root/.cache/pip/http-v2/8/1/e/6/5/81e6544d105932a41e988533bd6823780d1513fd622336c4a97f3ced.body: write /var/lib/containerd/io.containerd.content.v1.content/ingest/48a8baabb981c065c49c05ce9c37f09a0446ee9953a55783eed79249b2d52694/data: no space left on device")
[Step 2/4] Extracting Dockerfile...
✗ Dockerfile not found
```

**DA _result_row.json**:
```json
"status": "error",
"failure_reason": "no_dockerfile",
"error": "agent produced no Dockerfile: Dockerfile generation failed",
"pytest_pass_rate": 0.0,
"pytest_total_tests": 0
```

**RAT _result_row.json**:
```json
"status": "success",
"pytest_pass_rate": 0.7949,
"pytest_total_tests": 3739,
"pytest_passed": 2888
```

**RAT inner_commands.json** (cmd 14, successful):
```
pip install -e ".[dev]" -q 2>&1 | tail -30 → rc 0
```

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

This is **not a DA code deficiency**. The failure is a Docker daemon resource exhaustion issue on the host environment running the benchmark. 

**For the benchmark infrastructure**:
- Ensure test VMs/containers have sufficient disk space (recommend >50 GB free before starting, monitor during runs)
- Set a disk space check in the harness before launching any docker builds
- Consider layered cleanup (delete old run artifacts, prune dangling Docker images) between runs

**For DA robustness** (optional enhancement):
- Add a pre-flight disk space check in `agent.py` before initializing the Docker environment, fail-fast with a clear message if <5 GB free
- Add retry logic for transient Docker commit errors (though root cause here is fundamental resource exhaustion, not transient failure)
