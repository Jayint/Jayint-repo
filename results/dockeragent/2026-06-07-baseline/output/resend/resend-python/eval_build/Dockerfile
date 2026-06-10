FROM python:3.14.5
WORKDIR /testbed

# Configure apt reliability for eval image builds
RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

# Install git for cloning
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)

# Dependency-manager helpers inferred from verified setup
# No extra dependency bootstrap needed

# Clone repository and checkout base commit
RUN git clone https://github.com/resend/resend-python /testbed
# No base commit provided; using repository default branch HEAD

# Agent's verified setup instructions
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
RUN pip install tox
RUN pip install setuptools==68.2.2
RUN pip install wheel

# Post-setup compatibility helpers inferred from verified setup
# No post-setup compatibility helpers needed
RUN pip install --no-cache-dir pytest
