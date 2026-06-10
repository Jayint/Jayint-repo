FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/copier-org/copier.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /copier/. /repo && rm -rf /copier/
RUN rm -rf /copier
RUN cd /repo && pip install -e .
RUN cd /repo && pip install -q pytest==9.0.3 pytest-cov==7.1.0 pytest-gitconfig==0.9.0 pytest-xdist==3.8.0 pexpect==4.9.0
RUN cd /repo && pip install -q inline-snapshot==0.34.0 poethepoet==0.46.0