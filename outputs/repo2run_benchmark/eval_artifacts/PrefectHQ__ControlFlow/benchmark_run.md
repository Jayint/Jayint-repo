# Repo2Run Benchmark Run Log

## Instance
- Instance ID: `PrefectHQ__ControlFlow`
- Full Name: `PrefectHQ/ControlFlow`
- SHA: `f259fa`
- Repo URL: `https://github.com/PrefectHQ/ControlFlow.git`

## Outcome
- Execution Status: `dockerfile_missing`
- Dockerfile Generation Success: `false`
- Environment Build Success: `false`
- Paper Build Success: `true`
- Paper Alignment: `unexpected_failure`
- Docker Platform: `linux/amd64`
- Verification Command Source: `(none)`
- Agent Dockerfile Present: `false`
- Agent Dockerfile Usable: `false`
- Agent Dockerfile Ignored Reason: `(none)`

## Paths
- Agent Run Summary: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/PrefectHQ__ControlFlow/agent_run_summary.json`
- Agent Dockerfile: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/PrefectHQ__ControlFlow/Dockerfile`
- Eval Dockerfile: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/PrefectHQ__ControlFlow/Dockerfile.eval`
- Eval Build Context: `(none)`
- Result JSON: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/results/PrefectHQ__ControlFlow.json`

## Agent Run
- Command: `/opt/anaconda3/bin/python3.12 /Users/panjianying/Desktop/Jayint-repo_repo2run/agent.py https://github.com/PrefectHQ/ControlFlow.git --base-commit f259fa --image auto --model MiniMax-M2.7-highspeed --steps 100 --workplace /Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/PrefectHQ__ControlFlow --command-timeout 1800 --enable-observation-compression`
- Return Code: `0`
- Timed Out: `false`
- Duration Seconds: `990.73`
- Started At: `2026-05-18T18:40:11.746853+08:00`
- Finished At: `2026-05-18T18:56:42.476756+08:00`
- CWD: `/Users/panjianying/Desktop/Jayint-repo_repo2run`
- Stdout Log: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/PrefectHQ__ControlFlow/terminal_logs/agent_run.stdout.log`
- Stderr Log: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/PrefectHQ__ControlFlow/terminal_logs/agent_run.stderr.log`

## Eval Build Context
(not run)

## Resynthesis
(not run)

## Docker Build
(not run)

## Dockerfile Repair
(not run)

## Dockerfile Validation Attempts
(not run)

## Verification Commands
### Runtime Preparation Commands
- `(none)`

### Test Commands
- `(none)`

## Test Execution
(not run)

## Docker Cleanup
(not run)
