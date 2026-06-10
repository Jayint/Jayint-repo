FROM python:3.13
WORKDIR /testbed

# Configure apt reliability for eval image builds
RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

# Install git for cloning
RUN command -v git >/dev/null 2>&1 || (apt-get update && apt-get install -y git)

# Dependency-manager helpers inferred from verified setup
# No extra dependency bootstrap needed

# Clone repository and checkout base commit
RUN git clone https://github.com/NewFuture/DDNS /testbed
# No base commit provided; using repository default branch HEAD

# Agent's verified setup instructions
RUN printf '%s' 'Y2F0ID4gZGRucy9fX2luaXRfXy5weSA8PCAnRU9GJwojIC0qLSBjb2Rpbmc6dXRmLTggLSotCiIiIgpkZG5zIFBhY2thZ2UKIiIiCgpfX2Rlc2NyaXB0aW9uX18gPSAiYXV0b21hdGljYWxseSB1cGRhdGUgRE5TIHJlY29yZHMgdG8gbXkgSVAgW+Wfn+WQjeiHquWKqOaMh+WQkeacrOacuklQXSIKCiMg57yW6K+R5pe277yM54mI5pys5Lya6KKr5pu/5o2iCl9fdmVyc2lvbl9fID0gIjAuMC4wIgoKIyDml7bpl7TkuZ/kvJrooqvmm7/mjaLmjokKYnVpbGRfZGF0ZSA9ICIyMDI0LTAxLTAxIgpFT0Y=' | base64 -d > /tmp/jayint_run_1.sh && chmod +x /tmp/jayint_run_1.sh && /bin/sh /tmp/jayint_run_1.sh

# Post-setup compatibility helpers inferred from verified setup
# No post-setup compatibility helpers needed
RUN pip install --no-cache-dir pytest
