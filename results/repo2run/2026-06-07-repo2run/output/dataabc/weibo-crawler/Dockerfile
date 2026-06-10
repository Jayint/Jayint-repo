FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/dataabc/weibo-crawler.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /weibo-crawler/. /repo && rm -rf /weibo-crawler/
RUN rm -rf /weibo-crawler
RUN ls /repo/*.py /repo/*.toml /repo/*.cfg /repo/*.lock 2>/dev/null || true
RUN pip install lxml==6.1.0
RUN pip install pymongo==4.6.3
RUN pip install PyMySQL==1.1.1
RUN pip install Requests==2.33.0
RUN pip install schedule==1.2.1
RUN pip install tqdm==4.66.3
RUN pip install requests==2.33.0
RUN pip install json5==0.14.0
RUN pip install piexif==1.1.3
RUN pip install Flask==3.1.3