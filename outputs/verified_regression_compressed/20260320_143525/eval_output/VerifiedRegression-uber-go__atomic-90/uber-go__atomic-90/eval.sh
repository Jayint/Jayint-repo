#!/bin/bash

cd /testbed



cd /testbed

set +e
(
go test -race ./...
) && \
(
go test -coverprofile=cover.out -coverpkg go.uber.org/atomic -v ./...
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
