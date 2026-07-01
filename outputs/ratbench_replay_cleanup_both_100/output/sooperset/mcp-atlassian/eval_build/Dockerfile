FROM python:3.10
RUN (python -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || python3 -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry)
WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=5

RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

COPY . /app

RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'python -m pip install --upgrade pip setuptools wheel' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN cd /app && python -m pip install -e ".[dev]"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'python -m pip install pytest pytest-cov pytest-asyncio pytest-anyio ruff black mypy jinja2 pyyaml types-PyYAML' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install uv' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then break; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; if [ "$JAYINT_PIP_STATUS" -ne 0 ]; then curl -L --retry 5 --retry-delay 2 --retry-connrefused --fail --show-error --silent -o /tmp/jayint-uv-install.sh https://astral.sh/uv/install.sh && sh /tmp/jayint-uv-install.sh; fi
