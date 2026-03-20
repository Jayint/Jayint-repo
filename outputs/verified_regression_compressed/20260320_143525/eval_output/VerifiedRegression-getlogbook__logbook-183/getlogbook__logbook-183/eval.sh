#!/bin/bash

cd /testbed


# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || exit 1
fi

# Runtime preparation commands verified by the setup agent
set -e
redis-server --daemonize yes
set +e

cd /testbed

set +e
(
pytest tests
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
