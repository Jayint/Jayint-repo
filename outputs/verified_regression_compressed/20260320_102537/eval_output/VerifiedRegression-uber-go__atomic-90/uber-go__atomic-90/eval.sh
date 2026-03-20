#!/bin/bash

cd /testbed



cd /testbed

set +e
(
go test -race ./...
) && \
(
go test -coverprofile=cover.out -coverpkg $(go list -find ./... | grep -v "go.uber.org/atomic/internal/gen-atomicint" | grep -v "go.uber.org/atomic/internal/gen-atomicwrapper" | paste -sd,) -v ./...
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
