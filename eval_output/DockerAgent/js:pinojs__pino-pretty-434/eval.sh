#!/bin/bash

cd /testbed


# Check if package.json was modified and reinstall if needed
if [ -f package.json ]; then
    npm install || true
fi

npm test
TEST_EXIT_CODE=$?

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
