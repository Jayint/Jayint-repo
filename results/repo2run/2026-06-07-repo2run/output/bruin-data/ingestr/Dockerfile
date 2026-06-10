FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/bruin-data/ingestr.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /ingestr/. /repo && rm -rf /ingestr/
RUN rm -rf /ingestr