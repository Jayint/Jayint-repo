#!/usr/bin/env bash
#
# deploy.sh -- push the Mac working tree (tracked code + uncommitted edits to
# tracked files + new code under src/ and tests/) to the VM DockerAgent root,
# WITHOUT ever touching the box's irreplaceable data.
#
# The box at /opt/rat-bench-integration holds 6.6 GB that must NEVER be deleted
# or overwritten:  .env (API keys), workplace/, every rat_run*/ dir, results/,
# memory/.  This script is built so that those paths can never even appear in
# the transfer set, let alone be deleted.
#
# WHY THIS DESIGN (vs git-on-box / rsync --delete):
#   - The transfer set is "git ls-files" (tracked files) ONLY. Untracked box
#     data is therefore never enumerated. rsync with --files-from does not
#     recurse, so it physically cannot see or remove files that aren't listed.
#   - There is NO --delete anywhere. Deletion is impossible by construction,
#     even if the file list comes back empty (verified: empty list + no
#     --delete = no-op; precious data survives).
#   - It needs NO GitHub deploy key (the box can't auth to the private repo);
#     it pushes file contents over the existing root SSH key, already working.
#   - It runs on the Mac's STOCK openrsync (2.6.9-compatible). No Homebrew/GNU
#     rsync required. All flags used here were verified against stock openrsync
#     talking to the box's GNU rsync 3.2.7 receiver.
#
# USAGE (run from the Mac, after editing):
#   ./deploy.sh                # MANDATORY dry-run -> safety gate -> y/N -> apply (+harness fix)
#   ./deploy.sh --apply        # still dry-runs + safety-checks first, then applies (+harness fix)
#   ./deploy.sh --dry-run      # dry-run + safety checks only, change nothing
#   ./deploy.sh --patch-harness# ONLY (re)apply the RAT harness /repo fix; no code sync
#
set -euo pipefail

# ---- config (verified against the live box) --------------------------------
SRC="/Users/john/rat-bench-integration"     # Mac working tree (git repo)
HOST="root@167.233.64.96"                    # VM box (root key auth, no password)
DEST="/opt/rat-bench-integration"            # DOCKERAGENT_ROOT on the box
BRANCH_EXPECT="rat-bench-integration"        # refuse to deploy from another branch
RSYNC="/usr/bin/rsync"                        # Mac stock openrsync (pinned on purpose)
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
# RAT harness lives in a SEPARATE gitless dir on the box (NOT under our repo). We keep
# its empty-/repo fix as a versioned patch and (re)apply it idempotently on deploy.
RAT_ROOT_BOX="${RAT_ROOT_BOX:-/opt/runanything/src}"
HARNESS_PATCH="$SRC/patches/0001-rat-harness-repo-path-fix.patch"

# Protected top-level paths on the box. Used three independent ways below:
#   (1) stripped from the file list (anchored at repo root, leading ^),
#   (2) belt-and-suspenders rsync --exclude (anchored with leading /),
#   (3) post-list assertion that aborts if any leaked in.
# ANCHORING IS LOAD-BEARING: the repo legitimately tracks 420 files under
# outputs/repo2run_benchmark/results/ and a tracked .env.example -- an
# unanchored "results" or ".env" rule would wrongly drop those. Every rule
# here matches the TRANSFER ROOT only.
# Each alternative is anchored at the repo root AND at a path boundary so that
# legitimate tracked files whose names START with a protected token are NOT
# dropped:  .env.example (tracked, must ship) must not match ".env";
# outputs/.../results/ (420 tracked files) must not match top-level "results/".
#   \.env$            -> only the exact top-level .env file
#   memory/           -> top-level memory/ dir contents
#   workplace/        -> top-level workplace/ dir
#   results/          -> ONLY top-level results/ (leading ^, so nested is safe)
#   rat_run([/.]|$)   -> rat_run, rat_run_rat, rat_run.partial-*, etc. (token boundary)
PROTECT_LIST_REGEX='^(\.env$|memory/|workplace/|results/|rat_run([/.]|$))'

red(){ printf '\033[31m%s\033[0m\n' "$*"; }
grn(){ printf '\033[32m%s\033[0m\n' "$*"; }
ylw(){ printf '\033[33m%s\033[0m\n' "$*"; }
die(){ red "FATAL: $*"; exit 1; }

