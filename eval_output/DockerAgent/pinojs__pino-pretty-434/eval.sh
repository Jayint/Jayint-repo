#!/bin/bash

cd /testbed

npm test
TEST_EXIT_CODE=$?

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
