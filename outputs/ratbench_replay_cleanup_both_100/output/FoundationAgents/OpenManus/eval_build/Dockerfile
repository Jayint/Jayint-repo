FROM python:3.12
RUN (python -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || python3 -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry)
WORKDIR /app
COPY . /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=5

RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

RUN JAYINT_APT_ATTEMPT=1; JAYINT_APT_MAX_ATTEMPTS=3; JAYINT_APT_STATUS=1; while [ "$JAYINT_APT_ATTEMPT" -le "$JAYINT_APT_MAX_ATTEMPTS" ]; do rm -rf /var/lib/apt/lists/*; DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update && apt-get install -y libjpeg-dev zlib1g-dev' && JAYINT_APT_STATUS=0 && break; JAYINT_APT_STATUS=$?; (apt-get clean >/dev/null 2>&1 || true); rm -rf /var/lib/apt/lists/*; if [ "$JAYINT_APT_ATTEMPT" -eq "$JAYINT_APT_MAX_ATTEMPTS" ]; then exit "$JAYINT_APT_STATUS"; fi; JAYINT_APT_ATTEMPT=$((JAYINT_APT_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_APT_STATUS"

RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'python -m pip install --upgrade pip' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"


RUN PIP_NO_CACHE_DIR=1 python -m pip install pydantic~=2.10.6 openai~=1.66.3 tenacity~=9.0.0 pyyaml~=6.0.2 loguru~=0.7.3 numpy datasets~=3.4.1 fastapi~=0.115.11 tiktoken~=0.9.0 html2text~=2024.2.26 gymnasium~=1.1.1 uvicorn~=0.34.0 unidiff~=0.7.5 aiofiles~=24.1.0 pydantic_core~=2.27.2 colorama~=0.4.6 docker~=7.1.0 pytest~=8.3.5 pytest-asyncio~=0.25.3 mcp~=1.5.0 httpx>=0.27.0 tomli>=2.0.0 boto3~=1.37.18 requests~=2.32.3 beautifulsoup4~=4.13.3 huggingface-hub~=0.29.2 setuptools~=75.8.0

RUN PIP_NO_CACHE_DIR=1 python -m pip install crawl4ai~=0.6.3 pillow~=10.4.0

RUN PIP_NO_CACHE_DIR=1 python -m pip install browser-use~=0.1.40 googlesearch-python~=1.3.0 baidusearch~=1.0.3 duckduckgo_search~=7.5.3 browsergym~=0.13.3 --no-deps pillow

RUN PIP_NO_CACHE_DIR=1 python -m pip install "openai>=1.58.1,<1.67.0"

RUN PIP_NO_CACHE_DIR=1 python -m pip install -e /app/

RUN printf '%s\n' '# Global LLM configuration' '[llm]' 'model = "claude-3-7-sonnet-20250219"' 'base_url = "https://api.anthropic.com/v1/"' 'api_key = "YOUR_API_KEY"' 'max_tokens = 8192' 'temperature = 0.0' 'api_type = "anthropic"' 'api_version = "2023-06-01"' '' '[llm.vision]' 'model = "claude-3-7-sonnet-20250219"' 'base_url = "https://api.anthropic.com/v1/"' 'api_key = "YOUR_API_KEY"' 'max_tokens = 8192' 'temperature = 0.0' '' '[daytona]' 'daytona_api_key = ""' 'daytona_server_url = "https://app.daytona.io/api"' 'daytona_target = "us"' 'sandbox_image_name = "whitezxj/sandbox:0.1.0"' 'sandbox_entrypoint = "/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf"' 'VNC_password = "123456"' '' '[mcp]' 'server_reference = "app.mcp.server"' '' '[runflow]' 'use_data_analysis_agent = false' > /app/config/config.toml
