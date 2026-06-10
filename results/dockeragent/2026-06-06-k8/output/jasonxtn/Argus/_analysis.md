# Failure Analysis — jasonxtn/Argus

**Harness status:** success | **True outcome:** success_tests_all_error | **Category:** test_deps_not_installed | **Pytest:** 4 errors (all ModuleNotFoundError)

## Root cause

The Dockerfile synthesized by the agent excluded the editable package installation (`pip install -e . --no-build-isolation`) from its final build recipe, despite this command being executed and succeeding in the sandbox. The build recipe contains only `pip install setuptools wheel build`, but omits the critical step that installs the project's declared dependencies (requests, rich, dnspython, etc.). When the eval image runs pytest, the test modules fail to import their runtime dependencies because the environment is incomplete.

## Environment / trajectory state at termination

- **Steps used:** 14 agent steps completed successfully
- **Installed in sandbox:** setuptools, wheel, build, argus-recon package (editable) + all 12 declared dependencies (requests, urllib3, rich, dnspython, paramiko, beautifulsoup4, lxml, Jinja2, packaging, python-slugify, mmh3, idna)
- **Installed in eval image:** setuptools, wheel, build, pytest (added by harness) — but NOT argus-recon or its dependencies
- **Last action:** Agent reported "Success" after pytest collection appeared to succeed with 1 test collected (recursive_nameserver_leak_test::test_ns)
- **Missing in final Dockerfile:** `pip install -e . --no-build-isolation` command

## Key evidence

```
Step 9 in sandbox (succeeded):
RUN pip install -e . --no-build-isolation
Requirement already satisfied: requests<3,>=2.32 in /usr/local/lib/python3.12/site-packages
Collecting rich<14,>=13 (from argus-recon==2.0)
Successfully installed argus-recon-2.0 lxml-5.4.0 mmh3-4.1.0 packaging-24.2 paramiko-3.5.1 rich-13.9.4

Build recipe excluded this command:
"reason": "failed initial attempt due to missing build dependencies; succeeded after installing setuptools wheel build"

Eval image pytest result:
E   ModuleNotFoundError: No module named 'requests'
E   ModuleNotFoundError: No module named 'rich'
E   ModuleNotFoundError: No module named 'dns'
```

## Takeaway for DockerAgent

The build recipe synthesizer is conflating "command failure→recovery" with "command not-part-of-build." When `pip install -e .` fails initially (before setuptools is installed) and then succeeds after installing build tools, the successful run should be included in the build recipe IF it represents necessary state (which it does — installing the package and runtime dependencies). The exclusion logic marked it as "failed initial attempt" and did not re-include the subsequent successful execution. This creates a hollow Dockerfile that appears to build but cannot run tests because the package itself is never installed.

## Fixability

**trivial_synthesizer_fix** — The agent correctly performed `pip install -e .` after installing setuptools. The planner/synthesizer should track state-changing commands and include the final successful form in the build recipe. The fix is to improve the command filtering logic to preserve successful editable installs even if a prior attempt failed.
