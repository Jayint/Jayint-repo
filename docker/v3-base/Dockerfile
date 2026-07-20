# v3-base:${PY_MINOR} — harness INSTRUMENT baked in (spec §4.4).
#
# The instrument is what every run needs identically (python, pip, the pytest
# runner + pytest-timeout, a bare-`python` symlink, git for cloning) — as opposed
# to the ENVIRONMENT (the repo's declared deps), which the graph installs per repo.
# Baking the instrument lets the rendered setup.sh become fully graph-derived and
# collapses the pytest-install tangle. Floor-not-pin: a repo declaring pytest==X
# still installs X over the baked one in its own PIP section.
#
# Built on the VM (or locally for the Tier-0 recipe smoke); no registry/push.
ARG PY_MINOR=3.11
FROM python:${PY_MINOR}-slim
RUN python3 -m pip install --no-cache-dir --upgrade pip \
 && python3 -m pip install --no-cache-dir pytest pytest-timeout \
 && ln -sf "$(command -v python3)" /usr/local/bin/python \
 && apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m pip freeze | grep -Ei '^(pytest|pytest-timeout)==' > /opt/v3-instrument.txt
LABEL org.v3.instrument="pytest,pytest-timeout,git,pip"
