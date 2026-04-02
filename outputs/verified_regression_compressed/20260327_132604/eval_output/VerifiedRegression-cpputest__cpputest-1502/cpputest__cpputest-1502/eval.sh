#!/bin/bash

cd /testbed



cd /testbed

set +e
(
mkdir -p cpputest_build && cd cpputest_build && autoreconf -i .. && ../configure && make tdd
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
