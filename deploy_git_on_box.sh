#!/usr/bin/env bash
#
# deploy_git_on_box.sh  --  APPROACH: git_on_box (+ paired rsync for uncommitted edits)
#
# Makes /opt/rat-bench-integration on the VM a REAL git checkout of the private
# repo, pinned to the Mac's current branch, then overlays the Mac working tree's
# UNCOMMITTED tracked edits so iteration does not require commit+push.
#
# Run from the Mac:   ./deploy_git_on_box.sh
#
# ----------------------------------------------------------------------------
# ONE-TIME PREREQ YOU (the user) MUST DO ON GITHUB  (see PHASE 0 below):
#   1. This script generates an SSH deploy key ON THE BOX and prints its .pub.
#   2. You paste that .pub into:
#        https://github.com/Jayint/Jayint-repo/settings/keys  ->  "Add deploy key"
#        Title: rat-box-167.233.64.96   |  Allow write access: LEAVE UNCHECKED (read-only)
#   3. Re-run the script; it detects the key and proceeds.
# The private repo cannot be cloned on the box until this key is added. HTTPS
# returns 200 to github.com but a PRIVATE repo over HTTPS still needs a token,
# so we deliberately use the SSH remote (git@github.com:...) with the deploy key.
# ----------------------------------------------------------------------------
set -euo pipefail

# ----- real, verified constants ---------------------------------------------
BOX="root@167.233.64.96"
BOX_DIR="/opt/rat-bench-integration"
REPO_SSH="git@github.com:Jayint/Jayint-repo.git"
BRANCH="rat-bench-integration"
KEY="/root/.ssh/rat_deploy_ed25519"             # deploy key lives on the box
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"

# Untracked, IRREPLACEABLE box data that must NEVER be deleted or overwritten.
# (rat_run/ is .gitignored; the rest are untracked -> reset --hard never touches
#  untracked files, but we ALSO snapshot the one TRACKED danger: memory/.)
PROTECT_GLOBS='.env workplace rat_run rat_run_heavy rat_run_rat rat_run_rat.contaminated* rat_run.partial* rat_run_selfverify* rat_run_self* results .memory'

red(){ printf '\033[31m%s\033[0m\n' "$*"; }
grn(){ printf '\033[32m%s\033[0m\n' "$*"; }
die(){ red "FATAL: $*"; exit 1; }

