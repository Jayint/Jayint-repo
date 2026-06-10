FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/yutto-dev/yutto.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /yutto/. /repo && rm -rf /yutto/
RUN rm -rf /yutto
RUN pip install -q uv_build==0.11.19
RUN cd /repo && pip install -e . --no-build-isolation