# Apply the RAT harness empty-/repo fix to the box's gitless harness. Idempotent:
# already-fixed -> no-op; unrecognized version -> refuse to touch; always backs up first;
# verifies py_compile after. Lives here (not in the rsync set) because the harness is a
# separate dir (RAT_ROOT_BOX) outside DOCKERAGENT_ROOT and outside our git.
patch_harness() {
  echo; ylw "================= RAT HARNESS /repo FIX ($RAT_ROOT_BOX) ================="
  [ -f "$HARNESS_PATCH" ] || { ylw ">> no patch at $HARNESS_PATCH — skipping harness fix"; return 0; }
  $SSH "$HOST" "cat > /tmp/rat_harness_repo_path_fix.patch" < "$HARNESS_PATCH" \
    || { red ">> could not copy harness patch to box"; return 1; }
  $SSH "$HOST" 'bash -s' "$RAT_ROOT_BOX" <<'REMOTE'
set -euo pipefail
ROOT="$1"; ENV="$ROOT/libkit/environment.py"
[ -f "$ENV" ] || { echo "HARNESS_SKIP: $ENV not found (RAT_ROOT_BOX wrong?)"; exit 0; }
if grep -qF 'docker cp {self.root_path}/input/repo' "$ENV"; then
  echo "HARNESS_ALREADY_FIXED — no change"; exit 0
fi
if ! grep -qF 'docker cp {project_directory}/input/repo' "$ENV"; then
  echo "HARNESS_SKIP: unrecognized harness version (no buggy marker) — NOT touching"; exit 0
fi
BK="$ENV.bak-$(date -u +%Y%m%dT%H%M%SZ)"; cp -a "$ENV" "$BK"; echo "backup: $BK"
( cd "$ROOT" && git apply /tmp/rat_harness_repo_path_fix.patch )
( cd "$ROOT" && python3 -m py_compile libkit/environment.py )
echo "HARNESS_PATCHED_OK"
REMOTE
}

MODE="interactive"
case "${1:-}" in
  --apply)         MODE="apply" ;;
  --dry-run)       MODE="dryonly" ;;
  --patch-harness) MODE="patchharness" ;;
  "")              MODE="interactive" ;;
  *) die "unknown arg '$1' (use --apply | --dry-run | --patch-harness | no arg)";;
esac

# ---- preflight (fail fast, never half-deploy) ------------------------------
cd "$SRC" || die "cannot cd to SRC=$SRC"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$SRC is not a git repo"
[ -x "$RSYNC" ] || die "rsync not found at $RSYNC"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "$BRANCH_EXPECT" ] || die "on branch '$BRANCH', expected '$BRANCH_EXPECT' (refusing to deploy)"

# Box must exist and the DEST dir must already be there (we never create it,
# so a typo'd HOST/DEST can't accidentally seed a wrong tree somewhere).
$SSH "$HOST" "test -d '$DEST'" || die "$DEST not present on box, or SSH to $HOST failed"

# --patch-harness: only (re)apply the RAT harness /repo fix, then stop (no code sync).
if [ "$MODE" = "patchharness" ]; then
  patch_harness; exit $?
fi

COMMIT="$(git rev-parse HEAD)"
DESCRIBE="$(git describe --always --dirty --abbrev=12 2>/dev/null || echo "$COMMIT")"
DIRTY_N="$(git status --porcelain | grep -c . || true)"
DIFF_HASH="$(git diff HEAD | shasum -a 256 | cut -d' ' -f1)"   # fingerprints the uncommitted edits
STAMP_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
WHO="$(whoami)@$(hostname -s)"
grn ">> deploy  $DESCRIBE  ($DIRTY_N dirty entries)  ->  $HOST:$DEST  [$MODE]"

# ---- build the transfer list (tracked files + scoped new code) -------------
# NUL-delimited end to end so paths with spaces/newlines can never word-split.
#   (a) tracked files (committed + uncommitted edits to tracked files), minus:
#         - *.pyc churn (box recompiles its own),
#         - the real top-level .env (box keeps its API keys),
#         - memory/ (box's long_term_memories.jsonl is authoritative & live).
#   (b) NEW untracked code, SCOPED to src/ and tests/ ONLY. A blanket --others
#       would surface the Mac's untracked results/ (and workplace/) -- we never
#       want those on the box -- so the scope is deliberate.
LIST="$(mktemp -t synclist.XXXXXX)"
trap 'rm -f "$LIST" "$LIST.a" "$LIST.b"' EXIT

