FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/resend/resend-python.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /resend-python/. /repo && rm -rf /resend-python/
RUN rm -rf /resend-python
RUN pip install requests==2.34.2
RUN pip install typing_extensions==4.15.0
RUN pip install httpx==0.28.1