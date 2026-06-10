FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/nginx-proxy/nginx-proxy.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /nginx-proxy/. /repo && rm -rf /nginx-proxy/
RUN rm -rf /nginx-proxy
RUN pip install backoff==2.2.1
RUN pip install docker==7.1.0
RUN pip install packaging==26.2
RUN pip install pytest==9.0.3
RUN pip install pytest-ignore-flaky==2.2.1
RUN pip install requests==2.34.2
RUN pip install urllib3==2.7.0
RUN apt-get update -qq
RUN apt-get install -y -qq docker.io
RUN dockerd &>/dev/null & sleep 2
RUN docker info
RUN cd /repo && docker build --pull --build-arg NGINX_PROXY_VERSION="test" -f Dockerfile.debian -t nginxproxy/nginx-proxy:test .
RUN cd /repo && docker build --pull -t web test/requirements/web
RUN cd /repo && docker images