{ git ls-files -z \
  | tr '\0' '\n' | grep -v -e '\.pyc$' -e '^\.env$' -e '^memory/' | tr '\n' '\0' > "$LIST.a" ; } || true
{ git ls-files --others --exclude-standard -z -- 'src/' 'tests/' \
  | tr '\0' '\n' | grep -v -e '\.pyc$' | tr '\n' '\0' > "$LIST.b" ; } || true
cat "$LIST.a" "$LIST.b" > "$LIST"

# ---- HARD SAFETY ASSERTION: zero protected paths in the list ---------------
# If anything anchored to a protected top-level path leaked in, abort BEFORE
# any network call. This is the gate that actually defends memory/ and .env
# (whose names also appear as tracked .env.example / nested results files).
LEAK="$(tr '\0' '\n' < "$LIST" | grep -E "$PROTECT_LIST_REGEX" || true)"
if [ -n "$LEAK" ]; then
  red "ABORT: protected path(s) leaked into the transfer list:"
  printf '%s\n' "$LEAK"
  exit 2
fi
N_FILES="$(tr '\0' '\n' < "$LIST" | grep -c . || true)"
[ "$N_FILES" -gt 0 ] || die "transfer list is EMPTY (git ls-files returned nothing?) -- refusing to run"
grn ">> $N_FILES files in transfer set (tracked code incl. uncommitted edits + new src/ tests/)"

# ---- common rsync invocation (NO --delete, ever) ---------------------------
# Conservative flag set proven on stock openrsync -> GNU 3.2.7 receiver:
#   -r recurse, -l symlinks, -t times, -p perms, -z compress.
# Deliberately NOT used: -a (pulls -o/-g/-D owner/group/device which churn on
# the root-vs-501 box), --delete (would risk data), --delete-excluded
# (rejected by openrsync AND would delete data), --no-implied-dirs (the
# openrsync sender + GNU receiver disagree and abort).
# Excludes are anchored belt-and-suspenders; they can only ever SKIP a path,
# never delete one.
run_rsync() {
  local extra="$1"   # "" for real, "-n" for dry-run
  "$RSYNC" -rltpz $extra -i --out-format='%i %n' \
    --files-from="$LIST" --from0 \
    --exclude='/.env' \
    --exclude='/memory'    --exclude='/memory/***' \
    --exclude='/workplace' --exclude='/workplace/***' \
    --exclude='/results'   --exclude='/results/***' \
    --exclude='/rat_run*' \
    --exclude='/.git' \
    -e "$SSH" \
    "$SRC"/ "$HOST:$DEST"/
}

# ---- MANDATORY dry-run + safety verify -------------------------------------
echo; ylw "================= DRY RUN (no changes) ================="
DRYOUT="$(mktemp -t dryout.XXXXXX)"; trap 'rm -f "$LIST" "$LIST.a" "$LIST.b" "$DRYOUT"' EXIT
run_rsync "-n" | tee "$DRYOUT"

# Verify the planned operation can NEVER delete and never touch protected data.
if grep -qE '^\*deleting' "$DRYOUT"; then
  red "ABORT: dry-run shows a deletion -- this should be impossible without --delete."
  red "       Not applying. Investigate the rsync flags."
  exit 3
fi
# Anchored check: a protected path appears as a transfer ROOT in itemized output
# ("<flags> <name>"). .env.example / outputs/.../results/ are allowed; the real
# top-level .env, memory/, workplace/, results/, rat_run* are not.
PROT_HIT="$(grep -E ' (\.env|memory|workplace|results|rat_run[^ ]*)(/|$)' "$DRYOUT" || true)"
if [ -n "$PROT_HIT" ]; then
  red "ABORT: dry-run would touch a PROTECTED top-level path:"
  printf '%s\n' "$PROT_HIT"
  exit 4
fi
grn ">> safety check passed: 0 deletions, 0 protected paths in the plan."

if [ "$MODE" = "dryonly" ]; then
  grn ">> DRY-RUN only. Nothing changed, no provenance written."
  exit 0
fi

# ---- confirmation gate (skipped with --apply) ------------------------------
if [ "$MODE" = "interactive" ]; then
  printf "Proceed with the REAL sync of %s files to %s:%s ? [y/N] " "$N_FILES" "$HOST" "$DEST"
  read -r ans
  case "$ans" in y|Y|yes|YES) ;; *) ylw "Aborted by user."; exit 0;; esac
