#!/bin/bash

cd /testbed



cd /testbed

set +e
(
cd /testbed && rm -rf build && mkdir -p build && cd build && cmake -S .. -B . -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17 -DTESTS=ON -DEXTENSIONS=ON && cmake --build . --parallel
) && \
(
ctest --test-dir /testbed/build --output-on-failure
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
