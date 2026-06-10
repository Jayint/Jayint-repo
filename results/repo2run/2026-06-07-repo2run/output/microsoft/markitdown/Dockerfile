FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/microsoft/markitdown.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /markitdown/. /repo && rm -rf /markitdown/
RUN rm -rf /markitdown
RUN cd /repo && pip install -q -e packages/markitdown[all]
RUN cat /repo/packages/markitdown-ocr/pyproject.toml 2>/dev/null || cat /repo/packages/markitdown-ocr/setup.py 2>/dev/null || echo "No config found"
RUN cat /repo/packages/markitdown-sample-plugin/pyproject.toml 2>/dev/null || cat /repo/packages/markitdown-sample-plugin/setup.py 2>/dev/null || echo "No config found"
RUN cd /repo && pip install -q -e packages/markitdown-ocr
RUN cd /repo && pip install -q -e packages/markitdown-mcp
RUN cd /repo && pip install -q -e packages/markitdown-sample-plugin
RUN cd /repo && python -c "from markitdown_ocr import __version__; print('markitdown-ocr imported')" 2>&1 || pip install -q PyMuPDF 2>&1
RUN cd /repo && rm /repo/packages/markitdown-ocr/tests/__init__.py /repo/packages/markitdown-sample-plugin/tests/__init__.py