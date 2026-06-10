# Quickstart: RAT benchmark on a local PC (no VPS)

You do **not** need a cloud box to run this. The Hetzner/Vultr runbook
([LINUX_BOX_RUNBOOK.md](./LINUX_BOX_RUNBOOK.md)) exists purely for **RAM and throughput** —
a server lets you run many repos in parallel. The code itself runs fine on a laptop or
desktop; you just run sequentially (or at low concurrency) because **RAM is the wall**.

This guide is for a teammate setting it up on their own machine from a fresh clone.

---

## 0. OS support

| OS | Status |
|----|--------|
| **Linux** | ✅ native |
| **macOS** | ✅ native |
| **Windows** | ⚠️ Run inside **WSL2** (Ubuntu), not native Windows Python. The parallel scheduler uses POSIX process groups and the Docker calls use POSIX shell redirection — both assume a Unix shell. WSL2 gives you that for free. |

---

## 1. Prerequisites

- **Docker** installed and running (Docker Desktop on macOS/Windows-WSL2, or Docker Engine on Linux).
  Confirm: `docker info`.
- **Python 3.12** (`python3.12 --version`).
- **~15–20 GB free disk** (each repo builds an image; they're pruned as you go).
- An **LLM API key** — OpenRouter by default (or any OpenAI-compatible gateway).

---

## 2. Get the two code trees

The runner derives its paths from **where `run_rat_benchmark.py` lives**, so the simplest
layout needs **zero environment variables**: unzip the RAT harness into `runanything/src`
*inside* this repo.

```bash
# 1) this repo
git clone <your-fork-url> rat-bench-integration
cd rat-bench-integration

# 2) the RAT harness, unzipped to <repo>/runanything/src  → auto-detected by the runner
mkdir -p runanything
curl -sL -o /tmp/rat.zip "https://anonymous.4open.science/api/repo/RunAnyThing_Anonymous/zip"
unzip -q /tmp/rat.zip -d runanything/src
```

> Prefer a different location? Put RAT anywhere and set `export RAT_ROOT=/path/to/runanything/src`.
> The runner checks `<repo>/runanything/src`, then a sibling `../runanything/src`, then
> `/tmp/runanything/src`, before falling back to the repo-local path.

`runanything/` is gitignored-friendly — don't commit the harness; it's an external dataset.

---

## 3. Python env + dependencies

```bash
python3.12 -m venv .venv
source .venv/bin/activate            # Windows-WSL2 / macOS / Linux

pip install -U pip
pip install -r requirements.txt
# RAT harness deps (skip sweagent — it won't resolve on 3.12 and isn't import-required):
grep -v '^sweagent' runanything/src/requirements.txt > /tmp/rat-reqs.txt
pip install -r /tmp/rat-reqs.txt
pip install weave datasets pexpect pypdf   # the bits the import chain actually needs

# sanity: the runner should import cleanly and print help
python run_rat_benchmark.py --help
```

---

## 4. API key

```bash
cp .env.example .env
# edit .env and fill in OPENROUTER_API_KEY=...   (keep the file out of git; it's gitignored)
```

`agent.py` auto-loads `.env` from the repo. Provider precedence is OpenRouter → MiniMax → OpenAI;
all are OpenAI-compatible, so any one filled-in block works.

---

## 5. Run

Start with the **smoke tier, sequential** — one repo at a time, lowest RAM pressure:

```bash
python run_rat_benchmark.py \
  --tier smoke \
  --root-path ./rat_run \
  --llm deepseek/deepseek-v4-flash
```

`--repos-json` defaults to the dataset shipped in this repo
(`datasets/rat_python_hard_subset.json`), so you don't need to pass it.

To run just one repo (handy for a first smoke test):

```bash
python run_rat_benchmark.py --only owner/repo --root-path ./rat_run --llm deepseek/deepseek-v4-flash
```

### Concurrency — only if you have the RAM

RAM, not CPU, is the bottleneck: heavy repos building in parallel will swap-stall a small
machine (a 16 GB Mac stalled at 3 overlapping heavy builds). Rule of thumb:

| Free RAM | Suggested `--concurrency` |
|----------|---------------------------|
| ≤ 16 GB  | sequential (omit the flag) |
| 32 GB    | `--concurrency 2` |
| 64 GB+   | `--concurrency 4`–`6` |

```bash
python run_rat_benchmark.py --tier smoke --concurrency 2 --root-path ./rat_run \
  --llm deepseek/deepseek-v4-flash
```

Runs are **resume-safe**: re-running skips any repo whose `run_pytest_results.json` already
exists, so an interrupted batch picks up where it left off.

---

## 6. Results

- Per-repo artifacts land in `rat_run/output/<owner>/<repo>/` (`_result_row.json`, `_meta.json`,
  `run_pytest_results.json`, `run.log`).
- The aggregated report + `rat_run/rat_results.json` are written at the end of sequential and
  scheduler runs. To rebuild the report from existing per-repo rows without re-running:

```bash
python run_rat_benchmark.py --aggregate-only --root-path ./rat_run
```

- Optional cleanup of the images this tool built:

```bash
python run_rat_benchmark.py --prune
```

---

## Troubleshooting

- **`ModuleNotFoundError: No module named 'weave'`** — you're on the wrong interpreter. Activate
  the venv (`source .venv/bin/activate`) or call its python directly.
- **`KeyError: 'DOCKERAGENT_ROOT'`** — only happens if you invoke the model outside the runner.
  Run through `run_rat_benchmark.py`, which sets it automatically.
- **RAT import errors** — `RAT_ROOT` isn't pointing at the unzipped harness. Either put it at
  `<repo>/runanything/src` or `export RAT_ROOT=...`.
- **Everything stalls / machine swaps** — too much concurrency for your RAM. Drop to sequential.
