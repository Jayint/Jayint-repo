#!/bin/bash

cd /testbed



cd /testbed

set +e
(
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17 -DEXTENSIONS=ON -DTESTS=ON -DCPPUTEST_FLAGS=ON
) && \
(
cmake --build build --parallel
) && \
(
ctest --test-dir build --output-on-failure
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
