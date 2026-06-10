#!/usr/bin/env bash
# Bounded digest extractor for a single DockerAgent benchmark instance.
# Usage: bash _extract_digest.sh <repo_output_dir>
# Prints a size-bounded digest to stdout; NEVER dumps a full multi-MB run.log.
set -uo pipefail

DIR="${1:?usage: _extract_digest.sh <repo_output_dir>}"
LOG="$DIR/run.log"

sec() { printf '\n==================== %s ====================\n' "$1"; }

sec "RESULT ROW (_result_row.json)"
[ -f "$DIR/_result_row.json" ] && cat "$DIR/_result_row.json" || echo "(missing)"

sec "META (_meta.json)"
[ -f "$DIR/_meta.json" ] && cat "$DIR/_meta.json" || echo "(missing)"

sec "AGENT SUMMARY (dockerfile + flags + logs.error)"
python3 - "$DIR" <<'PY'
import json, glob, os, sys
d = sys.argv[1]
cand = [x for x in glob.glob(os.path.join(d, "*.json"))
        if not os.path.basename(x).startswith("_")
        and "pytest" not in os.path.basename(x)]
if not cand:
    print("(no agent summary json)"); raise SystemExit
s = json.load(open(cand[0]))
print("file:", os.path.basename(cand[0]))
for k in ("instance_id","language","build_success","test_success","platform"):
    print(f"{k}: {s.get(k)!r}")
df = s.get("dockerfile") or ""
print(f"\n--- dockerfile in summary (len={len(df)}) ---")
print(df if len(df) < 2500 else df[:2500] + "\n...[truncated]")
logs = s.get("logs") or {}
print("\n--- logs.* selected fields ---")
for k in ("error","build_recipe_error","build_recipe_source","test_command_source",
          "runtime_preparation_source","verification_source","skip_evaluation",
          "verified_test_command","verified_test_commands",
          "verified_runtime_preparation_commands","dropped_broad_test_commands"):
    if k in logs:
        v = logs[k]
        vs = str(v)
        print(f"{k}: {vs if len(vs)<400 else vs[:400]+'...'}")
ps = logs.get("platform_support")
if ps: print("platform_support:", ps)
steps = logs.get("agent_steps")
print(f"agent_steps in summary: {len(steps) if isinstance(steps,list) else steps}")
PY

sec "EVAL_BUILD DOCKERFILE (the file that docker build actually used)"
if [ -f "$DIR/eval_build/Dockerfile" ]; then
  nl -ba "$DIR/eval_build/Dockerfile"
else
  echo "(no eval_build/Dockerfile -> classic no_dockerfile case)"
fi

sec "PYTEST RESULTS (error_breakdown + summary)"
python3 - "$DIR" <<'PY'
import json, glob, os, sys
d = sys.argv[1]
for name in ("run_pytest_results.json","run_pytest_collect_results.json"):
    p = os.path.join(d, name)
    if not os.path.exists(p): continue
    try: j = json.load(open(p))
    except Exception as e:
        print(f"{name}: unreadable ({e})"); continue
    print(f"--- {name} ---")
    s = json.dumps(j)
    print(s if len(s) < 1800 else s[:1800] + "...[truncated]")
PY

if [ ! -f "$LOG" ]; then
  sec "RUN.LOG"; echo "(missing run.log)"; exit 0
fi

TOTAL=$(wc -l < "$LOG")
STEPS=$(grep -cE "^=+ Step [0-9]+ =+" "$LOG")
sec "TRAJECTORY OVERVIEW (run.log lines=$TOTAL, agent steps=$STEPS)"
echo "--- one line per step: step header + first action line ---"
grep -nE "^=+ Step [0-9]+ =+|^\[Action\]" "$LOG" | awk '
  /Step/ {hdr=$0; getline_action=1; next}
  /\[Action\]/ && getline_action {print hdr; getline_action=0}
' 2>/dev/null | head -120
echo
echo "--- compact step+action listing (alt view, first 200 lines of grep) ---"
awk '
  /^=+ Step [0-9]+ =+/ {step=$0; want=2; print "\n"step; next}
  want>0 && (/^\[Action\]/||/^\[Thought\]/) {print "  "$0; want--}
' "$LOG" | head -200

sec "LAST 3 STEPS IN FULL (where the agent gave up / final state)"
LAST_STEP_LINE=$(grep -nE "^=+ Step [0-9]+ =+" "$LOG" | tail -3 | head -1 | cut -d: -f1)
if [ -n "${LAST_STEP_LINE:-}" ]; then
  tail -n +"$LAST_STEP_LINE" "$LOG" | head -300
else
  echo "(no step markers found)"
fi

sec "KEY ERROR SIGNALS (grep, last 80 matches)"
grep -nE "Traceback|ModuleNotFoundError|ImportError|No such file|non-zero|returned non-zero exit|E: |ERROR:|error:|FAILED|Could not|command not found|Permission denied|Cannot|Configuration FAILED|Configuration did not complete|No Dockerfile|Dockerfile not found|exit code|Killed|MemoryError|timed out|timeout|Step limit|max steps|step budget" "$LOG" \
  | tail -80

sec "RUN.LOG TERMINAL TAIL (last 60 lines = docker build / wrap-up)"
tail -60 "$LOG"

sec "END DIGEST"
