FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/stlehmann/pyads.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /pyads/. /repo && rm -rf /pyads/
RUN rm -rf /pyads
RUN cat /repo/.github/workflows/*.yml 2>/dev/null || echo "No workflow files"
RUN cd /repo && git submodule init
RUN cd /repo && git submodule update
RUN cd /repo && apt-get update -qq
RUN cd /repo && apt-get install -y -qq meson ninja-build
RUN cd /repo && pip install -e .[tests,dev] -q