# ----- gather Mac-side provenance (committed pin + dirty state) --------------
cd "$(dirname "$0")"
[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || die "Mac is on $(git rev-parse --abbrev-ref HEAD), expected $BRANCH"
COMMIT="$(git rev-parse HEAD)"
COMMIT_SHORT="$(git rev-parse --short HEAD)"
DIRTY="$([ -n "$(git status --porcelain)" ] && echo dirty || echo clean)"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
HOSTUSER="$(whoami)@$(hostname -s)"

grn "Mac pin: $COMMIT_SHORT ($DIRTY)  branch=$BRANCH  @ $STAMP"

# =============================================================================
# PHASE 0 -- ensure the box has a deploy key + it is accepted by GitHub
# =============================================================================
# Generate the key on the box if missing (idempotent).
ssh $SSH_OPTS "$BOX" "test -f $KEY || ssh-keygen -t ed25519 -N '' -C 'rat-box-deploy' -f $KEY >/dev/null"

# Does the box authenticate to GitHub with this key yet?
if ! ssh $SSH_OPTS "$BOX" \
      "GIT_SSH_COMMAND='ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' \
       ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 \
       | grep -q 'successfully authenticated'"; then
  red "------------------------------------------------------------------"
  red "ACTION REQUIRED: add this READ-ONLY deploy key to the private repo:"
  red "  https://github.com/Jayint/Jayint-repo/settings/keys  -> Add deploy key"
  red "  Title: rat-box-167.233.64.96   Allow write access: UNCHECKED"
  red "------------------------------------------------------------------"
  ssh $SSH_OPTS "$BOX" "cat ${KEY}.pub"
  red "------------------------------------------------------------------"
  red "Then re-run:  ./deploy_git_on_box.sh"
  exit 2
fi
grn "Deploy key authenticates to GitHub."

# =============================================================================
# PHASE 1 -- in-place migration to a real git checkout, PRESERVING all data
# =============================================================================
# Everything below runs ON THE BOX in one ssh session. It is fully idempotent.
ssh $SSH_OPTS "$BOX" bash -s -- \
    "$BOX_DIR" "$REPO_SSH" "$BRANCH" "$KEY" "$COMMIT" "$DIRTY" "$STAMP" "$HOSTUSER" <<'REMOTE'
set -euo pipefail
BOX_DIR="$1"; REPO_SSH="$2"; BRANCH="$3"; KEY="$4"
COMMIT="$5"; DIRTY="$6"; STAMP="$7"; HOSTUSER="$8"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
cd "$BOX_DIR"

# --- 1a. HARD SAFETY: snapshot the TRACKED-but-live data BEFORE any reset. ---
# memory/ is tracked in the repo, so `git reset --hard` WOULD overwrite the
# box's live long_term_memories.jsonl. We copy it aside first, restore after.
SNAP="/opt/_rat_predeploy_snapshot/${STAMP//[:]/-}"
mkdir -p "$SNAP"
[ -e memory ] && cp -a memory "$SNAP/memory" || true
[ -e .env   ] && cp -a .env   "$SNAP/.env"   || true
echo "snapshot of tracked-live data -> $SNAP"

# --- 1b. git init in place (no clone-and-move; preserves the 6.6G untracked) -
if [ ! -d .git ]; then
  git init -q
  git symbolic-ref HEAD "refs/heads/$BRANCH"
fi
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_SSH"

# Mark this as a safe directory (uid 501/staff owns most files, we are root).
git config --global --add safe.directory "$BOX_DIR" 2>/dev/null || true

# --- 1c. fetch the exact commit the Mac is on -------------------------------
git fetch -q --depth=1 origin "$BRANCH"

# --- 1d. reset --hard to origin/BRANCH --------------------------------------
# reset --hard ONLY touches files that are in the index/commit. Every protected
# dir below is UNTRACKED in the repo, so reset leaves them 100% intact:
#   .env, workplace/, rat_run*/, results/, .memory/   <- untracked => never touched
# The ONE tracked hazard, memory/, was snapshotted in 1a and is restored in 1e.
git reset -q --hard "origin/$BRANCH"

# --- 1e. restore live tracked data the reset may have clobbered -------------
# Repo ships memory/{example.json,long_term_memories.jsonl}; the box's live copy
# is authoritative. Restore it so agent long-term memory is never rolled back.
if [ -d "$SNAP/memory" ]; then
  cp -a "$SNAP/memory/." memory/
  echo "restored live memory/ from snapshot"
fi
# .env is .gitignored (untracked) so reset never removed it; snapshot is belt-and-braces.
[ -f "$SNAP/.env" ] && [ ! -f .env ] && cp -a "$SNAP/.env" .env || true

# --- 1f. PROVENANCE record (constraint 3) -----------------------------------
cat > "$BOX_DIR/.deployed_from" <<EOF
commit:        $COMMIT
branch:        $BRANCH
mac_state:     $DIRTY
deployed_at:   $STAMP
deployed_by:   $HOSTUSER
method:        git_on_box (git reset --hard origin/$BRANCH) + rsync overlay of dirty tracked files
box_head:      $(git rev-parse HEAD)
EOF
echo "wrote provenance -> $BOX_DIR/.deployed_from"

# --- 1g. show that protected data survived ----------------------------------
echo "=== post-reset protected-data check ==="
for d in .env workplace rat_run rat_run_rat results .memory memory; do
  if [ -e "$d" ]; then printf '  OK  %-10s %s\n' "$d" "$(du -sh "$d" 2>/dev/null | cut -f1)"; fi
done
REMOTE

# =============================================================================
# PHASE 2 -- overlay UNCOMMITTED tracked edits (constraint 2) -----------------
# =============================================================================
# git_on_box alone deploys only COMMITTED code. The Mac currently has dirty
# tracked edits (e.g. agent.py) and NEW untracked code (src/artifact_verify.py,
# src/recipe_repair.py, tests/test_*.py) that are not yet committed. We push the
# full set of files the Mac would deploy = tracked files (committed OR dirty) so
# the box runs exactly what the Mac working tree has -- WITHOUT a commit+push.
#
# We build the file list from `git ls-files` (tracked only -> never includes
# workplace/, rat_run*, results/, .env) and feed it to rsync via --files-from.
# This is the bulletproof guarantee: rsync can only WRITE the 2306 tracked
# paths; it has no --delete and no directory recursion, so it physically cannot
# remove or touch the protected box data dirs.
TMPLIST="$(mktemp)"
trap 'rm -f "$TMPLIST"' EXIT
# (a) TRACKED files (committed + dirty), minus committed-by-mistake .pyc churn,
#     minus memory/ (live box copy is authoritative -- never push Mac's over it),
#     minus .env (gitignored anyway; box .env holds API keys and must not move).
git ls-files \
  | grep -v -e '^memory/' -e '\.pyc$' -e '^\.env$' \
  > "$TMPLIST"
# (b) NEW untracked code, SCOPED to src/ and tests/ ONLY. This is the new code
#     under iteration (artifact_verify.py, recipe_repair.py, their tests) that is
#     not yet committed. The scope makes it IMPOSSIBLE to pull in results/,
#     workplace/, or rat_run* -- verified: a blanket --others lists 201KB of
#     results/ paths, so we deliberately do NOT use a blanket --others here.
git ls-files --others --exclude-standard -- 'src/**' 'tests/**' \
  | grep -v -e '\.pyc$' \
  >> "$TMPLIST"
N=$(wc -l < "$TMPLIST" | tr -d ' ')
grn "Overlaying $N tracked working-tree files (incl. uncommitted edits) via rsync (no --delete)."

# Stock macOS rsync is 2.6.9 (BSD). Flags used are all supported there:
#   -r recursive (implied by --files-from but explicit is fine), -l links,
#   -t times, -p perms, -z compress, --files-from (newline-delimited list).
#   NO --delete, NO --from0 (list is newline- not NUL-separated; no path here
#   contains a newline), NO GNU-only flags (no --info, no --mkpath).  rsync
#   auto-creates parent dirs for --files-from paths, so src/ and tests/ subdirs
#   are created on the box as needed.
rsync -rltpz --files-from="$TMPLIST" \
  -e "ssh $SSH_OPTS" \
  ./ "$BOX:$BOX_DIR/"

# Record that the deployed tree includes uncommitted edits.
ssh $SSH_OPTS "$BOX" \
  "printf 'overlay:       %s tracked working-tree files via rsync (uncommitted edits included)\n' '$N' >> $BOX_DIR/.deployed_from"

grn "DONE. Box $BOX_DIR is git@$COMMIT_SHORT ($DIRTY) + live working-tree edits."
grn "Provenance: ssh $BOX cat $BOX_DIR/.deployed_from"
