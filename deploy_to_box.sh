#!/usr/bin/env bash
# deploy_to_box.sh — push Mac working-tree code (tracked files, incl. uncommitted
# edits) to the VM DockerAgent root, WITHOUT ever touching the box's irreplaceable
# data dirs. Run from the Mac after editing. Single command, no commit/push needed.
#
#   ./deploy_to_box.sh            # sync + stamp provenance
#   ./deploy_to_box.sh --dry-run  # show exactly what would change, change nothing
#
# Approach: git ls-files -z  ->  rsync --files-from=- --from0 over ssh.
# Only git-tracked files are ever in the transfer list, so untracked box data is
# never enumerated and never deleted. Protected top-level dirs are *additionally*
# stripped from the list AND excluded in rsync (belt + suspenders).
set -euo pipefail

# ---- config (real, verified) ------------------------------------------------
SRC="/Users/john/rat-bench-integration"                 # Mac working tree (git repo)
HOST="root@167.233.64.96"                                # VM box (key auth, no pw)
DEST="/opt/rat-bench-integration"                        # DOCKERAGENT_ROOT on box
RSYNC="/usr/bin/rsync"                                   # macOS stock (openrsync, BSD)
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"

# Top-level paths on the box that MUST NEVER be created/overwritten/deleted.
# NOTE: anchored with leading '/' so nested tracked paths (e.g.
# outputs/repo2run_benchmark/results/) are NOT affected — only the box's
# top-level data dirs are protected. 'memory/' is partially TRACKED in git
# (example.json, long_term_memories.jsonl) yet the box copy is irreplaceable,
# so it is force-protected here.
PROTECT_REGEX='^(memory/|workplace/|results/|rat_run|\.env$)'   # for source-side list filter (anchored at repo root)

DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

cd "$SRC"

# ---- preflight --------------------------------------------------------------
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "ERR: $SRC is not a git repo"; exit 1; }
$SSH "$HOST" "test -d '$DEST'" || { echo "ERR: $DEST missing on box (or ssh failed)"; exit 1; }

COMMIT="$(git rev-parse HEAD)"
DESCRIBE="$(git describe --always --dirty 2>/dev/null || echo "$COMMIT")"
DIRTY_N="$(git status --porcelain | wc -l | tr -d ' ')"
STAMP_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo ">> deploy $DESCRIBE  ($DIRTY_N dirty entries)  ->  $HOST:$DEST  ${DRY:+[DRY-RUN]}"

# ---- build the transfer list (tracked files only, danger paths stripped) ----
# git ls-files -z lists tracked files INCLUDING ones with uncommitted edits.
# grep -z with anchored regex removes protected top-level paths from the list,
# so they are never even candidates for transfer.
LIST="$(mktemp -t synclist.XXXXXX)"
trap 'rm -f "$LIST"' EXIT
git ls-files -z | grep -zvE "$PROTECT_REGEX" > "$LIST"

# Hard assertion: the list must contain ZERO protected paths.
if tr '\0' '\n' < "$LIST" | grep -qE "$PROTECT_REGEX"; then
  echo "ABORT: protected path leaked into transfer list"; exit 2
fi
N_FILES="$(tr '\0' '\n' < "$LIST" | grep -c . || true)"
echo ">> $N_FILES tracked files in transfer set"

# ---- the sync ---------------------------------------------------------------
# -a archive; --files-from/--from0 read NUL list from stdin; excludes are
# anchored to the transfer root ('/x') as a second guard. NO global --delete:
# files removed from git are simply absent from the list (they go stale on the
# box but are NEVER deleted) — this is the price of guaranteeing zero data loss
# on a box where untracked dirs sit alongside tracked code. Stale-file cleanup,
# if ever wanted, is a separate explicitly-scoped op (see TIDY note below).
#
# Flag note: macOS openrsync REJECTS --delete-missing-args, so cross-tool delete
# of git-removed files is intentionally NOT attempted here.
# (Do NOT use --no-implied-dirs: the macOS openrsync sender + GNU 3.2.7 receiver
#  disagree on it and the receiver aborts with "invalid path from sender". With
#  implied dirs ON, rsync creates only the parent dirs of listed files as needed.)
$RSYNC -a --human-readable $DRY \
  --files-from="$LIST" --from0 \
  --exclude='/memory'    --exclude='/memory/***' \
  --exclude='/workplace' --exclude='/workplace/***' \
  --exclude='/rat_run*' \
  --exclude='/results'   --exclude='/results/***' \
  --exclude='/.env' \
  --exclude='/.git' \
  --out-format='%i %n' \
  -e "$SSH" \
  "$SRC"/ "$HOST:$DEST"/

# ---- provenance stamp -------------------------------------------------------
# Written ONLY on a real (non-dry) run, as a single new file at the box root.
if [ -z "$DRY" ]; then
  $SSH "$HOST" "cat > '$DEST/.deployed_from_mac.txt'" <<EOF
commit:    $COMMIT
describe:  $DESCRIBE
dirty:     $DIRTY_N uncommitted entries (tracked edits ARE included in this sync)
synced:    $N_FILES tracked files
from:      $SRC  (branch $(git rev-parse --abbrev-ref HEAD))
by:        $(whoami)@$(hostname -s)
at:        $STAMP_TS
tool:      deploy_to_box.sh (rsync --files-from git ls-files)
EOF
  echo ">> stamped $DEST/.deployed_from_mac.txt"
  echo ">> DONE. DockerAgent at $DEST now matches Mac working tree (tracked files)."
else
  echo ">> DRY-RUN complete. No files changed, no stamp written."
fi

# TIDY note (optional, NOT run automatically): to remove a file you deleted from
# git that is now stale on the box, delete it by exact path, e.g.
#   ssh $HOST 'rm -f /opt/rat-bench-integration/path/to/removed_file.py'
# Never 'rm -rf' a directory on the box.
