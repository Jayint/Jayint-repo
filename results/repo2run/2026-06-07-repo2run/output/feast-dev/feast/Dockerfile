FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/feast-dev/feast.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /feast/. /repo && rm -rf /feast/
RUN rm -rf /feast
RUN find /repo -maxdepth 3 -name "requirements*.txt" -o -name "setup.py" -o -name "setup.cfg" -o -name "Pipfile" -o -name "Pipfile.lock" -o -name "environment.yml" -o -name "poetry.lock" 2>/dev/null
RUN cd /repo && pip install -e .[test] -q
RUN cd /repo && pip install -e .[test] -q 2>&1 | tail -5
RUN cd /repo && pip install sentence-transformers==5.5.1
RUN cd /repo && pip install -e .[grpcio] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[grpcio,gcp] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[grpcio,gcp] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[aws] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[ray] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[spark] -q 2>&1 | tail -5
RUN cd /repo && pip install assertpy==1.1 -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[k8s] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[ci] -q 2>&1 | tail -5
RUN cd /repo && pip install -e .[snowflake] -q 2>&1 | tail -5
RUN cd /repo && pip install snowflake-connector-python>=3.7,<5 -q 2>&1 | tail -10
RUN cd /repo && pip list | grep -i snowflake
RUN cd /repo && python -c "import snowflake.connector; print('OK')" 2>&1 || echo "Snowflake import failed"
RUN cd /repo && pip install snowflake-connector-python>=3.7,<5 -q 2>&1 | tail -5
RUN cd /repo && pip install snowflake-connector-python>=3.7,<5 -q 2>&1 | tail -5
RUN cd /repo && pip list | grep -i snowflake
RUN cd /repo && python -c "import snowflake.connector; print('OK')" 2>&1 || echo "FAILED"
RUN cd /repo && pip install snowflake-connector-python>=3.7,<5 -q 2>&1 | tail -10
RUN cd /repo && pip install snowflake-connector-python>=3.7,<5 -q 2>&1 | tail -5