# lyuwenyu/RT-DETR

- DA pass-rate: 0% (0 tests executed) | RAT pass-rate: 0% (3 tests with errors) | bucket: BOTH_FAIL
- DA build_success/test_success: False/False | error_breakdown: no_dockerfile from disk exhaustion
- RAT build_success/test_success: True/False | error_breakdown: {ModuleNotFoundError: 1, AttributeError: 1, ImportError: 1}

## Failure stage & category

DA: **docker_build** / **native_system_deps_missing** (disk space exhaustion)
RAT: **test_execution** / **other** (code-level compatibility issues)

## Root cause (why DA lost)

DockerAgent's container exhausted disk space during `pip install torch==2.0.1 torchvision==0.15.2 --force-reinstall` in Step 18 of the agent run. The error "failed to apply diff: write /var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/7564/fs/usr/local/lib/python3.10/site-packages/nvidia/nccl/lib/libnccl.so.2: no space left on device" prevented the agent from completing its Dockerfile generation. The container was cleaned up, and no Dockerfile was ever extracted, resulting in `failure_reason: "no_dockerfile"` and immediate test skipping. RAT's container did not encounter this issue, suggesting either a more efficient pip caching strategy, better image selection, or different dependency resolution ordering that consumed less intermediate disk space during the build process.

## What RAT did differently

- RAT used `pip install --no-cache-dir pytest openai` in the Dockerfile RUN instruction (line 125 of RAT run.log), avoiding pip cache bloat.
- RAT installed dependencies with the Aliyun mirror (`pip.conf` with index-url = https://mirrors.aliyun.com/pypi/simple/) configured at image build time (line 123), reducing redundant downloads.
- RAT ran the full dependency installation (`pip install -q -r /repo/rtdetrv2_pytorch/requirements.txt`) inside the container AFTER cloning, allowing the host filesystem to manage temporary artifacts.
- RAT then successfully executed pytest and collected 3 tests (`pytest_total_tests: 3`), despite those tests later failing on code-level import issues.

## Evidence

- DA run.log lines 826-831: "pip install torch==2.0.1 torchvision==0.15.2 --force-reinstall" followed by "failed to apply diff: write ... no space left on device" and container cleanup.
- DA _result_row.json line 3: `"failure_reason": "no_dockerfile"`
- RAT run.log lines 115-131: Dockerfile generated with `--no-cache-dir` and Aliyun mirror configured.
- RAT run.log line 145: "Container ... started with image ... successfully"
- RAT _result_row.json line 14: `"pytest_executed": true` with `"pytest_total_tests": 3` and `"pytest_errors": 3`

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In agent.py**: Add `--no-cache-dir` flag to all `pip install` commands to prevent cache accumulation in the container during interactive agent steps. This mirrors RAT's approach.
2. **In src/synthesizer.py**: When generating the Dockerfile, use `--no-cache-dir` in base RUN instructions, and consider configuring a pip mirror (e.g., Aliyun for Chinese mirrors, or a default fast mirror) at image build time rather than relying on network availability at test time.
3. **In agent.py or a new pre-flight check**: Before executing large pip installs (e.g., torch+CUDA packages), check available disk space in the container and warn/abort if free space is below a threshold (e.g., < 5 GB). This can prevent silent failures and truncated Dockerfile generation.
4. **Consider staged image building**: Install large dependencies (torch, torchvision) with specific versions in a separate RUN layer earlier in the Dockerfile, so failures are caught during `docker build` rather than during agent steps.
