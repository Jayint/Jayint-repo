FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/rq/rq.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /rq/. /repo && rm -rf /rq/
RUN rm -rf /rq
RUN ls /repo/requirements.txt 2>/dev/null || echo "No requirements.txt found"
RUN pip install click==8.4.1
RUN pip install croniter==6.2.2
RUN pip install redis==8.0.0
RUN pip install psutil==7.2.2
RUN pip install psutil==7.2.2
RUN cd /repo && apt-get update -qq
RUN cd /repo && apt-get install -y -qq redis-server
RUN cd /repo && redis-server --daemonize yes
RUN cd /repo && sleep 1