#!/usr/bin/env bash
# Tier-0 (local, needs Docker): the baked base has the instrument the setup.sh
# preamble used to add, and the stock python:3.X-slim does not. Proves the bake
# is correct — the safety argument for deleting the preamble in §4.4c.
set -Euo pipefail
MINOR="${1:-3.11}"
for img in "python:${MINOR}-slim" "v3-base:${MINOR}"; do
  echo "== ${img} =="
  docker run --rm "$img" sh -c '
    command -v python >/dev/null 2>&1 && echo "python: ok" || echo "python: MISSING"
    python3 -m pip --version 2>&1 | head -1
    python3 -c "import pytest; print(\"pytest\", pytest.__version__)" 2>&1 | head -1
    python3 -c "import pytest_timeout; print(\"pytest-timeout: ok\")" 2>&1 | head -1
    command -v git >/dev/null 2>&1 && echo "git: ok" || echo "git: MISSING"
  ' || true
done
