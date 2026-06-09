# Source Unavailable GitHub Recovery Notes

This note records the June 3, 2026 GitHub recovery check for the 13 Repo2Run
instances previously classified as source snapshot unavailable.

## Fixed

| Original instance | Dataset change | Evidence |
| --- | --- | --- |
| `NexaAI/nexa-sdk` | Canonical repo changed to `qualcomm/nexa-sdk`; `sha`/`base_commit` expanded to `33f6babe6bb2e3af7a930eff0321f612833d7262`. | GitHub redirects the repo to `qualcomm/nexa-sdk`; target commit becomes reachable after fetching GitHub PR refs. |
| `fedirz/faster-whisper-server` | Canonical repo changed to `speaches-ai/speaches`; `sha`/`base_commit` expanded to `cbb6c9284f626a7e708d5db8505297419a99154a`. | The project has moved to `speaches-ai/speaches`; target commit becomes reachable after fetching GitHub PR refs. |
| `hpcaitech/Open-Sora` | `sha`/`base_commit` expanded to `38de637cab331ca106d67467b63f66fc71678391`. | Repo is public; target commit becomes reachable after fetching GitHub PR refs. |
| `landing-ai/vision-agent` | `sha`/`base_commit` expanded to `63eab8673e827afd0e50137574a69d7c3964eeeb`. | Repo is public; target commit becomes reachable after fetching GitHub PR refs. |
| `opengeos/HyperCoast` | `sha`/`base_commit` expanded to `c1604cb53f3b917941c4105a157e4a1f0cb1b109`. | Repo is public; target commit becomes reachable after fetching GitHub PR refs. |
| `siliconflow/BizyAir` | `sha`/`base_commit` expanded to `cdb3bb86ab15216c78ad5c3097743c2a31582ab8`. | Repo is public; target commit becomes reachable after fetching GitHub PR refs. |

## Still Unresolved

| Instance | Current finding | Reason not modified |
| --- | --- | --- |
| `PrimeIntellect-ai/prime` | Repo is public, but `a974cf` was not found in ordinary refs or GitHub PR refs. | No trustworthy source snapshot or replacement repo found. |
| `RapidAI/RapidDoc` | Repo is public, but `5e5fef` was not found in ordinary refs or GitHub PR refs. | No trustworthy source snapshot or replacement repo found. |
| `jialuechen/deepfolio` | Repo is public, but `15d247` was not found in ordinary refs or GitHub PR refs. | No trustworthy source snapshot or replacement repo found. |
| `ucbepic/docetl` | Repo is public, but `00a761` was not found in ordinary refs or GitHub PR refs. | No trustworthy source snapshot or replacement repo found. |
| `outspeed-ai/outspeed` | GitHub git endpoint requires authentication / reports unavailable. `outspeed-ai/voice-devtools` is public but does not contain `049b40`. | No confirmed same-project public Git source found. |
| `run-llama/llama_extract` | Public web mirrors show the archived release `v0.0.5` at `89438fa`, but the GitHub git endpoint is unavailable; `run-llama/llama_cloud_services` does not contain `89438f`. | No cloneable GitHub source found for the original repo/ref. |
| `yihong1120/Construction-Hazard-Detection` | GitHub web page appears public, but git-upload-pack returns `Repository not found` and `git ls-remote` requests authentication. | The benchmark runner needs git clone/checkout; web visibility alone is insufficient. |

## Code Change

`agent.py` and `run_repo2run_benchmark.py` now fetch
`+refs/pull/*/head:refs/remotes/origin/pr/*` when a GitHub target commit cannot
be found on ordinary refs. This keeps the benchmark pinned to the original
Repo2Run revision instead of drifting to the latest repository HEAD.
