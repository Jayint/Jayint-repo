FROM python:3.12
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/rayai-labs/agentic-ray.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /agentic-ray/. /repo && rm -rf /agentic-ray/
RUN rm -rf /agentic-ray
RUN pip install -q uv==0.11.19
RUN cd /repo/packages/python-sdk && uv sync
RUN cd /repo && uv sync
RUN cd /repo/packages/python-sdk && uv export --dev -o /tmp/requirements.txt
RUN cd /repo/packages/python-sdk && uv export --dev -o /tmp/requirements.txt
RUN cd /repo && pip install -e packages/python-sdk
RUN cd /repo && pip install -q pytest==9.0.3 pytest-asyncio==1.4.0 respx==0.23.1