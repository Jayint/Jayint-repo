FROM python:3.10
WORKDIR /
COPY patch /patch
RUN apt-get update && apt-get install -y curl
RUN curl -sSL https://install.python-poetry.org | python -
ENV PATH="/root/.local/bin:$PATH"
RUN pip install pytest pytest-xdist
RUN pip install pipdeptree
RUN git clone https://github.com/supabase/supabase-py.git
RUN mkdir /repo
RUN git config --global --add safe.directory /repo
RUN cp -r /supabase-py/. /repo && rm -rf /supabase-py/
RUN rm -rf /supabase-py
RUN find /repo -name "requirements*.txt" -o -name "setup.py" -o -name "setup.cfg" -o -name "Pipfile" -o -name "poetry.lock" 2>/dev/null
RUN uv --version 2>&1 || echo "uv not installed"
RUN pip install -q uv==0.11.19
RUN cd /repo && uv sync
RUN cd /repo && pip install -q uv==0.11.19
RUN cd /repo && uv sync
RUN pip install -q PyJWT==2.13.0 faker==40.21.0 httpx==0.28.1 aiohttp==3.14.0 python-dotenv==1.2.2
RUN pip install -q PyJWT==2.13.0 faker==40.21.0 httpx==0.28.1 aiohttp==3.14.0 python-dotenv==1.2.2 pydantic==2.13.4 respx==0.23.1
RUN pip install -q -e /repo/src/auth -e /repo/src/functions -e /repo/src/postgrest -e /repo/src/realtime -e /repo/src/storage -e /repo/src/supabase
RUN head -5 /repo/src/functions/tests/_async/__init__.py 2>/dev/null; echo "---"; head -5 /repo/src/storage/tests/_async/__init__.py 2>/dev/null; echo "---"; head -5 /repo/src/storage/tests/__init__.py 2>/dev/null; echo "---"; head -5 /repo/src/postgrest/tests/__init__.py 2>/dev/null; echo "---"; head -5 /repo/src/functions/tests/__init__.py 2>/dev/null
RUN cd /repo && git apply --reject /patch/patch_1.diff --allow-empty
RUN pip install -q PyJWT==2.13.0 faker==40.21.0 httpx==0.28.1 aiohttp==3.14.0 python-dotenv==1.2.2 pydantic==2.13.4 respx==0.23.1
RUN ls -la /repo/src/functions/tests/__init__.py 2>&1
RUN ls -la /repo/src/postgrest/tests/__init__.py 2>&1
RUN ls -la /repo/src/storage/tests/__init__.py 2>&1
RUN cd /repo && touch src/functions/tests/_async/__init__.py src/functions/tests/_sync/__init__.py src/postgrest/tests/_async/__init__.py src/postgrest/tests/_sync/__init__.py
RUN cd /repo && rm src/supabase/tests/conftest.py
RUN cd /repo && touch src/functions/tests/_async/__init__.py src/functions/tests/_sync/__init__.py src/postgrest/tests/_async/__init__.py src/postgrest/tests/_sync/__init__.py src/storage/tests/_async/__init__.py src/storage/tests/_sync/__init__.py src/supabase/tests/_async/__init__.py src/supabase/tests/_sync/__init__.py
RUN cd /repo && rm -f src/supabase/tests/conftest.py
RUN cd /repo && ls -la src/functions/tests/_async/__init__.py 2>&1
RUN cd /repo && ls -la src/functions/tests/_sync/__init__.py 2>&1
RUN cd /repo && ls -la src/postgrest/tests/_async/__init__.py 2>&1
RUN cd /repo && ls -la src/postgrest/tests/_sync/__init__.py 2>&1
RUN cd /repo && ls -la src/storage/tests/_async/__init__.py 2>&1
RUN cd /repo && ls -la src/storage/tests/_sync/__init__.py 2>&1
RUN cd /repo && ls -la src/supabase/tests/_async/__init__.py 2>&1
RUN cd /repo && ls -la src/supabase/tests/_sync/__init__.py 2>&1
RUN cd /repo && pip install -q -e src/auth -e src/functions -e src/postgrest -e src/realtime -e src/storage -e src/supabase
ENV PYTHONPATH=/repo/src
RUN cd /repo && touch src/functions/tests/_async/__init__.py src/functions/tests/_sync/__init__.py src/postgrest/tests/_async/__init__.py src/postgrest/tests/_sync/__init__.py src/storage/tests/_async/__init__.py src/storage/tests/_sync/__init__.py
RUN cd /repo && rm -f src/supabase/tests/conftest.py
RUN cd /repo && touch src/auth/tests/__init__.py src/functions/tests/__init__.py src/postgrest/tests/__init__.py src/storage/tests/__init__.py src/supabase/tests/__init__.py