#!/bin/bash

cd /testbed



set +e
(
cd build && cmake .. && make -j$(nproc)
) && \
(
build/tests/CppUTest/CppUTestTests
) && \
(
./build/tests/CppUTestExt/CppUTestExtTests
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
