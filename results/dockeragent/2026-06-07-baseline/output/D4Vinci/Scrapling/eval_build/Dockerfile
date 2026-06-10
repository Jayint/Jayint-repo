FROM python:3.13
WORKDIR /testbed

# Configure apt reliability for eval image builds
RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

# Install git for cloning
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)

# Dependency-manager helpers inferred from verified setup
# No extra dependency bootstrap needed

# Clone repository and checkout base commit
RUN git clone https://github.com/D4Vinci/Scrapling /testbed
# No base commit provided; using repository default branch HEAD

# Agent's verified setup instructions
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install -e ".[fetchers]"' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install -r tests/requirements.txt' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_APT_ATTEMPT=1; JAYINT_APT_MAX_ATTEMPTS=3; JAYINT_APT_STATUS=1; while [ "$JAYINT_APT_ATTEMPT" -le "$JAYINT_APT_MAX_ATTEMPTS" ]; do rm -rf /var/lib/apt/lists/*; DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update && apt-get install -y libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 libexpat1 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64' && JAYINT_APT_STATUS=0 && break; JAYINT_APT_STATUS=$?; (apt-get clean >/dev/null 2>&1 || true); rm -rf /var/lib/apt/lists/*; if [ "$JAYINT_APT_ATTEMPT" -eq "$JAYINT_APT_MAX_ATTEMPTS" ]; then exit "$JAYINT_APT_STATUS"; fi; JAYINT_APT_ATTEMPT=$((JAYINT_APT_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_APT_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install "scrapling[ai]" requests' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install autoscraper' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install mechanicalsoup' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip install pyquery selectolax parsel' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"

# Post-setup compatibility helpers inferred from verified setup
# No post-setup compatibility helpers needed
RUN pip install --no-cache-dir pytest
