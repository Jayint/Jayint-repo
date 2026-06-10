FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/sooperset/mcp-atlassian.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /mcp-atlassian/. /repo && rm -rf /mcp-atlassian/
RUN rm -rf /mcp-atlassian
RUN cd /repo && pip install -e . -q 2>&1 | tail -5