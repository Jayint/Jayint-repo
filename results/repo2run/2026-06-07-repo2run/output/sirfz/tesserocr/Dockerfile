FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/sirfz/tesserocr.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /tesserocr/. /repo && rm -rf /tesserocr/
RUN rm -rf /tesserocr
RUN tesseract --version 2>&1 || echo "tesseract not found"
RUN apt-get update -qq
RUN apt-get install -y -qq tesseract-ocr libtesseract-dev libleptonica-dev pkg-config
RUN pip install cysignals==1.12.5
RUN pip install pillow==12.2.0
RUN pip install setuptools==79.0.1
RUN pip install wheel==0.45.1
RUN cd /repo && pip install -e . -q