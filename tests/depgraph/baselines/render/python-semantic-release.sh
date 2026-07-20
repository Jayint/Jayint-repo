#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 64 reciped (2 toolchain, 62 pip) + 0 needs
#   graph-hash: sha256:cda745b1d891   python: 3.11   platform: aarch64-manylinux_2_28   exclude-newer: 2024-06-28
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:pytest-clarity==1.0.1  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:pytest-clarity==1.0.1  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:annotated-types==0.7.0  version=0.7.0  requires=-  unblocks=pkg:pydantic==2.7.4
#@check python -m pip show annotated-types
if python3 -m pip install --break-system-packages --no-deps annotated-types==0.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:annotated-types==0.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cachetools==5.3.3  version=5.3.3  requires=-  unblocks=pkg:tox==4.15.1
#@check python -m pip show cachetools
if python3 -m pip install --break-system-packages --no-deps cachetools==5.3.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cachetools==5.3.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2024.6.2  version=2024.6.2  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2024.6.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2024.6.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cfgv==3.4.0  version=3.4.0  requires=-  unblocks=pkg:pre-commit==3.7.1
#@check python -m pip show cfgv
if python3 -m pip install --break-system-packages --no-deps cfgv==3.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cfgv==3.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:chardet==5.2.0  version=5.2.0  requires=-  unblocks=pkg:tox==4.15.1
#@check python -m pip show chardet
if python3 -m pip install --break-system-packages --no-deps chardet==5.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:chardet==5.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.3.2  version=3.3.2  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.3.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.3.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:colorama==0.4.6  version=0.4.6  requires=-  unblocks=pkg:click==8.1.7,pkg:pytest==7.4.4,pkg:tox==4.15.1
#@check python -m pip show colorama
if python3 -m pip install --break-system-packages --no-deps colorama==0.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorama==0.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.1.7  version=8.1.7  requires=pkg:colorama==0.4.6  unblocks=pkg:click-option-group==0.5.6
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.1.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.1.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click-option-group==0.5.6  version=0.5.6  requires=pkg:click==8.1.7
#@check python -m pip show click-option-group
if python3 -m pip install --break-system-packages --no-deps click-option-group==0.5.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click-option-group==0.5.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.5.4  version=7.5.4  requires=-  unblocks=pkg:pytest-cov==5.0.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.5.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.5.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.3.8  version=0.3.8  requires=-  unblocks=pkg:virtualenv==20.26.3
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.3.8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.3.8" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:dotty-dict==1.3.1  version=1.3.1  requires=-
#@check python -m pip show dotty-dict
if python3 -m pip install --break-system-packages --no-deps dotty-dict==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:dotty-dict==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:execnet==2.1.1  version=2.1.1  requires=-  unblocks=pkg:pytest-xdist==3.6.1
#@check python -m pip show execnet
if python3 -m pip install --break-system-packages --no-deps execnet==2.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:execnet==2.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.15.4  version=3.15.4  requires=-  unblocks=pkg:tox==4.15.1,pkg:virtualenv==20.26.3
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.15.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.15.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:identify==2.5.36  version=2.5.36  requires=-  unblocks=pkg:pre-commit==3.7.1
#@check python -m pip show identify
if python3 -m pip install --break-system-packages --no-deps identify==2.5.36
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:identify==2.5.36" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.7  version=3.7  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:importlib-resources==6.4.0  version=6.4.0  requires=-
#@check python -m pip show importlib-resources
if python3 -m pip install --break-system-packages --no-deps importlib-resources==6.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:importlib-resources==6.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.0.0  version=2.0.0  requires=-  unblocks=pkg:pytest==7.4.4
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markupsafe==2.1.5  version=2.1.5  requires=-  unblocks=pkg:jinja2==3.1.4
#@check python -m pip show markupsafe
if python3 -m pip install --break-system-packages --no-deps markupsafe==2.1.5
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markupsafe==2.1.5" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jinja2==3.1.4  version=3.1.4  requires=pkg:markupsafe==2.1.5
#@check python -m pip show jinja2
if python3 -m pip install --break-system-packages --no-deps jinja2==3.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jinja2==3.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mdurl==0.1.2  version=0.1.2  requires=-  unblocks=pkg:markdown-it-py==3.0.0
#@check python -m pip show mdurl
if python3 -m pip install --break-system-packages --no-deps mdurl==0.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mdurl==0.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown-it-py==3.0.0  version=3.0.0  requires=pkg:mdurl==0.1.2  unblocks=pkg:rich==13.7.1
#@check python -m pip show markdown-it-py
if python3 -m pip install --break-system-packages --no-deps markdown-it-py==3.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown-it-py==3.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.0.0  version=1.0.0  requires=-  unblocks=pkg:mypy==1.10.1
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.9.1  version=1.9.1  requires=-  unblocks=pkg:pre-commit==3.7.1
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.9.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.9.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==24.1  version=24.1  requires=-  unblocks=pkg:pyproject-api==1.7.1,pkg:pytest==7.4.4,pkg:tox==4.15.1
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==24.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==24.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.2.2  version=4.2.2  requires=-  unblocks=pkg:tox==4.15.1,pkg:virtualenv==20.26.3
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.5.0  version=1.5.0  requires=-  unblocks=pkg:pytest==7.4.4,pkg:tox==4.15.1
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pprintpp==0.4.0  version=0.4.0  requires=-  unblocks=pkg:pytest-clarity==1.0.1
#@check python -m pip show pprintpp
if python3 -m pip install --break-system-packages --no-deps pprintpp==0.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pprintpp==0.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.18.0  version=2.18.0  requires=-  unblocks=pkg:rich==13.7.1
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.18.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.18.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-api==1.7.1  version=1.7.1  requires=pkg:packaging==24.1  unblocks=pkg:tox==4.15.1
#@check python -m pip show pyproject-api
if python3 -m pip install --break-system-packages --no-deps pyproject-api==1.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-api==1.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==7.4.4  version=7.4.4  requires=pkg:colorama==0.4.6,pkg:iniconfig==2.0.0,pkg:packaging==24.1,pkg:pluggy==1.5.0  unblocks=pkg:pytest-clarity==1.0.1,pkg:pytest-cov==5.0.0,pkg:pytest-env==1.1.3,pkg:pytest-lazy-fixture==0.6.3,pkg:pytest-mock==3.14.0,pkg:pytest-pretty==1.2.0,pkg:pytest-xdist==3.6.1
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==7.4.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==7.4.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==5.0.0  version=5.0.0  requires=pkg:coverage==7.5.4,pkg:pytest==7.4.4
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==5.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==5.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-env==1.1.3  version=1.1.3  requires=pkg:pytest==7.4.4
#@check python -m pip show pytest-env
if python3 -m pip install --break-system-packages --no-deps pytest-env==1.1.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-env==1.1.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-lazy-fixture==0.6.3  version=0.6.3  requires=pkg:pytest==7.4.4
#@check python -m pip show pytest-lazy-fixture
if python3 -m pip install --break-system-packages --no-deps pytest-lazy-fixture==0.6.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-lazy-fixture==0.6.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-mock==3.14.0  version=3.14.0  requires=pkg:pytest==7.4.4
#@check python -m pip show pytest-mock
if python3 -m pip install --break-system-packages --no-deps pytest-mock==3.14.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-mock==3.14.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-xdist==3.6.1  version=3.6.1  requires=pkg:execnet==2.1.1,pkg:pytest==7.4.4
#@check python -m pip show pytest-xdist
if python3 -m pip install --break-system-packages --no-deps pytest-xdist==3.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-xdist==3.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml==6.0.1  version=6.0.1  requires=-  unblocks=pkg:pre-commit==3.7.1,pkg:responses==0.25.3
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rich==13.7.1  version=13.7.1  requires=pkg:markdown-it-py==3.0.0,pkg:pygments==2.18.0  unblocks=pkg:pytest-clarity==1.0.1,pkg:pytest-pretty==1.2.0
#@check python -m pip show rich
if python3 -m pip install --break-system-packages --no-deps rich==13.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rich==13.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-clarity==1.0.1  version=1.0.1  requires=binary:pkg-config,pkg:pprintpp==0.4.0,pkg:pytest==7.4.4,pkg:rich==13.7.1,tool:build-essential  build-from-source
#@check python -m pip show pytest-clarity
if python3 -m pip install --break-system-packages --no-deps pytest-clarity==1.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-clarity==1.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-pretty==1.2.0  version=1.2.0  requires=pkg:pytest==7.4.4,pkg:rich==13.7.1
#@check python -m pip show pytest-pretty
if python3 -m pip install --break-system-packages --no-deps pytest-pretty==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-pretty==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.5.0  version=0.5.0  requires=-
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:shellingham==1.5.4  version=1.5.4  requires=-
#@check python -m pip show shellingham
if python3 -m pip install --break-system-packages --no-deps shellingham==1.5.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:shellingham==1.5.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:smmap==5.0.1  version=5.0.1  requires=-  unblocks=pkg:gitdb==4.0.11
#@check python -m pip show smmap
if python3 -m pip install --break-system-packages --no-deps smmap==5.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:smmap==5.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:gitdb==4.0.11  version=4.0.11  requires=pkg:smmap==5.0.1  unblocks=pkg:gitpython==3.1.43
#@check python -m pip show gitdb
if python3 -m pip install --break-system-packages --no-deps gitdb==4.0.11
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:gitdb==4.0.11" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:gitpython==3.1.43  version=3.1.43  requires=pkg:gitdb==4.0.11
#@check python -m pip show gitpython
if python3 -m pip install --break-system-packages --no-deps gitpython==3.1.43
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:gitpython==3.1.43" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.0.1  version=2.0.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomlkit==0.12.5  version=0.12.5  requires=-
#@check python -m pip show tomlkit
if python3 -m pip install --break-system-packages --no-deps tomlkit==0.12.5
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomlkit==0.12.5" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-pytest-lazy-fixture==0.6.3.20240310  version=0.6.3.20240310  requires=-
#@check python -m pip show types-pytest-lazy-fixture
if python3 -m pip install --break-system-packages --no-deps types-pytest-lazy-fixture==0.6.3.20240310
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-pytest-lazy-fixture==0.6.3.20240310" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.12.2  version=4.12.2  requires=-  unblocks=pkg:mypy==1.10.1,pkg:pydantic-core==2.18.4,pkg:pydantic==2.7.4
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.12.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.12.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==1.10.1  version=1.10.1  requires=pkg:mypy-extensions==1.0.0,pkg:typing-extensions==4.12.2
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==1.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==1.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pydantic-core==2.18.4  version=2.18.4  requires=pkg:typing-extensions==4.12.2  unblocks=pkg:pydantic==2.7.4
#@check python -m pip show pydantic-core
if python3 -m pip install --break-system-packages --no-deps pydantic-core==2.18.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pydantic-core==2.18.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pydantic==2.7.4  version=2.7.4  requires=pkg:annotated-types==0.7.0,pkg:pydantic-core==2.18.4,pkg:typing-extensions==4.12.2
#@check python -m pip show pydantic
if python3 -m pip install --break-system-packages --no-deps pydantic==2.7.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pydantic==2.7.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.2.2  version=2.2.2  requires=-  unblocks=pkg:requests==2.32.3,pkg:responses==0.25.3,pkg:types-requests==2.32.0.20240622
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.32.3  version=2.32.3  requires=pkg:certifi==2024.6.2,pkg:charset-normalizer==3.3.2,pkg:idna==3.7,pkg:urllib3==2.2.2  unblocks=pkg:python-gitlab==4.7.0,pkg:requests-mock==1.12.1,pkg:requests-toolbelt==1.0.0,pkg:responses==0.25.3
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.32.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.32.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests-mock==1.12.1  version=1.12.1  requires=pkg:requests==2.32.3
#@check python -m pip show requests-mock
if python3 -m pip install --break-system-packages --no-deps requests-mock==1.12.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests-mock==1.12.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests-toolbelt==1.0.0  version=1.0.0  requires=pkg:requests==2.32.3  unblocks=pkg:python-gitlab==4.7.0
#@check python -m pip show requests-toolbelt
if python3 -m pip install --break-system-packages --no-deps requests-toolbelt==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests-toolbelt==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-gitlab==4.7.0  version=4.7.0  requires=pkg:requests-toolbelt==1.0.0,pkg:requests==2.32.3
#@check python -m pip show python-gitlab
if python3 -m pip install --break-system-packages --no-deps python-gitlab==4.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-gitlab==4.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:responses==0.25.3  version=0.25.3  requires=pkg:pyyaml==6.0.1,pkg:requests==2.32.3,pkg:urllib3==2.2.2
#@check python -m pip show responses
if python3 -m pip install --break-system-packages --no-deps responses==0.25.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:responses==0.25.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-requests==2.32.0.20240622  version=2.32.0.20240622  requires=pkg:urllib3==2.2.2
#@check python -m pip show types-requests
if python3 -m pip install --break-system-packages --no-deps types-requests==2.32.0.20240622
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-requests==2.32.0.20240622" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==20.26.3  version=20.26.3  requires=pkg:distlib==0.3.8,pkg:filelock==3.15.4,pkg:platformdirs==4.2.2  unblocks=pkg:pre-commit==3.7.1,pkg:tox==4.15.1
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==20.26.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==20.26.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit==3.7.1  version=3.7.1  requires=pkg:cfgv==3.4.0,pkg:identify==2.5.36,pkg:nodeenv==1.9.1,pkg:pyyaml==6.0.1,pkg:virtualenv==20.26.3
#@check python -m pip show pre-commit
if python3 -m pip install --break-system-packages --no-deps pre-commit==3.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit==3.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox==4.15.1  version=4.15.1  requires=pkg:cachetools==5.3.3,pkg:chardet==5.2.0,pkg:colorama==0.4.6,pkg:filelock==3.15.4,pkg:packaging==24.1,pkg:platformdirs==4.2.2,pkg:pluggy==1.5.0,pkg:pyproject-api==1.7.1,pkg:virtualenv==20.26.3
#@check python -m pip show tox
if python3 -m pip install --break-system-packages --no-deps tox==4.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox==4.15.1" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:python-semantic-release  requires=pkg:click-option-group==0.5.6,pkg:click==8.1.7,pkg:dotty-dict==1.3.1,pkg:gitpython==3.1.43,pkg:importlib-resources==6.4.0,pkg:jinja2==3.1.4,pkg:pydantic==2.7.4,pkg:python-gitlab==4.7.0,pkg:requests==2.32.3,pkg:rich==13.7.1,pkg:shellingham==1.5.4,pkg:tomlkit==0.12.5
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:python-semantic-release" >> /tmp/v3_failed_nodes.log
fi
