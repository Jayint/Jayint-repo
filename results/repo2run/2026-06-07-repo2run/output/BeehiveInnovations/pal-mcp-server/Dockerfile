FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/BeehiveInnovations/pal-mcp-server.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /pal-mcp-server/. /repo && rm -rf /pal-mcp-server/
RUN rm -rf /pal-mcp-server
RUN cd /repo && pip install -e . -q
RUN pip install pytest==9.0.3
RUN pip install pytest-asyncio==1.4.0
RUN pip install pytest-mock==3.15.1
RUN pip install black==26.5.1
RUN pip install ruff==0.15.16
RUN pip install isort==8.0.1
RUN pip install python-semantic-release==10.5.3
RUN pip install build==1.5.0