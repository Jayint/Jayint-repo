FROM python:3.10
WORKDIR /
COPY patch /patch
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/py2many/py2many.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /py2many/. /repo && rm -rf /py2many/
RUN rm -rf /py2many
RUN cd /repo && git apply --reject /patch/patch_1.diff --allow-empty
RUN cd /repo && pip install -e . -q
RUN cd /repo && git apply -R --reject /patch/patch_1.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_2.diff --allow-empty
RUN cd /repo && pip install -e . -q
RUN pip install argparse_dataclass==2.0.0
RUN pip install tree-sitter==0.25.2
RUN pip install tree-sitter-cpp==0.23.4
RUN pip install tree-sitter-rust==0.24.2
RUN cd /repo && git apply -R --reject /patch/patch_2.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_3.diff --allow-empty