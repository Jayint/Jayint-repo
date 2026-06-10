FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/yihong0618/bilingual_book_maker.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /bilingual_book_maker/. /repo && rm -rf /bilingual_book_maker/
RUN rm -rf /bilingual_book_maker
RUN cd /repo && pip install -e .