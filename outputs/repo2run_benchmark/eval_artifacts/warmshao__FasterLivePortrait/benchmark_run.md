# Repo2Run Benchmark Run Log

## Instance
- Instance ID: `warmshao__FasterLivePortrait`
- Full Name: `warmshao/FasterLivePortrait`
- SHA: `6aa810`
- Repo URL: `https://github.com/warmshao/FasterLivePortrait.git`

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
- Agent Run Summary: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/warmshao__FasterLivePortrait/agent_run_summary.json`
- Agent Dockerfile: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/warmshao__FasterLivePortrait/Dockerfile`
- Eval Dockerfile: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/warmshao__FasterLivePortrait/Dockerfile.eval`
- Eval Build Context: `(none)`
- Result JSON: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/results/warmshao__FasterLivePortrait.json`

## Agent Run
- Command: `/opt/anaconda3/bin/python3.12 /Users/panjianying/Desktop/Jayint-repo_repo2run/agent.py https://github.com/warmshao/FasterLivePortrait.git --base-commit 6aa810 --image auto --model MiniMax-M2.7-highspeed --steps 100 --workplace /Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/warmshao__FasterLivePortrait --command-timeout 1800 --enable-observation-compression`
- Return Code: `0`
- Timed Out: `false`
- Duration Seconds: `2129.563`
- Started At: `2026-05-20T23:45:16.800713+08:00`
- Finished At: `2026-05-21T00:20:46.363755+08:00`
- CWD: `/Users/panjianying/Desktop/Jayint-repo_repo2run`
- Stdout Log: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/warmshao__FasterLivePortrait/terminal_logs/agent_run.stdout.log`
- Stderr Log: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/warmshao__FasterLivePortrait/terminal_logs/agent_run.stderr.log`

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
