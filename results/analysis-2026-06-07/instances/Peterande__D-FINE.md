# Peterande/D-FINE

- DA pass-rate: 0/1 (0%) | RAT pass-rate: 2/2 (100%) | bucket: DA_LOSS
- DA build_success/test_success: False / False | error_breakdown: ModuleNotFoundError (faster_coco_eval module missing, never got tested)

## Failure stage & category

**Stage:** docker_build  
**Category:** docker_build_failed

## Root cause (why DA lost)

DA's agent successfully installed requirements.txt in a python:3.11 base container (Step 7), then attempted to install additional test packages (pytest, opencv-python, pycocotools, etc.) in Step 10. During that second pip install, the docker commit operation exhausted disk space with error `"no space left on device"` on `/var/lib/containerd`. The container was cleaned up, leaving zero verified test commands. The Verification Bundle was empty, so evaluation was skipped entirely, yielding 0 test passes and a ModuleNotFoundError on faster_coco_eval (which was not verified to work).

## What RAT did differently

- **Image selection:** RAT selected `pytorch/pytorch:2.12.0-cuda12.6-cudnn9-devel` (1.6 GB pre-built PyTorch image) rather than starting from `python:3.11` (lightweight) and installing torch+cuda from wheels (~2.5 GB downloads).
- **Installation strategy:** RAT's docker build Dockerfile (cached, SAME layers across runs) pre-installs pytest, sets up pip mirrors, and mounts `/repo` read-only; all dependency installs happen at container execution time via the `run-pytest` wrapper, not during docker commit snapshots.
- **No docker commit per package:** DA took snapshots after EVERY command with `docker commit`, which copies entire container filesystems into image layers. With large packages like torch (532 MB wheel) + cuda packages (~1.6 GB), multiple snapshots exhausted available disk. RAT builds once with a fixed Dockerfile and runs tests inside that single image.
- **Verified test commands:** RAT reported 2 passing tests collected and executed. DA's Verification Bundle never populated any test commands, so the evaluation script was empty.

## Evidence

**DA failure:**
- File: `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/Peterande/D-FINE/run.log` line 1318
  - `"failed to export layer: CreateDiff: mount callback failed on /var/lib/containerd/tmpmounts/containerd-mount1211478035: failed to write compressed diff: failed to create diff tar stream: failed to copy: /var/lib/containerd/tmpmounts/containerd-mount584778680/usr/local/lib/python3.11/site-packages/torch/lib/libtorch_cpu.so: write /var/lib/containerd/io.containerd.content.v1.content/ingest/0241b1a54180df2f0cfae28de844369b7747e3d67edff7273db7736396e96def/data: **no space left on device**"`
- Line 1328: `"No accepted Verification Bundle test commands were found; skipping evaluation script generation."`
- `/Users/john/rat-bench-integration/results/dockeragent/2026-06-07-baseline/output/Peterande/D-FINE/_result_row.json`:
  - `"build_success": false`, `"test_success": false`, `"pytest_errors": 1`, `"error_breakdown": {"ModuleNotFoundError": 1}`

**RAT success:**
- File: `/Users/john/rat-bench-integration/results/rat/2026-06-07-corrected/output/Peterande/D-FINE/_result_row.json`:
  - `"pytest_collect_success": true`, `"pytest_pass_rate": 1.0`, `"pytest_total_tests": 2`, `"pytest_passed": 2`
- RAT's outer_commands.json shows:
  - `pip install -q -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple` (executed once in build time)
  - Test execution via `run-pytest` wrapper with 2 test functions in tests/test_core_imports.py

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **Implement image pre-selection in synthesizer.py:** Detect heavy package stacks (torch, cuda, tensorflow) early and prefer pre-built images (pytorch/pytorch, tensorflow/tensorflow) instead of starting from minimal python images. This avoids massive wheel downloads and intermediate layer bloat.

2. **Disable per-command docker commits:** Replace the snapshot-after-each-command pattern with a batching strategy. Group multiple setup commands into a single RUN instruction in the Dockerfile. Docker layer caching will still work, and you avoid disk bloat from N intermediate images.

3. **Monitor remaining disk during docker operations:** Add a preflight check in the sandbox that estimates total disk needed (sum of all package wheel sizes + 2x layer overhead). Reject or skip the agent if remaining disk < 50% of estimated need.

4. **Defer heavy test package installation:** Do NOT install test frameworks (pytest, matplotlib, opencv) alongside the main requirements. Require test commands to be collected BEFORE committing a heavy image. If tests need extra deps, add them in a separate RUN layer or install on-the-fly at test time.

5. **Verify the Verification Bundle is non-empty before skipping evaluation:** Add a warning/error log if no test commands are accepted, and mark build_success=false to signal downstream that the agent failed to establish a working environment.
