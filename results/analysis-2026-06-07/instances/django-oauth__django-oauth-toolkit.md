# django-oauth/django-oauth-toolkit

- **DA pass-rate:** 0.0 (0/0 tests) | **RAT pass-rate:** 1.0 (550/550 tests) | **bucket:** DA_LOSS
- **DA build_success/test_success:** False/False | **error_breakdown:** pytest_collect_success=false, collection error: ImproperlyConfigured DJANGO_SETTINGS_MODULE

## Failure stage & category
**Stage:** test_collection  
**Category:** test_collection_error

## Root cause (why DA lost)

DA's pytest collection failed because the required Django environment variable `DJANGO_SETTINGS_MODULE=tests.settings` was not properly set in the container shell environment. While DA attempted an inline env var override (`DJANGO_SETTINGS_MODULE=tests.settings python -m pytest --collect-only`), the collection still failed with `django.core.exceptions.ImproperlyConfigured: Requested setting OAUTH2_PROVIDER, but settings are not configured`. This suggests the inline override was insufficient; the pytest conftest.py imports oauth2_provider.models which in turn imports oauth2_provider.settings, and that import happens before pytest can apply environment configurations. Self-verify then rejected the bundle and auto-finalized with a no-op test command, resulting in pytest_collect_success=false and 0 tests collected.

## What RAT did differently

RAT explicitly set the environment variables as persistent shell exports before any pytest collection attempt:
- `export DJANGO_SETTINGS_MODULE=tests.settings` (outer_commands.json line 13)
- `export PYTHONPATH=/repo` (outer_commands.json line 14)
- Then ran `python -m pytest --co -q /repo` (outer_commands.json line 15)
- Additionally persisted these exports to `/root/.bashrc` to ensure they were available for the final test execution (outer_commands.json lines 17-18)

This ensured the Django settings module was available to Python at module-import time during conftest loading, not just as an inline prefix.

## Evidence

**DA pytest collection error** (run_pytest_collect_results.json):
```
ImproperlyConfigured: Requested setting OAUTH2_PROVIDER, but settings are not configured. 
You must either define the environment variable DJANGO_SETTINGS_MODULE or call settings.configure() 
before accessing settings.
```

Full traceback:
```
ImportError while loading conftest '/testbed/tests/conftest.py'.
tests/conftest.py:14: in <module>
    from oauth2_provider.models import get_application_model, get_id_token_model
oauth2_provider/models.py:24: in <module>
    from .generators import generate_client_id, generate_client_secret
oauth2_provider/generators.py:4: in <module>
    from .settings import oauth2_settings
oauth2_provider/settings.py:32: in <module>
    USER_SETTINGS = getattr(settings, "OAUTH2_PROVIDER", None)
```

**DA verified test commands** (docker-oauth__django-oauth-toolkit.json):
```
DJANGO_SETTINGS_MODULE=tests.settings python -m pytest --collect-only -q --disable-warnings
```

**RAT command sequence** (outer_commands.json lines 10-15):
```
pip install -q -e ".[test]" -i https://mirrors.aliyun.com/pypi/simple    -> rc 0
run-pytest-collect                                                        -> rc 0
cat /repo/tests/settings.py                                              -> rc 0
export DJANGO_SETTINGS_MODULE=tests.settings                             -> rc 0
export PYTHONPATH=/repo                                                  -> rc 0
python -m pytest --co -q /repo                                           -> rc 0
```

## Fix recommendation (for our agent.py / src/synthesizer.py / src/recipe_repair.py)

1. **In src/synthesizer.py (when detecting Django projects):** When a tests/settings.py or Django test configuration is detected, explicitly generate shell export commands for DJANGO_SETTINGS_MODULE and PYTHONPATH *before* any pytest collection command, rather than relying on inline environment variable prefixes.

2. **In src/synthesizer.py (Dockerfile generation):** After pip install, add RUN commands that export these variables and persist them to ~/.bashrc:
   ```dockerfile
   RUN export DJANGO_SETTINGS_MODULE=tests.settings && \
       export PYTHONPATH=/app && \
       echo 'export DJANGO_SETTINGS_MODULE=tests.settings' >> /root/.bashrc && \
       echo 'export PYTHONPATH=/app' >> /root/.bashrc
   ```

3. **In src/recipe_repair.py:** When pytest collection fails with ImproperlyConfigured, diagnose whether DJANGO_SETTINGS_MODULE is set in the shell environment, and suggest explicit export commands as a repair strategy.

4. **Preferred pattern:** For Django projects, use the verified setup flow that ensures environment variables are exported to shell *before* any subprocess calls that might import the settings module.
