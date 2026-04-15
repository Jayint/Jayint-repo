#!/bin/bash

cd /testbed



cd /testbed

set +e
(
go build -buildvcs=false ./...
) && \
(
go build -buildvcs=false ./...
) && \
(
go test -race ./...
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
