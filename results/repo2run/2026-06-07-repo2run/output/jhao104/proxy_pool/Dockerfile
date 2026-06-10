FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/jhao104/proxy_pool.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /proxy_pool/. /repo && rm -rf /proxy_pool/
RUN rm -rf /proxy_pool
RUN pip install requests==2.31.0
RUN pip install gunicorn==19.9.0
RUN pip install lxml==4.9.2
RUN pip install redis==8.0.0
RUN pip install click==8.0.1
RUN pip install Flask==2.1.1
RUN pip install werkzeug==2.1.2
RUN pip install pytest==9.0.3
RUN pip install pytest-cov==7.1.0
RUN pip install apscheduler==3.10.0
RUN pip install fakeredis==2.36.0
RUN pip install async_timeout==5.0.1
RUN pip install typing_extensions==4.15.0