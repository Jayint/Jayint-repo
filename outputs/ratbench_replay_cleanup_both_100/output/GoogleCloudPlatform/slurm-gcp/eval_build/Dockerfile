FROM rust:1.80
RUN (python -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || python3 -m pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry || pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org pytest pytest-xdist poetry)
WORKDIR /app
COPY . /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=5

RUN printf '%s\n' 'Acquire::Retries "5";' 'Acquire::http::Timeout "120";' 'Acquire::https::Timeout "120";' 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99jayint-retries

RUN JAYINT_APT_ATTEMPT=1; JAYINT_APT_MAX_ATTEMPTS=3; JAYINT_APT_STATUS=1; while [ "$JAYINT_APT_ATTEMPT" -le "$JAYINT_APT_MAX_ATTEMPTS" ]; do rm -rf /var/lib/apt/lists/*; DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update -qq && apt-get install -y -qq python3-pip python3-pytest python3-pytest-mock git' && JAYINT_APT_STATUS=0 && break; JAYINT_APT_STATUS=$?; (apt-get clean >/dev/null 2>&1 || true); rm -rf /var/lib/apt/lists/*; if [ "$JAYINT_APT_ATTEMPT" -eq "$JAYINT_APT_MAX_ATTEMPTS" ]; then exit "$JAYINT_APT_STATUS"; fi; JAYINT_APT_ATTEMPT=$((JAYINT_APT_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_APT_STATUS"


RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip3 install --break-system-packages mock google-api-python-client requests pyyaml addict httplib2 python-hostlist' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN mkdir -p /tmp/fake_gcp
RUN cat > /tmp/fake_gcp/credentials.json << 'EOFCRED'
{
  "type": "service_account",
  "project_id": "fake-project",
  "private_key_id": "fake-key-id",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDalTjfzl5RaSys\nturUNWIRZfyU43lGvTCH1Bxx036kjDIFQ6qxqCvwcYXU3942QkBMSp130ah7K9my\nqWlkWH679BUzpj9AhOM5h+rj5mzTf0IzBWRyWcqhnoFg7a3SYlxF+3DYhGe+gxQE\nnyGoaHq1hXfanTvtRFw7OjzExatJ3IcYm4QMfYbniulagt7Z21bgw7SqgxU/8iZL\nHPVb02QZHEEREt9Eywx2qFDTCZb8MFwA8zWs9GyB0mG2zdYwHTnvhYv4BWqdHOHY\nX4MnjMWI+mF03vd7QWs46VIXL4wqDgfMl+ZDDWRY/mnbLpeddxyoULHDvdtTiO/i\nkZsJs9jNAgMBAAECggEAGWNTo8sYcWRB6B2m0NcSeOrbl3+ESo8W3+/nDeF+yCXw\neWQOo2vhDqBg5wTJjovLZBEWkSAs7YF+W54Ju7n7nmEPRVx95ESLD9eVlLri0P3q\n9nod3JpJStRaOqPgmJsMFKTLypo2Yo5e+Npy0liwR/0kmqPAniJehBKTlFdROdbI\nWOAbtA/dJuiZN7CZjzRw/CStAkeXChukKFF7ttqk+sK+3k2EHKIXYJh9Txg/Jzy5\n0NlC01MuooJCVv+ieFEIB1qtG4dXmxnv+kMgzUaStt8N6jLIyFUpRIUZOXD9pPJ+\n5CTb5TawTLCAecKSUSFf0euCJRrAD98je5yT+9t0gQKBgQD8eIrAwuKholReFoUY\nnOrZ7ZoozZ5Yx9qCUyaIzNQTiqHM1IegoYR9NcVxgca9nMqWkCqDz0wfVu+WUpZb\nps1iXzSJKHYzb28Z6R1Wx7PaJiuMenTfb01fWKcDw/nDcW5ncLhilybNz3mtVCCI\nGjhzjbaU8N7u5mIJRIJ+tKymFQKBgQDdo2nMbfDzTVw05CYW9ZL8uni+E1sSxtVg\n158d2batVMbPI8dLv7iAXYteDa1zjhTi+6TsGGCItHlu7k6RRcwhigmQHy7KZofg\nRt0tTXBCglyYIYmbX0sHpjMLyNgvkTQDJUO71T9Swf3nXWJnot5PIXw++Ok+IQm7\nEtL96jwN2QKBgAzHORaahofxxF1ivGfLgWN4UCYiihIqQ1f3R2sDGvTGPnw0gYzS\nv/bqYszk6Z8tMV+/ejdjpZGsf8HW+j83SQvij7EleB4QVMo170M2JlczOV8Jz6KD\naqIZxZI+OAT5V8IqhVqQFemyx5DLQj5BPXwzvvr42f+zb2DSs9wf3uElAoGBAJQh\nOH+p5wUlaf/rj81+eauxkSswUokdHNVppu3TaXU1GN6uQe/Nhik82/2TVJ7LkggW\nOaaVsEcNn69VVGthY8nysOuC+8nIdv2dPwudGu9Io0wF9itCqCnHmYxBwsMf38xO\nZpOuNXFBOEoerrsnLj9i2OBZicG9YPyH2z5bA6IZAoGBANk2dF4x1eG2+/aeoIR/\nqZlEjmD/T8tL9VqVJJq8UkX8EHpg3X1zN2B26vf3+6BppbPSyRLmkLeEtJf+1hGy\nBId/YTh9uNpb3v94AcgRH2RqkwifkCqPfKBhDohLL9kUnlByGGtleMoWJJH5MFRx\nJ4eytKhYXIM/5mUijqurbAMo\n-----END PRIVATE KEY-----\n",
  "client_email": "fake@fake-project.iam.gserviceaccount.com",
  "client_id": "123456789",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token"
}
EOFCRED
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip3 install --break-system-packages paramiko tftest' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
RUN JAYINT_PIP_ATTEMPT=1; JAYINT_PIP_MAX_ATTEMPTS=3; JAYINT_PIP_STATUS=1; while [ "$JAYINT_PIP_ATTEMPT" -le "$JAYINT_PIP_MAX_ATTEMPTS" ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc 'pip3 install --break-system-packages psutil' && JAYINT_PIP_STATUS=0 && break; JAYINT_PIP_STATUS=$?; (python -m pip cache purge >/dev/null 2>&1 || python3 -m pip cache purge >/dev/null 2>&1 || pip cache purge >/dev/null 2>&1 || true); if [ "$JAYINT_PIP_ATTEMPT" -eq "$JAYINT_PIP_MAX_ATTEMPTS" ]; then exit "$JAYINT_PIP_STATUS"; fi; JAYINT_PIP_ATTEMPT=$((JAYINT_PIP_ATTEMPT + 1)); sleep 5; done; exit "$JAYINT_PIP_STATUS"
