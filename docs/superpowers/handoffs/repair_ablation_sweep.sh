#!/usr/bin/env bash
# repair_ablation_sweep.sh — controlled "how much does the react repair loop help" study.
#
# For each repo in a prior CONSTRUCTION-ONLY v3 run, re-run the react repair loop SEEDED with
# that run's already-generated setup.sh (construction skipped -> the first-pass script is held
# FIXED, so the only variable is repair). Per repo you get: step-0 (== the v3 first pass) and the
# final outcome after repair. The delta is the repair loop's contribution.
#
# Usage:
#   OPENROUTER_API_KEY=... OPENROUTER_API_BASE=https://openrouter.ai/api/v1 \
#     ./repair_ablation_sweep.sh <prior_run_dir> <out_dir> [model] [max_steps] [test_threshold]
#
# Example:
#   ./repair_ablation_sweep.sh \
#     /opt/runs/john-planner-v3/construction-python50-20260707-072356 \
#     /opt/runs/john-planner-v3/repair-ablation-$(date +%Y%m%d-%H%M%S) \
#     deepseek/deepseek-v4-flash 30 0.9
#
# Notes:
#   * Services are OFF by default (V3_INCLUDE_SERVICES unset) — the seed scripts are the
#     no-services baseline; nothing to toggle.
#   * PYTHONUNBUFFERED=1 is set so logs stream (otherwise stdout block-buffers to the file).
#   * Each repo runs independently; a failure in one does not stop the sweep.
set -uo pipefail

PRIOR_RUN="${1:?prior run dir (with output/<owner>/<repo>/) required}"
OUT_DIR="${2:?output dir required}"
MODEL="${3:-deepseek/deepseek-v4-flash}"
MAX_STEPS="${4:-30}"
TEST_THRESHOLD="${5:-0.9}"

# run_v3_e2e.py — adjust if your agent checkout lives elsewhere on the VM.
DRIVER="${V3_DRIVER:-/opt/agents/john-planner-v3/scripts/run_v3_e2e.py}"
# Wall-clock cap per repo so one hung repo can't stall the whole sweep (the container-build
# step is not internally timeout-bounded). Uses coreutils `timeout` when available.
PER_REPO_TIMEOUT="${PER_REPO_TIMEOUT:-2400}"
TIMEOUT_PREFIX=""
command -v timeout >/dev/null 2>&1 && TIMEOUT_PREFIX="timeout -k 30 $PER_REPO_TIMEOUT"

mkdir -p "$OUT_DIR"
SUMMARY="$OUT_DIR/summary.csv"
echo "owner_repo,base_image,stop_reason,verdict,seconds" > "$SUMMARY"

shopt -s nullglob
count=0
for leaf in "$PRIOR_RUN"/output/*/*/ ; do
    setup="$leaf/setup.sh"
    dockerfile="$leaf/eval_build/Dockerfile"
    src="$leaf/v3_src"
    slug="$(basename "$(dirname "$leaf")")_$(basename "$leaf")"

    # Skip repos that didn't render / don't have a checkout.
    [ -f "$setup" ]      || { echo "SKIP $slug (no setup.sh)"; continue; }
    [ -f "$dockerfile" ] || { echo "SKIP $slug (no eval_build/Dockerfile)"; continue; }
    [ -d "$src" ]        || { echo "SKIP $slug (no v3_src checkout)"; continue; }

    # The base image the seed script was rendered for — hold it FIXED (no fresh LLM selection).
    base_image="$(grep -m1 '^FROM ' "$dockerfile" | awk '{print $2}')"
    [ -n "$base_image" ] || { echo "SKIP $slug (no FROM in Dockerfile)"; continue; }

    repo_out="$OUT_DIR/$slug"
    mkdir -p "$repo_out"
    echo ">>> $slug  (base=$base_image)"
    t0=$(date +%s)
    $TIMEOUT_PREFIX env PYTHONUNBUFFERED=1 python3 -u "$DRIVER" "$src" --arm react \
        --seed-script "$setup" --base-image "$base_image" \
        --model "$MODEL" --max-steps "$MAX_STEPS" --test-threshold "$TEST_THRESHOLD" \
        --out "$repo_out/setup.sh" --trace-out "$repo_out/trace.jsonl" \
        > "$repo_out/run.log" 2>&1
    rc=$?
    dur=$(( $(date +%s) - t0 ))
    [ "$rc" = 124 ] && echo "[sweep] TIMEOUT after ${PER_REPO_TIMEOUT}s (killed)" >> "$repo_out/run.log"

    stop_reason="$(grep -oE 'stop_reason=[A-Za-z_]+' "$repo_out/run.log" | tail -1 | cut -d= -f2)"
    verdict="$(grep -oE 'V3 E2E: [A-Za-z_]+' "$repo_out/run.log" | tail -1 | awk '{print $3}')"
    [ "$rc" = 124 ] && { stop_reason="TIMEOUT"; verdict="TIMEOUT"; }
    echo "$slug,$base_image,${stop_reason:-NONE},${verdict:-NONE},$dur" >> "$SUMMARY"
    echo "    -> stop_reason=${stop_reason:-NONE} verdict=${verdict:-NONE} (${dur}s, rc=$rc)"
    count=$((count+1))
done

echo ""
echo "=== swept $count repos -> $SUMMARY ==="
echo "--- outcome tally ---"
tail -n +2 "$SUMMARY" | cut -d, -f3 | sort | uniq -c | sort -rn
echo "--- verdict tally (DONE == repair reached the >=${TEST_THRESHOLD} gate) ---"
tail -n +2 "$SUMMARY" | cut -d, -f4 | sort | uniq -c | sort -rn
echo ""
echo "Compare 'verdict=PASS' count here against the prior run's first-pass pass count"
echo "(from $PRIOR_RUN's _result_row.json / pytest results) = the repair loop's net contribution."
