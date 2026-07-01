FROM python:3.10
RUN (python -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || python3 -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry)
WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=5

RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

COPY . /app

RUN JAYINT_APT_ATTEMPT=1; JAYINT_APT_MAX_ATTEMPTS=3; JAYINT_APT_STATUS=1; while [ "$JAYINT_APT_ATTEMPT" -le "$JAYINT_APT_MAX_ATTEMPTS" ]; do rm -rf /var/lib/apt/lists/*; DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update && apt-get install -y libssl-dev libffi-dev build-essential libxml2-dev libxslt1-dev' && JAYINT_APT_STATUS=0 && break; JAYINT_APT_STATUS=$?; (apt-get clean >/dev/null 2>&1 || true); rm -rf /var/lib/apt/lists/*; if [ "$JAYINT_APT_ATTEMPT" -eq "$JAYINT_APT_MAX_ATTEMPTS" ]; then exit "$JAYINT_APT_STATUS"; fi; JAYINT_APT_ATTEMPT=$((JAYINT_APT_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_APT_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'python -m pip install --upgrade pip setuptools wheel' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN cd /app && PDM_BUILD_SCM_VERSION=0.1.0 pip install -e . --no-deps
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install pytest pytest-asyncio' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install '"'"'httpx<0.28'"'"' --force-reinstall' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
