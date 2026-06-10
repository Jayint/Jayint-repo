FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/pre-commit/pre-commit.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /pre-commit/. /repo && rm -rf /pre-commit/
RUN rm -rf /pre-commit
RUN cd /repo && pip install -e . 2>&1 | tail -20
RUN pip install covdefaults==2.3.0
RUN pip install coverage==7.14.1
RUN pip install distlib==0.4.1
RUN pip install pytest==9.0.3
RUN pip install pytest-env==1.6.0
RUN pip install re-assert==1.1.0