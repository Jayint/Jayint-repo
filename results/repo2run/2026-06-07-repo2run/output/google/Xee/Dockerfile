FROM python:3.11
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/google/Xee.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /Xee/. /repo && rm -rf /Xee/
RUN rm -rf /Xee
RUN find /repo -maxdepth 2 -name "requirements*.txt" -o -name "Pipfile" -o -name "setup.py" 2>/dev/null
RUN cd /repo && pip install -q -e .
RUN cd /repo && pip install -q -e .[tests]