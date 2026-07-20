#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 49 reciped (2 toolchain, 47 pip) + 0 needs
#   graph-hash: sha256:1384a36d351e   python: 3.11   platform: aarch64-manylinux_2_28   exclude-newer: 2024-09-19
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:flasgger==0.9.7.1  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:flasgger==0.9.7.1  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:attrs==24.2.0  version=24.2.0  requires=-  unblocks=pkg:jsonschema==4.23.0,pkg:referencing==0.35.1
#@check python -m pip show attrs
if python3 -m pip install --break-system-packages --no-deps attrs==24.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attrs==24.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:blinker==1.8.2  version=1.8.2  requires=-  unblocks=pkg:flask==3.0.3
#@check python -m pip show blinker
if python3 -m pip install --break-system-packages --no-deps blinker==1.8.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:blinker==1.8.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2024.8.30  version=2024.8.30  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2024.8.30
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2024.8.30" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.3.2  version=3.3.2  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.3.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.3.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.1.7  version=8.1.7  requires=-  unblocks=pkg:flask==3.0.3
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.1.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.1.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.6.1  version=7.6.1  requires=-  unblocks=pkg:pytest-cov==5.0.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:decorator==5.1.1  version=5.1.1  requires=-  unblocks=pkg:httpbin==0.10.2
#@check python -m pip show decorator
if python3 -m pip install --break-system-packages --no-deps decorator==5.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:decorator==5.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:execnet==2.1.1  version=2.1.1  requires=-  unblocks=pkg:pytest-xdist==3.6.1
#@check python -m pip show execnet
if python3 -m pip install --break-system-packages --no-deps execnet==2.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:execnet==2.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:greenlet==2.0.2  version=2.0.2  requires=-  unblocks=pkg:httpbin==0.10.2
#@check python -m pip show greenlet
if python3 -m pip install --break-system-packages --no-deps greenlet==2.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:greenlet==2.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.10  version=3.10  requires=-  unblocks=pkg:requests==2.32.3,pkg:trustme==1.1.0
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.10" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.0.0  version=2.0.0  requires=-  unblocks=pkg:pytest==8.3.3
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:itsdangerous==2.2.0  version=2.2.0  requires=-  unblocks=pkg:flask==3.0.3
#@check python -m pip show itsdangerous
if python3 -m pip install --break-system-packages --no-deps itsdangerous==2.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:itsdangerous==2.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markupsafe==2.1.5  version=2.1.5  requires=-  unblocks=pkg:jinja2==3.1.4,pkg:werkzeug==3.0.4
#@check python -m pip show markupsafe
if python3 -m pip install --break-system-packages --no-deps markupsafe==2.1.5
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markupsafe==2.1.5" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jinja2==3.1.4  version=3.1.4  requires=pkg:markupsafe==2.1.5  unblocks=pkg:flask==3.0.3
#@check python -m pip show jinja2
if python3 -m pip install --break-system-packages --no-deps jinja2==3.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jinja2==3.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mistune==3.0.2  version=3.0.2  requires=-  unblocks=pkg:flasgger==0.9.7.1
#@check python -m pip show mistune
if python3 -m pip install --break-system-packages --no-deps mistune==3.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mistune==3.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.9.1  version=1.9.1  requires=-  unblocks=pkg:pyright==1.1.381
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.9.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.9.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==24.1  version=24.1  requires=-  unblocks=pkg:flasgger==0.9.7.1,pkg:pytest==8.3.3
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==24.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==24.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.5.0  version=1.5.0  requires=-  unblocks=pkg:pytest==8.3.3
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycparser==2.22  version=2.22  requires=-  unblocks=pkg:cffi==1.17.1
#@check python -m pip show pycparser
if python3 -m pip install --break-system-packages --no-deps pycparser==2.22
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycparser==2.22" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cffi==1.17.1  version=1.17.1  requires=pkg:pycparser==2.22  unblocks=pkg:brotlicffi==1.1.0.0,pkg:cryptography==43.0.1
#@check python -m pip show cffi
if python3 -m pip install --break-system-packages --no-deps cffi==1.17.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cffi==1.17.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:brotlicffi==1.1.0.0  version=1.1.0.0  requires=pkg:cffi==1.17.1  unblocks=pkg:httpbin==0.10.2
#@check python -m pip show brotlicffi
if python3 -m pip install --break-system-packages --no-deps brotlicffi==1.1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:brotlicffi==1.1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cryptography==43.0.1  version=43.0.1  requires=pkg:cffi==1.17.1  unblocks=pkg:pyopenssl==24.2.1,pkg:trustme==1.1.0
#@check python -m pip show cryptography
if python3 -m pip install --break-system-packages --no-deps cryptography==43.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cryptography==43.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyopenssl==24.2.1  version=24.2.1  requires=pkg:cryptography==43.0.1
#@check python -m pip show pyopenssl
if python3 -m pip install --break-system-packages --no-deps pyopenssl==24.2.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyopenssl==24.2.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyright==1.1.381  version=1.1.381  requires=pkg:nodeenv==1.9.1
#@check python -m pip show pyright
if python3 -m pip install --break-system-packages --no-deps pyright==1.1.381
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyright==1.1.381" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pysocks==1.7.1  version=1.7.1  requires=-
#@check python -m pip show pysocks
if python3 -m pip install --break-system-packages --no-deps pysocks==1.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pysocks==1.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==8.3.3  version=8.3.3  requires=pkg:iniconfig==2.0.0,pkg:packaging==24.1,pkg:pluggy==1.5.0  unblocks=pkg:pytest-cov==5.0.0,pkg:pytest-mock==3.14.0,pkg:pytest-xdist==3.6.1
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==8.3.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==8.3.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==5.0.0  version=5.0.0  requires=pkg:coverage==7.6.1,pkg:pytest==8.3.3
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==5.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==5.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-mock==3.14.0  version=3.14.0  requires=pkg:pytest==8.3.3
#@check python -m pip show pytest-mock
if python3 -m pip install --break-system-packages --no-deps pytest-mock==3.14.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-mock==3.14.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-xdist==3.6.1  version=3.6.1  requires=pkg:execnet==2.1.1,pkg:pytest==8.3.3
#@check python -m pip show pytest-xdist
if python3 -m pip install --break-system-packages --no-deps pytest-xdist==3.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-xdist==3.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml==6.0.2  version=6.0.2  requires=-  unblocks=pkg:flasgger==0.9.7.1
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rpds-py==0.20.0  version=0.20.0  requires=-  unblocks=pkg:jsonschema==4.23.0,pkg:referencing==0.35.1
#@check python -m pip show rpds-py
if python3 -m pip install --break-system-packages --no-deps rpds-py==0.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rpds-py==0.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:referencing==0.35.1  version=0.35.1  requires=pkg:attrs==24.2.0,pkg:rpds-py==0.20.0  unblocks=pkg:jsonschema-specifications==2023.12.1,pkg:jsonschema==4.23.0
#@check python -m pip show referencing
if python3 -m pip install --break-system-packages --no-deps referencing==0.35.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:referencing==0.35.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jsonschema-specifications==2023.12.1  version=2023.12.1  requires=pkg:referencing==0.35.1  unblocks=pkg:jsonschema==4.23.0
#@check python -m pip show jsonschema-specifications
if python3 -m pip install --break-system-packages --no-deps jsonschema-specifications==2023.12.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jsonschema-specifications==2023.12.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jsonschema==4.23.0  version=4.23.0  requires=pkg:attrs==24.2.0,pkg:jsonschema-specifications==2023.12.1,pkg:referencing==0.35.1,pkg:rpds-py==0.20.0  unblocks=pkg:flasgger==0.9.7.1
#@check python -m pip show jsonschema
if python3 -m pip install --break-system-packages --no-deps jsonschema==4.23.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jsonschema==4.23.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==75.1.0  version=75.1.0  requires=-
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==75.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==75.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:six==1.16.0  version=1.16.0  requires=-  unblocks=pkg:flasgger==0.9.7.1,pkg:httpbin==0.10.2
#@check python -m pip show six
if python3 -m pip install --break-system-packages --no-deps six==1.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:six==1.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.0.1  version=2.0.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:trustme==1.1.0  version=1.1.0  requires=pkg:cryptography==43.0.1,pkg:idna==3.10
#@check python -m pip show trustme
if python3 -m pip install --break-system-packages --no-deps trustme==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:trustme==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.12.2  version=4.12.2  requires=-
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.12.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.12.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.2.3  version=2.2.3  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.2.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.2.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.32.3  version=2.32.3  requires=pkg:certifi==2024.8.30,pkg:charset-normalizer==3.3.2,pkg:idna==3.10,pkg:urllib3==2.2.3
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.32.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.32.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:werkzeug==3.0.4  version=3.0.4  requires=pkg:markupsafe==2.1.5  unblocks=pkg:flask==3.0.3,pkg:httpbin==0.10.2
#@check python -m pip show werkzeug
if python3 -m pip install --break-system-packages --no-deps werkzeug==3.0.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:werkzeug==3.0.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:flask==3.0.3  version=3.0.3  requires=pkg:blinker==1.8.2,pkg:click==8.1.7,pkg:itsdangerous==2.2.0,pkg:jinja2==3.1.4,pkg:werkzeug==3.0.4  unblocks=pkg:flasgger==0.9.7.1,pkg:httpbin==0.10.2
#@check python -m pip show flask
if python3 -m pip install --break-system-packages --no-deps flask==3.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:flask==3.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:flasgger==0.9.7.1  version=0.9.7.1  requires=binary:pkg-config,pkg:flask==3.0.3,pkg:jsonschema==4.23.0,pkg:mistune==3.0.2,pkg:packaging==24.1,pkg:pyyaml==6.0.2,pkg:six==1.16.0,tool:build-essential  unblocks=pkg:httpbin==0.10.2  build-from-source
#@check python -m pip show flasgger
if python3 -m pip install --break-system-packages --no-deps flasgger==0.9.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:flasgger==0.9.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:httpbin==0.10.2  version=0.10.2  requires=pkg:brotlicffi==1.1.0.0,pkg:decorator==5.1.1,pkg:flasgger==0.9.7.1,pkg:flask==3.0.3,pkg:greenlet==2.0.2,pkg:six==1.16.0,pkg:werkzeug==3.0.4  unblocks=pkg:pytest-httpbin==2.1.0
#@check python -m pip show httpbin
if python3 -m pip install --break-system-packages --no-deps httpbin==0.10.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:httpbin==0.10.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-httpbin==2.1.0  version=2.1.0  requires=pkg:httpbin==0.10.2
#@check python -m pip show pytest-httpbin
if python3 -m pip install --break-system-packages --no-deps pytest-httpbin==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-httpbin==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wheel==0.44.0  version=0.44.0  requires=-
#@check python -m pip show wheel
if python3 -m pip install --break-system-packages --no-deps wheel==0.44.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wheel==0.44.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:requests  requires=pkg:certifi==2024.8.30,pkg:charset-normalizer==3.3.2,pkg:httpbin==0.10.2,pkg:idna==3.10,pkg:pyright==1.1.381,pkg:pytest-cov==5.0.0,pkg:pytest-httpbin==2.1.0,pkg:pytest-mock==3.14.0,pkg:pytest-xdist==3.6.1,pkg:pytest==8.3.3,pkg:requests==2.32.3,pkg:trustme==1.1.0,pkg:typing-extensions==4.12.2,pkg:urllib3==2.2.3,pkg:wheel==0.44.0
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:requests" >> /tmp/v3_failed_nodes.log
fi
