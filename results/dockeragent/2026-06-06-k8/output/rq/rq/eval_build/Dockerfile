FROM python:3.13
WORKDIR /testbed

# Configure apt reliability for eval image builds
RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

# Install git for cloning
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)

# Dependency-manager helpers inferred from verified setup
# No extra dependency bootstrap needed

# Clone repository and checkout base commit
RUN git clone https://github.com/rq/rq /testbed
# No base commit provided; using repository default branch HEAD

# Agent's verified setup instructions
# No additional setup instructions from agent

# Post-setup compatibility helpers inferred from verified setup
# No post-setup compatibility helpers needed
RUN pip install --no-cache-dir pytest
