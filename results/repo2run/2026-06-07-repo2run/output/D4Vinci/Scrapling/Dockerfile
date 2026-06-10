FROM python:3.10
WORKDIR /
COPY patch /patch
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/D4Vinci/Scrapling.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /Scrapling/. /repo && rm -rf /Scrapling/
RUN rm -rf /Scrapling
RUN cd /repo && pip install -q -e .
RUN cd /repo && pip install -q -e .[all]
RUN pip install pytest_httpbin==2.1.0
RUN cd /repo && pip install -q requests==2.34.2
RUN cd /repo && git apply --reject /patch/patch_1.diff --allow-empty
RUN cd /repo && git apply -R --reject /patch/patch_1.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_2.diff --allow-empty
RUN cd /repo && git apply -R --reject /patch/patch_2.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_3.diff --allow-empty
RUN cd /repo && git apply -R --reject /patch/patch_3.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_4.diff --allow-empty
RUN cd /repo && pip install -q -r tests/requirements.txt 2>&1 | tail -5
RUN pip install pytest==8.4.2
RUN pip install pytest-cov==7.1.0
RUN pip install playwright==1.59.0
RUN pip install werkzeug==2.3.8
RUN pip install pytest-httpbin==2.1.0
RUN pip install pytest-asyncio==1.4.0
RUN pip install httpbin==0.10.0
RUN pip install pytest-xdist==3.8.0