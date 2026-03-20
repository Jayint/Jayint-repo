#!/bin/bash

cd /testbed


# Check if package.json was modified and reinstall if needed
if [ -f package.json ]; then
    npm install || exit 1
fi


set +e
(
npm test
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
