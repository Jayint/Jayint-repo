#!/bin/bash

cd /testbed


# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || exit 1
fi


cd /testbed

set +e
(
python -m pytest tests/test_ticketing.py::test_basic_ticketing
) && \
(
python -m pytest tests/ --ignore=tests/test_queues.py
) && \
(
python -m pytest tests/test_queues.py --collect-only
) && \
(
python -m pytest tests/test_queues.py::test_zeromq_handler tests/test_queues.py::test_zeromq_background_thread tests/test_queues.py::test_missing_zeromq tests/test_queues.py::test_multi_processing_handler tests/test_queues.py::test_threaded_wrapper_handler tests/test_queues.py::test_execnet_handler tests/test_queues.py::test_subscriber_group
) && \
(
python -m pytest tests/ -k "not redis"
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
