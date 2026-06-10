FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/EnableSecurity/wafw00f.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /wafw00f/. /repo && rm -rf /wafw00f/
RUN rm -rf /wafw00f
RUN cd /repo && pip install -q -e .[dev]