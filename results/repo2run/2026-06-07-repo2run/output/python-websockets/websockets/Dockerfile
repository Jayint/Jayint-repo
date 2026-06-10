FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/python-websockets/websockets.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /websockets/. /repo && rm -rf /websockets/
RUN rm -rf /websockets
RUN cd /repo && pip install -e .
RUN ls /repo/*.txt /repo/*.ini /repo/*.cfg 2>/dev/null; cat /repo/tox.ini
RUN pip install -q trio==0.33.0 werkzeug==3.1.8 python-socks[asyncio]