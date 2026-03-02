#!/bin/bash

cd /testbed

python -m pytest tests/test_mail_handler.py::test_mail_handler_arguments -v
TEST_EXIT_CODE=$?

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
