# Failure Analysis — NevaMind-AI/memU-server

**Harness Status:** error | **True Outcome:** no_dockerfile | **Category:** other | **Pytest:** N/A (no build)

## Root Cause

The planner model (deepseek-v4-flash) systematically violated the single-step response protocol by returning bash commands wrapped in markdown code fences (```bash` ... ```` instead of plain `Action: <command>` format. The agent rejected all 30 planner attempts with "Error: No command found. Please specify an action in 'Action: <command>' format." This caused the agent loop to exhaust without ever executing any actual setup steps beyond two initial read-only probes.

## Environment / Trajectory State at Termination

- **Steps Completed:** 0 actual setup steps; 30 planner loop attempts, all rejected.
- **Agent Steps in Summary:** 0 (only 2 actions logged in successful_actions + failed_actions: a read-only `pwd && ls -la` and a malformed multi-command action).
- **Installed:** Only baseline python:3.13 image.
- **Missing:** All project dependencies (never queried pyproject.toml or requirements), PostgreSQL service (detected but not installed), pytest and its collection never attempted.
- **Last Failing Action:** Step 30 — planner still returning markdown-wrapped command; agent exhausted 30-step budget and declared "Environment Configuration FAILED."

## Key Evidence

From `/Users/john/rat-bench-integration/workplace/multi_docker_eval_NevaMind-AI__memU-server/logs/setup_logs/0.md` and `1.md`:

```
[ASSISTANT]
I'll start by inspecting the key configuration files to understand the project's dependencies and setup.

**Action:**
```bash
cat /repo/pyproject.toml
```

Observation: Error: No command found. Please specify an action in 'Action: <command>' format.

[NEXT ATTEMPT]

I need to find the correct path to the repository. Let me check the working directory.

**Action:**
```bash
pwd && ls -la
```

Observation: Error: No command found. Please specify an action in 'Action: <command>' format.
```

Every planner response in the 30-step loop produced the same pattern: markdown code fences instead of raw `Action:` format. The agent's error message is explicit: "Please specify an action in 'Action: <command>' format."

## Takeaway for DockerAgent

1. **Model Protocol Compliance Failure**: The deepseek-v4-flash model does not naturally follow the single-step `Action: <command>` protocol described in the system prompt. It defaults to markdown code fence wrapping.
2. **No Fallback Parsing**: DockerAgent's action parser appears to be strict (no markdown unstripping/normalization). For robustness, add a post-processing layer that extracts bash from markdown code fences before rejecting.
3. **Model Selection Issue**: For this benchmark (RAT hard 50), deepseek-v4-flash is unsuitable. Switch to a model with stronger instruction-following (Sonnet, Opus, or a tuned deepseek variant with explicit format examples).
4. **Early Termination Signal**: The repeated "No Action detected. Asking Planner to clarify" warnings (steps 1–30) are a clear signal that the planner is stuck in a format loop. Add a detection circuit-breaker: if 3+ consecutive clarifications return the same parse error, emit an alert and fail fast rather than looping to 30 steps.

## Fixability

**Category:** planner_strategy_fix  
**Reason:** The root cause is the planner model's failure to follow the `Action:` format protocol, not a synthesizer bug, missing dependencies, or environment deficiency. Either the prompt engineering (few-shot examples) needs tuning, or the model selection is wrong. The agent implementation itself is working correctly (it correctly rejected malformed actions). A trivial fix is impossible without either retuning the prompt for deepseek or switching to a more compliant model.
