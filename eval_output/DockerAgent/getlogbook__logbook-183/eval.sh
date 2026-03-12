#!/bin/bash

cd /testbed


# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || true
fi

python -m pytest tests/test_mail_handler.py::test_mail_handler_arguments -v
TEST_EXIT_CODE=$?

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
