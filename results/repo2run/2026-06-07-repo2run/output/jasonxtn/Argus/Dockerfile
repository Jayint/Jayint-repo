FROM python:3.10
WORKDIR /
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/jasonxtn/Argus.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /Argus/. /repo && rm -rf /Argus/
RUN rm -rf /Argus
RUN cd /repo && pip install -e . -q
RUN pip install aiohttp==3.14.0
RUN pip install requests==2.34.2
RUN pip install urllib3==2.7.0
RUN pip install aioquic==1.3.0
RUN pip install cmd2==3.5.1
RUN pip install rich==15.0.0
RUN pip install colorama==0.4.6
RUN pip install tabulate==0.10.0
RUN pip install dnspython==2.8.0
RUN pip install pysnmp==7.1.27
RUN pip install paramiko==3.5.1
RUN pip install beautifulsoup4==4.14.3
RUN pip install lxml==5.4.0
RUN pip install pyOpenSSL==26.2.0
RUN pip install PyJWT==2.13.0
RUN pip install cryptography==48.0.0
RUN pip install Jinja2==3.1.6
RUN pip install python-slugify==8.0.4
RUN pip install packaging==24.2
RUN pip install idna==3.18
RUN pip install mmh3==4.1.0
RUN pip install jarm==0.1.0
RUN pip install ldap3==2.9.1
RUN pip install pytest==9.0.3
RUN pip install pytest-cov==7.1.0
RUN pip install black==26.5.1
RUN pip install flake8==7.3.0
RUN pip install bandit==1.9.4
RUN pip install sphinx==8.1.3
RUN pip install sphinx-rtd-theme==3.1.0