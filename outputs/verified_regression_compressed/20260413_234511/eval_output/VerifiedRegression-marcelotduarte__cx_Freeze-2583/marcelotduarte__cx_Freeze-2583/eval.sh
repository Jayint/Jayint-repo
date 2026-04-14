#!/bin/bash

cd /testbed


# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || exit 1
fi


cd /testbed

set +e
(
pytest tests/ -v --no-cov --ignore=tests/test_command_bdist_appimage.py --ignore=tests/test_command_bdist_deb.py --ignore=tests/test_command_bdist_dmg.py --ignore=tests/test_command_bdist_mac.py --ignore=tests/test_command_bdist_rpm.py --ignore=tests/test_command_bdist_msi.py --ignore=tests/test_winmsvcr.py --ignore=tests/test_winversioninfo.py --ignore=tests/test_windows_manifest.py --ignore=tests/test_plist_items.py -q
)
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
