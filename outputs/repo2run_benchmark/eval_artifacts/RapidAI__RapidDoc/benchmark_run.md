# Repo2Run Benchmark Run Log

## Instance
- Instance ID: `RapidAI__RapidDoc`
- Full Name: `RapidAI/RapidDoc`
- SHA: `5e5fef`
- Repo URL: `https://github.com/RapidAI/RapidDoc.git`

## Outcome
- Execution Status: `dockerfile_missing`
- Dockerfile Generation Success: `false`
- Environment Build Success: `false`
- Paper Build Success: `true`
- Paper Alignment: `unexpected_failure`
- Docker Platform: `(none)`
- Verification Command Source: `(none)`
- Agent Dockerfile Present: `false`
- Agent Dockerfile Usable: `false`
- Agent Dockerfile Ignored Reason: `agent_run_failed_or_timed_out`

## Paths
- Agent Run Summary: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/RapidAI__RapidDoc/agent_run_summary.json`
- Agent Dockerfile: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/RapidAI__RapidDoc/Dockerfile`
- Eval Dockerfile: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/RapidAI__RapidDoc/Dockerfile.eval`
- Eval Build Context: `(none)`
- Result JSON: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/results/RapidAI__RapidDoc.json`

## Agent Run
- Command: `/opt/anaconda3/bin/python3.12 /Users/panjianying/Desktop/Jayint-repo_repo2run/agent.py https://github.com/RapidAI/RapidDoc.git --base-commit 5e5fef --image auto --model MiniMax-M2.7-highspeed --steps 100 --workplace /Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/workplaces/RapidAI__RapidDoc --command-timeout 1800 --enable-observation-compression`
- Return Code: `1`
- Timed Out: `false`
- Duration Seconds: `375.768`
- Started At: `2026-05-18T21:25:37.297634+08:00`
- Finished At: `2026-05-18T21:31:53.065630+08:00`
- CWD: `/Users/panjianying/Desktop/Jayint-repo_repo2run`
- Stdout Log: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/RapidAI__RapidDoc/terminal_logs/agent_run.stdout.log`
- Stderr Log: `/Users/panjianying/Desktop/Jayint-repo_repo2run/outputs/repo2run_benchmark/eval_artifacts/RapidAI__RapidDoc/terminal_logs/agent_run.stderr.log`

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
