# Failure Analysis — django-oauth/django-oauth-toolkit

**Harness status:** success | **True outcome:** success_tests_all_error | **Category:** test_harness_artifact | **Pytest:** 0 collected, 0 passed, 0 errors

## Root cause

The harness test runner executed `python -m pytest --co -q /testbed` without the required `DJANGO_SETTINGS_MODULE=tests.settings` environment variable, causing a conftest import error. The agent successfully configured the environment and verified 550 tests could be collected with the proper env vars (Step 13), but the harness's automatic test collection (`pytest --co`) runs without those variables and fails to load Django settings.

## Environment / trajectory state at termination

- **Agent steps:** 13 steps executed, agent concluded environment was fully configured
- **Installed:** All core dependencies (Django 6.0.6, oauth2-provider, pytest, pytest-django, pytest-cov, pytest-xdist, pytest-mock, etc.)
- **Editable install:** `pip install -e ".[test]"` succeeded and installed django-oauth-toolkit in editable mode
- **Last successful action:** Step 13 — agent ran `cd /app && PYTHONPATH=/app DJANGO_SETTINGS_MODULE=tests.settings pytest --collect-only -q --disable-warnings` and **successfully collected 550 tests**
- **Failure:** Harness test runner ignored the verified test command and instead ran bare `python -m pytest --co` without env vars, causing Django ImproperlyConfigured error on conftest import

## Key evidence

From agent Step 13 (successful):
```
Command succeeded.
[Observation]
tests/test_application_views.py::TestApplicationRegistrationView::test_application_registration_user
...
550 tests collected in 0.70s
```

From harness eval runner (failure):
```
🔧 Command: python -m pytest --co -q /testbed
📋 Pytest Collect output:
ImportError while loading conftest '/testbed/tests/conftest.py'.
...
USER_SETTINGS = getattr(settings, "OAUTH2_PROVIDER", None)
E   django.core.exceptions.ImproperlyConfigured: Requested setting OAUTH2_PROVIDER, but settings are not configured. You must either define the environment variable DJANGO_SETTINGS_MODULE or call settings.configure() before accessing settings.
```

The generated eval_script does include the env vars:
```bash
cd /testbed && PYTHONPATH=/testbed DJANGO_SETTINGS_MODULE=tests.settings pytest --collect-only -q --disable-warnings
```

But the harness test collector runs a default pytest collection without these env vars.

## Takeaway for DockerAgent

The agent completed its job correctly — it installed dependencies, set up the editable package install, and verified that tests could be collected with proper environment setup. The failure is in the evaluation harness: the harness test runner should either (a) respect the `DJANGO_SETTINGS_MODULE` environment variable encoding in the test command, or (b) run the eval_script that was provided instead of auto-generating a bare pytest call.

## Fixability

**test_harness_artifact** — The harness's automatic pytest collection phase does not use the environment variables the agent verified are necessary. This is a framework-level limitation, not a DockerAgent or package configuration issue. The correct test command with env vars was verified to work (550 tests collected), but the harness bypasses it.
