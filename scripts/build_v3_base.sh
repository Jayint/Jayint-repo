#!/usr/bin/env bash
# Build + tag the v3-base:3.X instrument-baked base images (spec §4.4).
# Built on the VM (or locally for the Tier-0 recipe smoke); no registry/push.
#
# NOT -e: a single archived-apt minor (3.6/3.7 debian repos have moved to
# archive.debian.org) must not block the rest of the matrix. Failures are
# collected and the script exits non-zero iff any minor failed.
set -Euo pipefail
MINORS=(3.6 3.7 3.8 3.9 3.10 3.11 3.12 3.13 3.14)
DF="$(cd "$(dirname "$0")/.." && pwd)/docker/v3-base/Dockerfile"
CTX="$(dirname "$DF")"
built=()
failed=()
for m in "${MINORS[@]}"; do
  echo "== building v3-base:${m} =="
  if docker build --build-arg "PY_MINOR=${m}" -t "v3-base:${m}" -f "$DF" "$CTX"; then
    built+=("$m")
  else
    echo "!! FAILED v3-base:${m}"
    failed+=("$m")
  fi
done
echo "built:  ${built[*]:-none}"
echo "failed: ${failed[*]:-none}"
[ ${#failed[@]} -eq 0 ]
