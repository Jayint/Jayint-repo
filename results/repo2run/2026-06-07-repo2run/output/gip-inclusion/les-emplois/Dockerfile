FROM python:3.13
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/gip-inclusion/les-emplois.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /les-emplois/. /repo && rm -rf /les-emplois/
RUN rm -rf /les-emplois
RUN cd /repo && pip install -q -r requirements/dev.txt
ENV PYTHONPATH=/repo:$PYTHONPATH
RUN apt-get update -qq
RUN apt-get install -y -qq libgdal-dev gdal-bin