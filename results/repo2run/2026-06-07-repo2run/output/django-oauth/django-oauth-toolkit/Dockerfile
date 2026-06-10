FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/django-oauth/django-oauth-toolkit.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /django-oauth-toolkit/. /repo && rm -rf /django-oauth-toolkit/
RUN rm -rf /django-oauth-toolkit
RUN cd /repo && pip install -q -e .[test]
ENV DJANGO_SETTINGS_MODULE=tests.settings
ENV PYTHONPATH=/repo:$PYTHONPATH
RUN cd /repo && DJANGO_SETTINGS_MODULE=tests.settings PYTHONPATH=/repo python -c "from django.conf import settings; print(settings.SECRET_KEY)"
RUN cd /repo && DJANGO_SETTINGS_MODULE=tests.settings PYTHONPATH=/repo python /home/tools/runtest.py