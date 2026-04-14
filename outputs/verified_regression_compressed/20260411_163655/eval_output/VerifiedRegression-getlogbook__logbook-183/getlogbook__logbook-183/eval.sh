#!/bin/bash

cd /testbed


# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || exit 1
fi


cd /testbed

set +e
(
DISABLE_LOGBOOK_CEXT=True python setup.py develop && pytest -r s tests/
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
