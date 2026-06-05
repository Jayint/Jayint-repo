# Runbook: RAT benchmark on a Linux box (Vultr/DO, Singapore)

Decisions (2026-06-05): memory-optimized box in **Singapore**, **amd64**, results via **git branch + bucket**,
teammate in China **consumes results only** (one owner runs the box).

Why a box: a 16 GB Mac swap-stalled at K=3 when 3 heavy repos overlapped. RAM is the wall, not CPU.
128 GB removes it, so heavy repos stop fighting for memory and plain `xargs -P N` is enough (no scheduler needed).

---

## 0. What you do vs what I can drive

- **You (cloud console, ~10 min):** create the instance + SSH key, create a private GitHub repo for code
  transfer, (optional) create a bucket for logs.
- **Me (once the box exists + I have SSH, or you paste commands with `!`):** the whole install → transfer →
  run → publish-results pipeline below.

I cannot click the Vultr/DO UI or hold cloud credentials, so provisioning (Step 1) is yours. Everything after
is scriptable.

---

## 1. Provision (you)

**Vultr:** Compute → "Optimized Cloud Compute" → **Memory Optimized** → **Singapore** →
16 vCPU / 128 GB RAM / NVMe → **Ubuntu 22.04** → attach your SSH key → Deploy.

**DigitalOcean (equivalent):** Droplets → **Memory-Optimized** → region **SGP1** →
`m-16vcpu-128gb` → **Ubuntu 22.04** → SSH key → Create.

Note the public IP. Confirm `ssh root@<IP>` works. (cost-no-object → take the 128 GB tier.)

---

## 2. Bootstrap the box (Docker + Python 3.12 + git)

```bash
# on the box, as root
apt-get update && apt-get install -y git curl unzip build-essential software-properties-common
# Docker (native, amd64)
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
# Python 3.12
add-apt-repository -y ppa:deadsnakes/ppa && apt-get update
apt-get install -y python3.12 python3.12-venv python3.12-dev
docker info | grep -E "Server Version|Total Memory|CPUs"   # sanity
```

---

## 3. Get the code onto the box

```bash
# our repo: push from the Mac to a PRIVATE GitHub repo first (you create it), then on the box:
git clone git@github.com:<you>/rat-bench-integration.git /opt/rat-bench-integration

# the .env (OpenRouter key) is gitignored on purpose -> copy it SEPARATELY, never commit it:
#   from the Mac:  scp /Users/john/rat-bench-integration/.env root@<IP>:/opt/rat-bench-integration/.env

# RAT benchmark code (anonymized repo):
mkdir -p /opt/runanything && cd /opt/runanything
curl -sL -o repo.zip "https://anonymous.4open.science/api/repo/RunAnyThing_Anonymous/zip"
unzip -q repo.zip -d src
```

---

## 4. venv + dependencies

```bash
python3.12 -m venv /opt/rat_venv
/opt/rat_venv/bin/pip install -U pip
# our agent deps:
/opt/rat_venv/bin/pip install -r /opt/rat-bench-integration/requirements.txt
# RAT harness deps, MINUS sweagent (it won't resolve on py3.12 and is NOT import-required;
# baselines shell out, so importing our model only needs pexpect/datasets/weave):
grep -v '^sweagent' /opt/runanything/src/requirements.txt > /tmp/rat-reqs.txt
/opt/rat_venv/bin/pip install -r /tmp/rat-reqs.txt
# belt-and-suspenders (these are the ones the import chain actually needs):
/opt/rat_venv/bin/pip install weave datasets pexpect pypdf
# verify the full import chain (no torch needed; memory embedder is lazy):
DOCKERAGENT_ROOT=/opt/rat-bench-integration RAT_ROOT=/opt/runanything/src \
  /opt/rat_venv/bin/python /opt/rat-bench-integration/run_rat_benchmark.py --help
```

---

## 5. Configure environment (override the Mac defaults baked into the runner)

`run_rat_benchmark.py` setdefaults macOS paths; exporting these overrides them. `dockeragent_model.py`
reads `DOCKERAGENT_ROOT` at import, so it must be set:

```bash
export RAT_ROOT=/opt/runanything/src
export DOCKERAGENT_ROOT=/opt/rat-bench-integration
# .env (OpenRouter) is auto-loaded by agent.py from the repo dir.
```

---

## 6. Run (128 GB RAM => high concurrency is safe)

```bash
cd /opt/rat-bench-integration
# adapt the helper for Linux paths:
cat > /tmp/run_one.sh <<'EOF'
#!/bin/bash
IDX="$1"; cd /opt/rat-bench-integration || exit 99
S=$(date +%s)
/opt/rat_venv/bin/python run_rat_benchmark.py --tier "$TIER" --offset "$IDX" --limit 1 \
  --root-path ./rat_run --llm deepseek/deepseek-v4-flash > "rat_run/_shardlog_${IDX}.log" 2>&1
echo "idx=$IDX rc=$? secs=$(( $(date +%s)-S ))" >> rat_run/_timings.txt
EOF
chmod +x /tmp/run_one.sh

# smoke tier first (16 repos), K=6 (128 GB easily holds 6 heavies):
TIER=smoke; export TIER
mkdir -p rat_run; : > rat_run/_timings.txt
seq 0 15 | xargs -P 6 -n 1 sh /tmp/run_one.sh
# then the full 50 (use --tier all and offsets 0..49 at K=8-10)
```

Run inside `tmux` so an SSH drop never kills the run: `tmux new -s bench`, run, detach `Ctrl-b d`.

**Caveat (from the eng review):** each `--limit 1` child overwrites `rat_run/rat_results.json` (the CQ-1
clobber). The per-repo `run_pytest_results.json` are race-safe, so re-aggregate the final report from those,
or run the final pass sequentially for a clean `rat_results.json`. On 128 GB the tiered scheduler is optional;
revisit only if you want auto-supervision + a single clean aggregate.

---

## 7. Publish results (git branch + bucket)

```bash
cd /opt/rat-bench-integration
git checkout -b results/smoke-$(date +%Y%m%d)
git add -f rat_run/output/**/run_pytest*results.json rat_run/rat_results.json
git commit -m "results: smoke tier on linux box $(date +%Y-%m-%d)"
git push -u origin HEAD          # both of you pull this branch

# bulky logs -> bucket (S3 example; or rclone to Cloudflare R2):
tar czf /tmp/rat-logs.tgz rat_run/_shardlog_*.log
aws s3 cp /tmp/rat-logs.tgz s3://<your-bucket>/rat/logs-$(date +%Y%m%d).tgz
```

Your China teammate just `git pull`s the results branch. He never touches the box.

---

## 8. Security

- SSH **key auth only**; disable password auth (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).
- `ufw allow 22 && ufw enable`.
- The OpenRouter key lives **only** in `/opt/rat-bench-integration/.env` (gitignored). Never commit it.
  It was pasted in chat earlier, so rotate it on OpenRouter once the box is set up if you care.
- Don't expose Docker's API port. Don't run anything as a service on a public port.

---

## 9. Open items before first run

- [ ] Private GitHub repo created + our branch pushed (code transfer path).
- [ ] Bucket chosen (S3 or Cloudflare R2) for logs (or skip; keep logs on the box).
- [ ] Box provisioned + SSH confirmed.