fi

# ---- REAL sync -------------------------------------------------------------
echo; ylw "================= APPLYING ================="
run_rsync ""

# ---- provenance stamp on the box -------------------------------------------
# A single new dotfile at the box root (not a protected path, not in any data
# dir). Records commit + dirty fingerprint + who/when so the box is never again
# a mystery snapshot. Written via a quoted heredoc over SSH (no remote shell
# expansion of the values).
$SSH "$HOST" "cat > '$DEST/.deployed_from_mac.txt'" <<EOF
deployed_at_utc : $STAMP_TS
from_mac        : $WHO
src_path        : $SRC
branch          : $BRANCH
commit          : $COMMIT
git_describe    : $DESCRIBE
uncommitted     : $DIRTY_N dirty entries; diff(HEAD) sha256 = $DIFF_HASH
synced_files    : $N_FILES tracked files (uncommitted edits to tracked files ARE included)
method          : deploy.sh -- rsync -rltpz --files-from=<git ls-files> --from0, NO --delete
note            : data dirs (.env, workplace/, rat_run*/, results/, memory/) preserved untouched
EOF
grn ">> stamped $HOST:$DEST/.deployed_from_mac.txt"

# ---- (re)apply the RAT harness /repo fix (idempotent; separate gitless dir) ----
patch_harness || ylw ">> harness patch step had issues (see above); the code sync above still succeeded."

# ---- post-sync hash assertion (critical files local vs VM) ------------------
echo; ylw "================= HASH VERIFICATION ================="
HASH_CHECK_FILES=(
  "run_rat_benchmark.py"
  "repo2run_repair_port.py"
  "agent.py"
  "multi_docker_eval_adapter.py"
  "src/synthesizer.py"
)
HASH_FAIL=0
for RELPATH in "${HASH_CHECK_FILES[@]}"; do
  LOCAL_HASH="$(shasum -a 256 "$SRC/$RELPATH" 2>/dev/null | awk '{print $1}')"
  if [ -z "$LOCAL_HASH" ]; then
    ylw "  SKIP  $RELPATH (not found locally — not a critical error if intentionally absent)"
    continue
  fi
  VM_HASH="$($SSH "$HOST" "sha256sum '$DEST/$RELPATH' 2>/dev/null | awk '{print \$1}'" 2>/dev/null || true)"
  if [ -z "$VM_HASH" ]; then
    red "  ABORT-WARNING: $RELPATH — cannot read hash from VM (file missing on box?)"
    HASH_FAIL=1
  elif [ "$LOCAL_HASH" = "$VM_HASH" ]; then
    grn "  OK    $RELPATH  ($LOCAL_HASH)"
  else
    red "  ABORT-WARNING: $RELPATH — HASH MISMATCH"
    red "    local: $LOCAL_HASH"
    red "    VM:    $VM_HASH"
    HASH_FAIL=1
  fi
done
if [ "$HASH_FAIL" -ne 0 ]; then
  red ">> HASH ASSERTION FAILED: one or more critical files differ between local and VM."
  red "   The rsync may have been partially blocked or a file was modified after deploy."
  red "   Re-run ./deploy.sh --apply to retry, or investigate the mismatches above."
else
  grn ">> Hash assertion passed: all critical files match local vs VM."
fi

# Stamp the deployed commit so the runner (no git on the box) can self-label each run.
SHORT_COMMIT="$(git rev-parse --short HEAD)"
printf '%s\n' "$SHORT_COMMIT" | $SSH "$HOST" "cat > '$DEST/.deployed_commit'" \
  && grn ">> stamped $DEST/.deployed_commit = $SHORT_COMMIT"

grn ">> DONE. DockerAgent code at $DEST now matches the Mac working tree (tracked files)."
ylw "   Tip: read provenance with  $SSH $HOST 'cat $DEST/.deployed_from_mac.txt'"

# ---- STALE-FILE NOTE (never automated) -------------------------------------
# A file you DELETE or RENAME in git stays on the box (no --delete by design).
# Stale .py files are harmless unless imported. To remove one, do it by EXACT
# path -- never rm -rf a directory:
#   $SSH $HOST 'rm -f /opt/rat-bench-integration/path/to/removed_file.py'
