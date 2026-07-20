#!/usr/bin/env bash
# Tier-0 (local, needs Docker): the baked base has the instrument the setup.sh
# floor provides, and the stock python:3.X-slim does not. Proves the bake is
# correct. The stock side is INFORMATIONAL (its pytest/git misses are EXPECTED);
# the v3-base side is a GATE — this script exits non-zero if the baked image is
# missing or lacks any instrument (pip, pytest, pytest-timeout, git, python).
set -Euo pipefail
MINOR="${1:-3.11}"

echo "== python:${MINOR}-slim (stock, informational) =="
docker run --rm "python:${MINOR}-slim" sh -c '
  command -v python >/dev/null 2>&1 && echo "python: ok" || echo "python: MISSING"
  python3 -m pip --version 2>&1 | head -1
  python3 -c "import pytest; print(\"pytest\", pytest.__version__)" 2>&1 | head -1
  python3 -c "import pytest_timeout; print(\"pytest-timeout: ok\")" 2>&1 | head -1
  command -v git >/dev/null 2>&1 && echo "git: ok" || echo "git: MISSING"
' || echo "(stock probe failed — informational only)"

echo "== v3-base:${MINOR} (baked, ASSERTED) =="
if ! docker image inspect "v3-base:${MINOR}" >/dev/null 2>&1; then
  echo "FAIL: image v3-base:${MINOR} is not built (run scripts/build_v3_base.sh)"
  exit 1
fi
# Each check must pass INSIDE the container; the outer `docker run` propagates the
# in-container exit code, and `set -e` aborts on the first missing instrument.
docker run --rm "v3-base:${MINOR}" sh -c '
  set -e
  command -v python  >/dev/null 2>&1 || { echo "FAIL: python missing"; exit 1; }
  python3 -m pip --version >/dev/null 2>&1 || { echo "FAIL: pip missing"; exit 1; }
  python3 -c "import pytest" || { echo "FAIL: pytest missing"; exit 1; }
  python3 -c "import pytest_timeout" || { echo "FAIL: pytest-timeout missing"; exit 1; }
  command -v git >/dev/null 2>&1 || { echo "FAIL: git missing"; exit 1; }
  echo "v3-base:'"${MINOR}"' instrument OK: $(python3 -c "import pytest; print(\"pytest\", pytest.__version__)")"
'
