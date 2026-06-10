FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/Yelp/dumb-init.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /dumb-init/. /repo && rm -rf /dumb-init/
RUN rm -rf /dumb-init
RUN gcc --version
RUN cat /repo/.pipreqs/*.txt 2>/dev/null || echo "No pipreqs files"
RUN pip install pre-commit==4.6.0
RUN pip install pytest==9.0.3
RUN pip install pytest-timeout==1.4.2