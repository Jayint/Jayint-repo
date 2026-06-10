FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/unit8co/darts.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /darts/. /repo && rm -rf /darts/
RUN rm -rf /darts
RUN cat /repo/INSTALL.md 2>/dev/null
RUN ls /repo/darts/tests/ 2>/dev/null || echo "no tests dir"
RUN ls /repo/*.txt 2>/dev/null || echo "no txt files"
RUN cd /repo && pip install -e . -q
RUN cd /repo && pip install -q testfixtures==8.3.0