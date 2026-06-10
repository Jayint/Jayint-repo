FROM python:3.10
WORKDIR /
COPY patch /patch
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/GoogleCloudPlatform/slurm-gcp.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /slurm-gcp/. /repo && rm -rf /slurm-gcp/
RUN rm -rf /slurm-gcp
RUN ls /repo/*.py /repo/*.toml /repo/*.cfg /repo/*.txt /repo/Pipfile* /repo/environment* 2>/dev/null; echo "---"; cat /repo/test/requirements.txt
RUN find /repo -type f \( -name "requirements*.txt" -o -name "Pipfile*" -o -name "pyproject.toml" -o -name "setup.py" -o -name "setup.cfg" \) 2>/dev/null
RUN pip install addict==2.4.0
RUN pip install asttokens==2.2.1
RUN pip install backcall==0.2.0
RUN pip install cffi==2.0.0
RUN pip install executing==1.2.0
RUN pip install google-api-python-client==2.93.0
RUN pip install google-auth-httplib2==0.1.0
RUN pip install google-cloud-tpu==1.10.0
RUN pip install ipdb==0.13.13
RUN pip install ipython==8.14.0
RUN pip install more-executors==2.11.4
RUN pip install paramiko==3.4.0
RUN pip install pickleshare==0.7.5
RUN pip install psutil==5.9.5
RUN pip install ptyprocess==0.7.0
RUN pip install pure-eval==0.2.2
RUN pip install py-cpuinfo==9.0.0
RUN pip install pyaml==23.7.0
RUN pip install pycparser==2.21
RUN pip install pytest==7.4.0
RUN pip install pytest-benchmark==4.0.0
RUN pip install python-hostlist==1.23.0
RUN pip install pyyaml==6.0
RUN pip install requests==2.33.0
RUN pip install stack-data==0.6.2
RUN pip install tftest==1.8.4
RUN pip install wcwidth==0.2.6
RUN find /repo -type f -name "util*" 2>/dev/null
ENV PYTHONPATH=/repo/scripts:/repo/test
RUN cd /repo && git apply --reject /patch/patch_1.diff --allow-empty
RUN cd /repo && git apply -R --reject /patch/patch_1.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_2.diff --allow-empty
RUN cd /repo && git apply -R --reject /patch/patch_2.diff --allow-empty
RUN cd /repo && git apply --reject /patch/patch_3.diff --allow-empty
RUN cd /repo && find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
RUN cd /repo && pip install google-cloud-storage==3.11.0 google-cloud-secret-manager==2.29.0 -q
RUN cd /repo && find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
RUN cd /repo && find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
RUN cd /repo/scripts && find /repo -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
RUN cd /repo && pip install mock==5.2.0 -q
RUN cd /repo && find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null