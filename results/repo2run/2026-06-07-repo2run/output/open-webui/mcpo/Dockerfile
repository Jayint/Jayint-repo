FROM python:3.11
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/open-webui/mcpo.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /mcpo/. /repo && rm -rf /mcpo/
RUN rm -rf /mcpo
RUN find /repo -type f -name "*.py" -path "*/test*" 2>/dev/null | head -10
RUN cd /repo && pip install -q -e .