#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 121 reciped (35 toolchain, 86 pip) + 0 needs
#   graph-hash: sha256:848e15a6d5f6
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:alabaster==1.0.0,pkg:amqp==5.1.1,pkg:anyio==4.14.2,pkg:asgiref==3.11.1,pkg:ast-serialize==0.6.0,pkg:async-timeout==4.0.2,pkg:babel==2.18.0,pkg:billiard==3.6.4.0,pkg:blinker==1.9.0,pkg:cachetools==7.1.4,pkg:celery==5.2.7,pkg:certifi==2026.6.17,pkg:cffi==2.1.0,pkg:cfgv==3.5.0,pkg:charset-normalizer==3.4.9,pkg:click-didyoumean==0.3.0,pkg:click-plugins==1.1.1,pkg:click-repl==0.2.0,pkg:click==8.4.2,pkg:colorama==0.4.6,pkg:cryptography==49.0.0,pkg:distlib==0.4.3,pkg:docutils==0.22.4,pkg:filelock==3.29.7,pkg:flask==2.3.2,pkg:greenlet==3.5.3,pkg:h11==0.16.0,pkg:identify==2.6.19,pkg:idna==3.18,pkg:imagesize==2.0.0,pkg:iniconfig==2.3.0,pkg:itsdangerous==2.2.0,pkg:jinja2==3.1.6,pkg:kombu==5.2.4,pkg:librt==0.13.0,pkg:markupsafe==3.0.3,pkg:mypy-extensions==1.1.0,pkg:mypy==2.3.0,pkg:nodeenv==1.10.0,pkg:packaging==26.2,pkg:pathspec==1.1.1,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pre-commit-uv==4.2.2,pkg:pre-commit==4.6.0,pkg:prompt-toolkit==3.0.38,pkg:pycparser==3.0,pkg:pygments==2.20.0,pkg:pyproject-api==1.10.1,pkg:pyright==1.1.411,pkg:pytest==9.1.1,pkg:python-discovery==1.4.4,pkg:python-dotenv==1.2.2,pkg:pytz==2023.3,pkg:pyyaml==6.0.3,pkg:redis==4.5.4,pkg:requests==2.34.2,pkg:roman-numerals==4.1.0,pkg:ruff==0.15.21,pkg:six==1.16.0,pkg:snowballstemmer==3.1.1,pkg:sphinx-autobuild==2025.8.25,pkg:sphinx==9.0.4,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,pkg:starlette==1.3.1,pkg:tomli-w==1.2.0,pkg:tox-uv-bare==1.35.2,pkg:tox-uv==1.35.2,pkg:tox==4.56.4,pkg:types-contextvars==2.4.7.3,pkg:types-dataclasses==0.6.6,pkg:typing-extensions==4.16.0,pkg:urllib3==2.7.0,pkg:uv==0.11.28,pkg:uvicorn==0.51.0,pkg:vine==5.0.0,pkg:virtualenv==21.6.1,pkg:watchfiles==1.2.0,pkg:wcwidth==0.2.6,pkg:websockets==16.1,pkg:werkzeug==3.1.8  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:cargo  provider=apt:cargo  requires=-  unblocks=pkg:cryptography==49.0.0,pkg:watchfiles==1.2.0  toolchain
#@check dpkg-query -W -f='${Status}' cargo 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends cargo
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:cargo" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:dbus-test-runner  provider=apt:dbus-test-runner  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' dbus-test-runner 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends dbus-test-runner
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:dbus-test-runner" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:dvipng  provider=apt:dvipng  requires=-  unblocks=pkg:celery==5.2.7  toolchain
#@check dpkg-query -W -f='${Status}' dvipng 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends dvipng
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:dvipng" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:flake8  provider=apt:flake8  requires=-  unblocks=pkg:mypy-extensions==1.1.0,pkg:mypy==2.3.0  toolchain
#@check dpkg-query -W -f='${Status}' flake8 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends flake8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:flake8" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:flit  provider=apt:flit  requires=-  unblocks=pkg:blinker==1.9.0,pkg:flask==2.3.2,pkg:idna==3.18,pkg:jinja2==3.1.6,pkg:packaging==26.2,pkg:pathspec==1.1.1,pkg:sphinx-autobuild==2025.8.25,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,pkg:tomli-w==1.2.0,pkg:typing-extensions==4.16.0,pkg:werkzeug==3.1.8  toolchain
#@check dpkg-query -W -f='${Status}' flit 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends flit
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:flit" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:furo  provider=apt:furo  requires=-  unblocks=pkg:greenlet==3.5.3,pkg:mypy==2.3.0,pkg:pyproject-api==1.10.1,pkg:pytest==9.1.1  toolchain
#@check dpkg-query -W -f='${Status}' furo 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends furo
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:furo" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:gir1.2-glib-2.0  provider=apt:gir1.2-glib-2.0  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' gir1.2-glib-2.0 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends gir1.2-glib-2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:gir1.2-glib-2.0" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:gobject-introspection  provider=apt:gobject-introspection  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' gobject-introspection 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends gobject-introspection
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:gobject-introspection" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:help2man  provider=apt:help2man  requires=-  unblocks=pkg:identify==2.6.19,pkg:mypy-extensions==1.1.0,pkg:mypy==2.3.0,pkg:python-dotenv==1.2.2,pkg:uvicorn==0.51.0  toolchain
#@check dpkg-query -W -f='${Status}' help2man 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends help2man
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:help2man" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libffi-dev  provider=apt:libffi-dev  requires=-  unblocks=pkg:cffi==2.1.0  toolchain
#@check dpkg-query -W -f='${Status}' libffi-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libffi-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libffi-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libgee-0.8-dev  provider=apt:libgee-0.8-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libgee-0.8-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libgee-0.8-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libgee-0.8-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libgirepository1.0-dev  provider=apt:libgirepository1.0-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libgirepository1.0-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libgirepository1.0-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libgirepository1.0-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libglib2.0-dev  provider=apt:libglib2.0-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libglib2.0-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libglib2.0-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libglib2.0-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libjson-glib-dev  provider=apt:libjson-glib-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libjson-glib-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libjson-glib-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libjson-glib-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libproperties-cpp-dev  provider=apt:libproperties-cpp-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libproperties-cpp-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libproperties-cpp-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libproperties-cpp-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libssl-dev  provider=apt:libssl-dev  requires=-  unblocks=pkg:cryptography==49.0.0  toolchain
#@check dpkg-query -W -f='${Status}' libssl-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libssl-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libssl-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libyaml-dev  provider=apt:libyaml-dev  requires=-  unblocks=pkg:pyyaml==6.0.3  toolchain
#@check dpkg-query -W -f='${Status}' libyaml-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libyaml-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libyaml-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:locales  provider=apt:locales  requires=-  unblocks=pkg:celery==5.2.7  toolchain
#@check dpkg-query -W -f='${Status}' locales 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends locales
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:locales" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:lsof  provider=apt:lsof  requires=-  unblocks=pkg:pytest==9.1.1  toolchain
#@check dpkg-query -W -f='${Status}' lsof 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends lsof
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:lsof" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:mkdocs  provider=apt:mkdocs  requires=-  unblocks=pkg:uvicorn==0.51.0  toolchain
#@check dpkg-query -W -f='${Status}' mkdocs 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends mkdocs
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:mkdocs" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:openssl  provider=apt:openssl  requires=-  unblocks=pkg:requests==2.34.2  toolchain
#@check dpkg-query -W -f='${Status}' openssl 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends openssl
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:openssl" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:alabaster==1.0.0,pkg:amqp==5.1.1,pkg:anyio==4.14.2,pkg:asgiref==3.11.1,pkg:ast-serialize==0.6.0,pkg:async-timeout==4.0.2,pkg:babel==2.18.0,pkg:billiard==3.6.4.0,pkg:blinker==1.9.0,pkg:cachetools==7.1.4,pkg:celery==5.2.7,pkg:certifi==2026.6.17,pkg:cffi==2.1.0,pkg:cfgv==3.5.0,pkg:charset-normalizer==3.4.9,pkg:click-didyoumean==0.3.0,pkg:click-plugins==1.1.1,pkg:click-repl==0.2.0,pkg:click==8.4.2,pkg:colorama==0.4.6,pkg:cryptography==49.0.0,pkg:distlib==0.4.3,pkg:docutils==0.22.4,pkg:filelock==3.29.7,pkg:flask==2.3.2,pkg:greenlet==3.5.3,pkg:h11==0.16.0,pkg:identify==2.6.19,pkg:idna==3.18,pkg:imagesize==2.0.0,pkg:iniconfig==2.3.0,pkg:itsdangerous==2.2.0,pkg:jinja2==3.1.6,pkg:kombu==5.2.4,pkg:librt==0.13.0,pkg:markupsafe==3.0.3,pkg:mypy-extensions==1.1.0,pkg:mypy==2.3.0,pkg:nodeenv==1.10.0,pkg:packaging==26.2,pkg:pathspec==1.1.1,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pre-commit-uv==4.2.2,pkg:pre-commit==4.6.0,pkg:prompt-toolkit==3.0.38,pkg:pycparser==3.0,pkg:pygments==2.20.0,pkg:pyproject-api==1.10.1,pkg:pyright==1.1.411,pkg:pytest==9.1.1,pkg:python-discovery==1.4.4,pkg:python-dotenv==1.2.2,pkg:pytz==2023.3,pkg:pyyaml==6.0.3,pkg:redis==4.5.4,pkg:requests==2.34.2,pkg:roman-numerals==4.1.0,pkg:ruff==0.15.21,pkg:six==1.16.0,pkg:snowballstemmer==3.1.1,pkg:sphinx-autobuild==2025.8.25,pkg:sphinx==9.0.4,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,pkg:starlette==1.3.1,pkg:tomli-w==1.2.0,pkg:tox-uv-bare==1.35.2,pkg:tox-uv==1.35.2,pkg:tox==4.56.4,pkg:types-contextvars==2.4.7.3,pkg:types-dataclasses==0.6.6,pkg:typing-extensions==4.16.0,pkg:urllib3==2.7.0,pkg:uv==0.11.28,pkg:uvicorn==0.51.0,pkg:vine==5.0.0,pkg:virtualenv==21.6.1,pkg:watchfiles==1.2.0,pkg:wcwidth==0.2.6,pkg:websockets==16.1,pkg:werkzeug==3.1.8  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pybuild-plugin-pyproject  provider=apt:pybuild-plugin-pyproject  requires=-  unblocks=pkg:amqp==5.1.1,pkg:anyio==4.14.2,pkg:asgiref==3.11.1,pkg:blinker==1.9.0,pkg:cachetools==7.1.4,pkg:celery==5.2.7,pkg:cffi==2.1.0,pkg:charset-normalizer==3.4.9,pkg:colorama==0.4.6,pkg:cryptography==49.0.0,pkg:filelock==3.29.7,pkg:flask==2.3.2,pkg:idna==3.18,pkg:itsdangerous==2.2.0,pkg:jinja2==3.1.6,pkg:mypy==2.3.0,pkg:packaging==26.2,pkg:pathspec==1.1.1,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:prompt-toolkit==3.0.38,pkg:pyproject-api==1.10.1,pkg:pytest==9.1.1,pkg:pyyaml==6.0.3,pkg:redis==4.5.4,pkg:requests==2.34.2,pkg:six==1.16.0,pkg:sphinx-autobuild==2025.8.25,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,pkg:starlette==1.3.1,pkg:tomli-w==1.2.0,pkg:typing-extensions==4.16.0,pkg:urllib3==2.7.0,pkg:uvicorn==0.51.0,pkg:virtualenv==21.6.1,pkg:watchfiles==1.2.0,pkg:werkzeug==3.1.8  toolchain
#@check dpkg-query -W -f='${Status}' pybuild-plugin-pyproject 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pybuild-plugin-pyproject
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pybuild-plugin-pyproject" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pyflakes3  provider=apt:pyflakes3  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' pyflakes3 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pyflakes3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pyflakes3" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:python3:any  provider=apt:python3:any  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' python3:any 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends python3:any
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:python3:any" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:sphinx-common  provider=apt:sphinx-common  requires=-  unblocks=pkg:charset-normalizer==3.4.9  toolchain
#@check dpkg-query -W -f='${Status}' sphinx-common 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends sphinx-common
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:sphinx-common" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:texlive-latex-base  provider=apt:texlive-latex-base  requires=-  unblocks=pkg:celery==5.2.7  toolchain
#@check dpkg-query -W -f='${Status}' texlive-latex-base 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends texlive-latex-base
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:texlive-latex-base" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:texlive-latex-extra  provider=apt:texlive-latex-extra  requires=-  unblocks=pkg:celery==5.2.7  toolchain
#@check dpkg-query -W -f='${Status}' texlive-latex-extra 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends texlive-latex-extra
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:texlive-latex-extra" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:tzdata-legacy  provider=apt:tzdata-legacy  requires=-  unblocks=pkg:celery==5.2.7  toolchain
#@check dpkg-query -W -f='${Status}' tzdata-legacy 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends tzdata-legacy
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:tzdata-legacy" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:unicode-data  provider=apt:unicode-data  requires=-  unblocks=pkg:wcwidth==0.2.6  toolchain
#@check dpkg-query -W -f='${Status}' unicode-data 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends unicode-data
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:unicode-data" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:unzip  provider=apt:unzip  requires=-  unblocks=pkg:virtualenv==21.6.1  toolchain
#@check dpkg-query -W -f='${Status}' unzip 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends unzip
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:unzip" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:valac  provider=apt:valac  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' valac 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends valac
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:valac" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:virtualenv  provider=apt:virtualenv  requires=-  unblocks=pkg:cffi==2.1.0  toolchain
#@check dpkg-query -W -f='${Status}' virtualenv 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends virtualenv
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:virtualenv" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:zip  provider=apt:zip  requires=-  unblocks=pkg:virtualenv==21.6.1  toolchain
#@check dpkg-query -W -f='${Status}' zip 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends zip
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:zip" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:alabaster==1.0.0  version=1.0.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show alabaster
if python3 -m pip install --break-system-packages --no-deps alabaster==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:alabaster==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:asgiref==3.11.1  version=3.11.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential
#@check python -m pip show asgiref
if python3 -m pip install --break-system-packages --no-deps asgiref==3.11.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:asgiref==3.11.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ast-serialize==0.6.0  version=0.6.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:mypy==2.3.0
#@check python -m pip show ast-serialize
if python3 -m pip install --break-system-packages --no-deps ast-serialize==0.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ast-serialize==0.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:async-timeout==4.0.2  version=4.0.2  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:redis==4.5.4
#@check python -m pip show async-timeout
if python3 -m pip install --break-system-packages --no-deps async-timeout==4.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:async-timeout==4.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:babel==2.18.0  version=2.18.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show babel
if python3 -m pip install --break-system-packages --no-deps babel==2.18.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:babel==2.18.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:billiard==3.6.4.0  version=3.6.4.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show billiard
if python3 -m pip install --break-system-packages --no-deps billiard==3.6.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:billiard==3.6.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:blinker==1.9.0  version=1.9.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:flask==2.3.2
#@check python -m pip show blinker
if python3 -m pip install --break-system-packages --no-deps blinker==1.9.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:blinker==1.9.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cachetools==7.1.4  version=7.1.4  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:tox==4.56.4
#@check python -m pip show cachetools
if python3 -m pip install --break-system-packages --no-deps cachetools==7.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cachetools==7.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2026.6.17  version=2026.6.17  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:requests==2.34.2
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2026.6.17
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2026.6.17" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cfgv==3.5.0  version=3.5.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show cfgv
if python3 -m pip install --break-system-packages --no-deps cfgv==3.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cfgv==3.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.4.9  version=3.4.9  requires=aptdep:pybuild-plugin-pyproject,aptdep:sphinx-common,binary:pkg-config,tool:build-essential  unblocks=pkg:requests==2.34.2
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.4.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.4.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.4.2  version=8.4.2  requires=aptdep:dbus-test-runner,aptdep:gir1.2-glib-2.0,aptdep:gobject-introspection,aptdep:libgee-0.8-dev,aptdep:libgirepository1.0-dev,aptdep:libglib2.0-dev,aptdep:libjson-glib-dev,aptdep:libproperties-cpp-dev,aptdep:pyflakes3,aptdep:python3:any,aptdep:valac,binary:pkg-config,tool:build-essential  unblocks=pkg:celery==5.2.7,pkg:click-didyoumean==0.3.0,pkg:click-plugins==1.1.1,pkg:click-repl==0.2.0,pkg:flask==2.3.2,pkg:uvicorn==0.51.0
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click-didyoumean==0.3.0  version=0.3.0  requires=binary:pkg-config,pkg:click==8.4.2,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show click-didyoumean
if python3 -m pip install --break-system-packages --no-deps click-didyoumean==0.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click-didyoumean==0.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click-plugins==1.1.1  version=1.1.1  requires=binary:pkg-config,pkg:click==8.4.2,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show click-plugins
if python3 -m pip install --break-system-packages --no-deps click-plugins==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click-plugins==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:colorama==0.4.6  version=0.4.6  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx-autobuild==2025.8.25,pkg:tox==4.56.4
#@check python -m pip show colorama
if python3 -m pip install --break-system-packages --no-deps colorama==0.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorama==0.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.4.3  version=0.4.3  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:virtualenv==21.6.1
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:docutils==0.22.4  version=0.22.4  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show docutils
if python3 -m pip install --break-system-packages --no-deps docutils==0.22.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:docutils==0.22.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.7  version=3.29.7  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:python-discovery==1.4.4,pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:greenlet==3.5.3  version=3.5.3  requires=aptdep:furo,binary:pkg-config,tool:build-essential
#@check python -m pip show greenlet
if python3 -m pip install --break-system-packages --no-deps greenlet==3.5.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:greenlet==3.5.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:h11==0.16.0  version=0.16.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:uvicorn==0.51.0
#@check python -m pip show h11
if python3 -m pip install --break-system-packages --no-deps h11==0.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:h11==0.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:identify==2.6.19  version=2.6.19  requires=aptdep:help2man,binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show identify
if python3 -m pip install --break-system-packages --no-deps identify==2.6.19
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:identify==2.6.19" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.18  version=3.18  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:anyio==4.14.2,pkg:requests==2.34.2
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:imagesize==2.0.0  version=2.0.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show imagesize
if python3 -m pip install --break-system-packages --no-deps imagesize==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:imagesize==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pytest==9.1.1
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:itsdangerous==2.2.0  version=2.2.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:flask==2.3.2
#@check python -m pip show itsdangerous
if python3 -m pip install --break-system-packages --no-deps itsdangerous==2.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:itsdangerous==2.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:librt==0.13.0  version=0.13.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:mypy==2.3.0
#@check python -m pip show librt
if python3 -m pip install --break-system-packages --no-deps librt==0.13.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:librt==0.13.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markupsafe==3.0.3  version=3.0.3  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:jinja2==3.1.6,pkg:werkzeug==3.1.8
#@check python -m pip show markupsafe
if python3 -m pip install --break-system-packages --no-deps markupsafe==3.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markupsafe==3.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jinja2==3.1.6  version=3.1.6  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:markupsafe==3.0.3,tool:build-essential  unblocks=pkg:flask==2.3.2,pkg:sphinx==9.0.4
#@check python -m pip show jinja2
if python3 -m pip install --break-system-packages --no-deps jinja2==3.1.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jinja2==3.1.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.1.0  version=1.1.0  requires=aptdep:flake8,aptdep:help2man,binary:pkg-config,tool:build-essential  unblocks=pkg:mypy==2.3.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.10.0  version=1.10.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==4.6.0,pkg:pyright==1.1.411
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pyproject-api==1.10.1,pkg:pytest==9.1.1,pkg:sphinx==9.0.4,pkg:tox-uv-bare==1.35.2,pkg:tox==4.56.4
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pathspec==1.1.1  version=1.1.1  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:mypy==2.3.0
#@check python -m pip show pathspec
if python3 -m pip install --break-system-packages --no-deps pathspec==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pathspec==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:python-discovery==1.4.4,pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pytest==9.1.1,pkg:tox==4.56.4
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycparser==3.0  version=3.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:cffi==2.1.0
#@check python -m pip show pycparser
if python3 -m pip install --break-system-packages --no-deps pycparser==3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycparser==3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cffi==2.1.0  version=2.1.0  requires=aptdep:libffi-dev,aptdep:pybuild-plugin-pyproject,aptdep:virtualenv,binary:pkg-config,pkg:pycparser==3.0,tool:build-essential  unblocks=pkg:cryptography==49.0.0
#@check python -m pip show cffi
if python3 -m pip install --break-system-packages --no-deps cffi==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cffi==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cryptography==49.0.0  version=49.0.0  requires=aptdep:cargo,aptdep:libssl-dev,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:cffi==2.1.0,tool:build-essential
#@check python -m pip show cryptography
if python3 -m pip install --break-system-packages --no-deps cryptography==49.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cryptography==49.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pytest==9.1.1,pkg:sphinx==9.0.4
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-api==1.10.1  version=1.10.1  requires=aptdep:furo,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:packaging==26.2,tool:build-essential  unblocks=pkg:tox==4.56.4
#@check python -m pip show pyproject-api
if python3 -m pip install --break-system-packages --no-deps pyproject-api==1.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-api==1.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.1.1  version=9.1.1  requires=aptdep:furo,aptdep:lsof,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0,tool:build-essential
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-discovery==1.4.4  version=1.4.4  requires=binary:pkg-config,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,tool:build-essential  unblocks=pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show python-discovery
if python3 -m pip install --break-system-packages --no-deps python-discovery==1.4.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-discovery==1.4.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-dotenv==1.2.2  version=1.2.2  requires=aptdep:help2man,binary:pkg-config,tool:build-essential
#@check python -m pip show python-dotenv
if python3 -m pip install --break-system-packages --no-deps python-dotenv==1.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-dotenv==1.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytz==2023.3  version=2023.3  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show pytz
if python3 -m pip install --break-system-packages --no-deps pytz==2023.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytz==2023.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml==6.0.3  version=6.0.3  requires=aptdep:libyaml-dev,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:redis==4.5.4  version=4.5.4  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:async-timeout==4.0.2,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show redis
if python3 -m pip install --break-system-packages --no-deps redis==4.5.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:redis==4.5.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:roman-numerals==4.1.0  version=4.1.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show roman-numerals
if python3 -m pip install --break-system-packages --no-deps roman-numerals==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:roman-numerals==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.15.21  version=0.15.21  requires=binary:pkg-config,tool:build-essential
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.15.21
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.15.21" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:six==1.16.0  version=1.16.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:click-repl==0.2.0
#@check python -m pip show six
if python3 -m pip install --break-system-packages --no-deps six==1.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:six==1.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:snowballstemmer==3.1.1  version=3.1.1  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show snowballstemmer
if python3 -m pip install --break-system-packages --no-deps snowballstemmer==3.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:snowballstemmer==3.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-applehelp==2.0.0  version=2.0.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-applehelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-applehelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-applehelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-devhelp==2.0.0  version=2.0.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-devhelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-devhelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-devhelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-htmlhelp==2.1.0  version=2.1.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-htmlhelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-htmlhelp==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-htmlhelp==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-jsmath==1.0.1  version=1.0.1  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-jsmath
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-jsmath==1.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-jsmath==1.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-qthelp==2.0.0  version=2.0.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-qthelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-qthelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-qthelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-serializinghtml==2.0.0  version=2.0.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-serializinghtml
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-serializinghtml==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-serializinghtml==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli-w==1.2.0  version=1.2.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:tox==4.56.4
#@check python -m pip show tomli-w
if python3 -m pip install --break-system-packages --no-deps tomli-w==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli-w==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-contextvars==2.4.7.3  version=2.4.7.3  requires=binary:pkg-config,tool:build-essential
#@check python -m pip show types-contextvars
if python3 -m pip install --break-system-packages --no-deps types-contextvars==2.4.7.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-contextvars==2.4.7.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-dataclasses==0.6.6  version=0.6.6  requires=binary:pkg-config,tool:build-essential
#@check python -m pip show types-dataclasses
if python3 -m pip install --break-system-packages --no-deps types-dataclasses==0.6.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-dataclasses==0.6.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.16.0  version=4.16.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:anyio==4.14.2,pkg:mypy==2.3.0,pkg:pyright==1.1.411,pkg:starlette==1.3.1
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:anyio==4.14.2  version=4.14.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:idna==3.18,pkg:typing-extensions==4.16.0,tool:build-essential  unblocks=pkg:starlette==1.3.1,pkg:watchfiles==1.2.0
#@check python -m pip show anyio
if python3 -m pip install --break-system-packages --no-deps anyio==4.14.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:anyio==4.14.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==2.3.0  version=2.3.0  requires=aptdep:flake8,aptdep:furo,aptdep:help2man,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:ast-serialize==0.6.0,pkg:librt==0.13.0,pkg:mypy-extensions==1.1.0,pkg:pathspec==1.1.1,pkg:typing-extensions==4.16.0,tool:build-essential
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyright==1.1.411  version=1.1.411  requires=binary:pkg-config,pkg:nodeenv==1.10.0,pkg:typing-extensions==4.16.0,tool:build-essential
#@check python -m pip show pyright
if python3 -m pip install --break-system-packages --no-deps pyright==1.1.411
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyright==1.1.411" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:starlette==1.3.1  version=1.3.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:anyio==4.14.2,pkg:typing-extensions==4.16.0,tool:build-essential  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show starlette
if python3 -m pip install --break-system-packages --no-deps starlette==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:starlette==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:requests==2.34.2
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=aptdep:openssl,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:certifi==2026.6.17,pkg:charset-normalizer==3.4.9,pkg:idna==3.18,pkg:urllib3==2.7.0,tool:build-essential  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.34.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.34.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx==9.0.4  version=9.0.4  requires=binary:pkg-config,pkg:alabaster==1.0.0,pkg:babel==2.18.0,pkg:docutils==0.22.4,pkg:imagesize==2.0.0,pkg:jinja2==3.1.6,pkg:packaging==26.2,pkg:pygments==2.20.0,pkg:requests==2.34.2,pkg:roman-numerals==4.1.0,pkg:snowballstemmer==3.1.1,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,tool:build-essential  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show sphinx
if python3 -m pip install --break-system-packages --no-deps sphinx==9.0.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx==9.0.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:uv==0.11.28  version=0.11.28  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit-uv==4.2.2,pkg:tox-uv==1.35.2
#@check python -m pip show uv
if python3 -m pip install --break-system-packages --no-deps uv==0.11.28
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:uv==0.11.28" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:uvicorn==0.51.0  version=0.51.0  requires=aptdep:help2man,aptdep:mkdocs,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:click==8.4.2,pkg:h11==0.16.0,tool:build-essential  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show uvicorn
if python3 -m pip install --break-system-packages --no-deps uvicorn==0.51.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:uvicorn==0.51.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:vine==5.0.0  version=5.0.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:amqp==5.1.1,pkg:celery==5.2.7,pkg:kombu==5.2.4
#@check python -m pip show vine
if python3 -m pip install --break-system-packages --no-deps vine==5.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:vine==5.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:amqp==5.1.1  version=5.1.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:vine==5.0.0,tool:build-essential  unblocks=pkg:kombu==5.2.4
#@check python -m pip show amqp
if python3 -m pip install --break-system-packages --no-deps amqp==5.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:amqp==5.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:kombu==5.2.4  version=5.2.4  requires=binary:pkg-config,pkg:amqp==5.1.1,pkg:vine==5.0.0,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show kombu
if python3 -m pip install --break-system-packages --no-deps kombu==5.2.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:kombu==5.2.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==21.6.1  version=21.6.1  requires=aptdep:pybuild-plugin-pyproject,aptdep:unzip,aptdep:zip,binary:pkg-config,pkg:distlib==0.4.3,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,pkg:python-discovery==1.4.4,tool:build-essential  unblocks=pkg:pre-commit==4.6.0,pkg:tox==4.56.4
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==21.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==21.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit==4.6.0  version=4.6.0  requires=binary:pkg-config,pkg:cfgv==3.5.0,pkg:identify==2.6.19,pkg:nodeenv==1.10.0,pkg:pyyaml==6.0.3,pkg:virtualenv==21.6.1,tool:build-essential  unblocks=pkg:pre-commit-uv==4.2.2
#@check python -m pip show pre-commit
if python3 -m pip install --break-system-packages --no-deps pre-commit==4.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit==4.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit-uv==4.2.2  version=4.2.2  requires=binary:pkg-config,pkg:pre-commit==4.6.0,pkg:uv==0.11.28,tool:build-essential
#@check python -m pip show pre-commit-uv
if python3 -m pip install --break-system-packages --no-deps pre-commit-uv==4.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit-uv==4.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox==4.56.4  version=4.56.4  requires=binary:pkg-config,pkg:cachetools==7.1.4,pkg:colorama==0.4.6,pkg:filelock==3.29.7,pkg:packaging==26.2,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pyproject-api==1.10.1,pkg:python-discovery==1.4.4,pkg:tomli-w==1.2.0,pkg:virtualenv==21.6.1,tool:build-essential  unblocks=pkg:tox-uv-bare==1.35.2
#@check python -m pip show tox
if python3 -m pip install --break-system-packages --no-deps tox==4.56.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox==4.56.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox-uv-bare==1.35.2  version=1.35.2  requires=binary:pkg-config,pkg:packaging==26.2,pkg:tox==4.56.4,tool:build-essential  unblocks=pkg:tox-uv==1.35.2
#@check python -m pip show tox-uv-bare
if python3 -m pip install --break-system-packages --no-deps tox-uv-bare==1.35.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox-uv-bare==1.35.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox-uv==1.35.2  version=1.35.2  requires=binary:pkg-config,pkg:tox-uv-bare==1.35.2,pkg:uv==0.11.28,tool:build-essential
#@check python -m pip show tox-uv
if python3 -m pip install --break-system-packages --no-deps tox-uv==1.35.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox-uv==1.35.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:watchfiles==1.2.0  version=1.2.0  requires=aptdep:cargo,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:anyio==4.14.2,tool:build-essential  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show watchfiles
if python3 -m pip install --break-system-packages --no-deps watchfiles==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:watchfiles==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wcwidth==0.2.6  version=0.2.6  requires=aptdep:unicode-data,binary:pkg-config,tool:build-essential  unblocks=pkg:prompt-toolkit==3.0.38
#@check python -m pip show wcwidth
if python3 -m pip install --break-system-packages --no-deps wcwidth==0.2.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wcwidth==0.2.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:prompt-toolkit==3.0.38  version=3.0.38  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:wcwidth==0.2.6,tool:build-essential  unblocks=pkg:click-repl==0.2.0
#@check python -m pip show prompt-toolkit
if python3 -m pip install --break-system-packages --no-deps prompt-toolkit==3.0.38
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:prompt-toolkit==3.0.38" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click-repl==0.2.0  version=0.2.0  requires=binary:pkg-config,pkg:click==8.4.2,pkg:prompt-toolkit==3.0.38,pkg:six==1.16.0,tool:build-essential  unblocks=pkg:celery==5.2.7
#@check python -m pip show click-repl
if python3 -m pip install --break-system-packages --no-deps click-repl==0.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click-repl==0.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:celery==5.2.7  version=5.2.7  requires=aptdep:dvipng,aptdep:locales,aptdep:pybuild-plugin-pyproject,aptdep:texlive-latex-base,aptdep:texlive-latex-extra,aptdep:tzdata-legacy,binary:pkg-config,pkg:billiard==3.6.4.0,pkg:click-didyoumean==0.3.0,pkg:click-plugins==1.1.1,pkg:click-repl==0.2.0,pkg:click==8.4.2,pkg:kombu==5.2.4,pkg:pytz==2023.3,pkg:redis==4.5.4,pkg:vine==5.0.0,tool:build-essential
#@check python -m pip show celery
if python3 -m pip install --break-system-packages --no-deps celery==5.2.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:celery==5.2.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:websockets==16.1  version=16.1  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show websockets
if python3 -m pip install --break-system-packages --no-deps websockets==16.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:websockets==16.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx-autobuild==2025.8.25  version=2025.8.25  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:colorama==0.4.6,pkg:sphinx==9.0.4,pkg:starlette==1.3.1,pkg:uvicorn==0.51.0,pkg:watchfiles==1.2.0,pkg:websockets==16.1,tool:build-essential
#@check python -m pip show sphinx-autobuild
if python3 -m pip install --break-system-packages --no-deps sphinx-autobuild==2025.8.25
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx-autobuild==2025.8.25" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:werkzeug==3.1.8  version=3.1.8  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:markupsafe==3.0.3,tool:build-essential  unblocks=pkg:flask==2.3.2
#@check python -m pip show werkzeug
if python3 -m pip install --break-system-packages --no-deps werkzeug==3.1.8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:werkzeug==3.1.8" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:flask==2.3.2  version=2.3.2  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:blinker==1.9.0,pkg:click==8.4.2,pkg:itsdangerous==2.2.0,pkg:jinja2==3.1.6,pkg:werkzeug==3.1.8,tool:build-essential
#@check python -m pip show flask
if python3 -m pip install --break-system-packages --no-deps flask==2.3.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:flask==2.3.2" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:Flask  requires=pkg:amqp==5.1.1,pkg:asgiref==3.11.1,pkg:async-timeout==4.0.2,pkg:billiard==3.6.4.0,pkg:blinker==1.9.0,pkg:celery==5.2.7,pkg:click-didyoumean==0.3.0,pkg:click-plugins==1.1.1,pkg:click-repl==0.2.0,pkg:click==8.4.2,pkg:cryptography==49.0.0,pkg:flask==2.3.2,pkg:greenlet==3.5.3,pkg:itsdangerous==2.2.0,pkg:jinja2==3.1.6,pkg:kombu==5.2.4,pkg:markupsafe==3.0.3,pkg:mypy==2.3.0,pkg:pre-commit-uv==4.2.2,pkg:pre-commit==4.6.0,pkg:prompt-toolkit==3.0.38,pkg:pyright==1.1.411,pkg:pytest==9.1.1,pkg:python-dotenv==1.2.2,pkg:pytz==2023.3,pkg:redis==4.5.4,pkg:ruff==0.15.21,pkg:six==1.16.0,pkg:sphinx-autobuild==2025.8.25,pkg:sphinx==9.0.4,pkg:tox-uv==1.35.2,pkg:tox==4.56.4,pkg:types-contextvars==2.4.7.3,pkg:types-dataclasses==0.6.6,pkg:vine==5.0.0,pkg:wcwidth==0.2.6,pkg:werkzeug==3.1.8
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:Flask" >> /tmp/v3_failed_nodes.log
fi
