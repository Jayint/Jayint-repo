#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 69 reciped (5 toolchain, 64 pip) + 0 needs
#   graph-hash: sha256:c1365167ecf4   python: 3.11   platform: aarch64-manylinux_2_28
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:cmake  provider=apt:cmake  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' cmake 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends cmake
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:cmake" >> /tmp/v3_failed_nodes.log
fi
#@node binary:git  provider=apt:git  requires=-  toolchain  evidence=tools/test_sdist.py:33  p = run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)
#@check command -v git
if apt-get install -y --no-install-recommends git
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:git" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libzmq3-dev  provider=apt:libzmq3-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libzmq3-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libzmq3-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libzmq3-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pybuild-plugin-pyproject  provider=apt:pybuild-plugin-pyproject  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' pybuild-plugin-pyproject 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pybuild-plugin-pyproject
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pybuild-plugin-pyproject" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:abi3info==2025.11.29  version=2025.11.29  requires=-  unblocks=pkg:abi3audit==0.0.26
#@check python -m pip show abi3info
if python3 -m pip install --break-system-packages --no-deps abi3info==2025.11.29
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:abi3info==2025.11.29" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ast-serialize==0.6.0  version=0.6.0  requires=-  unblocks=pkg:mypy==2.3.0
#@check python -m pip show ast-serialize
if python3 -m pip install --break-system-packages --no-deps ast-serialize==0.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ast-serialize==0.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:attrs==26.1.0  version=26.1.0  requires=-  unblocks=pkg:cattrs==26.1.0,pkg:requests-cache==1.3.3
#@check python -m pip show attrs
if python3 -m pip install --break-system-packages --no-deps attrs==26.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attrs==26.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:bashlex==0.18  version=0.18  requires=-  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show bashlex
if python3 -m pip install --break-system-packages --no-deps bashlex==0.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:bashlex==0.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:bracex==3.0  version=3.0  requires=-  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show bracex
if python3 -m pip install --break-system-packages --no-deps bracex==3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:bracex==3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2026.6.17  version=2026.6.17  requires=-  unblocks=pkg:cibuildwheel==3.1.4,pkg:requests==2.34.2
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2026.6.17
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2026.6.17" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.4.9  version=3.4.9  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.4.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.4.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.4.2  version=8.4.2  requires=-  unblocks=pkg:black==26.5.1
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.15.1  version=7.15.1  requires=-  unblocks=pkg:codecov==2.1.13,pkg:pytest-cov==2.10.1
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.15.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cython==3.2.8  version=3.2.8  requires=-
#@check python -m pip show cython
if python3 -m pip install --break-system-packages --no-deps cython==3.2.8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cython==3.2.8" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:dnspython==2.8.0  version=2.8.0  requires=-  unblocks=pkg:pymongo==4.17.0
#@check python -m pip show dnspython
if python3 -m pip install --break-system-packages --no-deps dnspython==2.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:dnspython==2.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.7  version=3.29.7  requires=-  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:greenlet==3.5.3  version=3.5.3  requires=-  unblocks=pkg:gevent==26.5.0
#@check python -m pip show greenlet
if python3 -m pip install --break-system-packages --no-deps greenlet==3.5.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:greenlet==3.5.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:humanize==4.16.0  version=4.16.0  requires=-  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show humanize
if python3 -m pip install --break-system-packages --no-deps humanize==4.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:humanize==4.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.18  version=3.18  requires=-  unblocks=pkg:requests==2.34.2,pkg:url-normalize==3.0.0
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:kaitaistruct==0.11  version=0.11  requires=-  unblocks=pkg:abi3audit==0.0.26
#@check python -m pip show kaitaistruct
if python3 -m pip install --break-system-packages --no-deps kaitaistruct==0.11
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:kaitaistruct==0.11" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:librt==0.13.0  version=0.13.0  requires=-  unblocks=pkg:mypy==2.3.0
#@check python -m pip show librt
if python3 -m pip install --break-system-packages --no-deps librt==0.13.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:librt==0.13.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mccabe==0.7.0  version=0.7.0  requires=-  unblocks=pkg:flake8==7.3.0
#@check python -m pip show mccabe
if python3 -m pip install --break-system-packages --no-deps mccabe==0.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mccabe==0.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mdurl==0.1.2  version=0.1.2  requires=-  unblocks=pkg:markdown-it-py==4.2.0
#@check python -m pip show mdurl
if python3 -m pip install --break-system-packages --no-deps mdurl==0.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mdurl==0.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown-it-py==4.2.0  version=4.2.0  requires=pkg:mdurl==0.1.2  unblocks=pkg:rich==15.0.0
#@check python -m pip show markdown-it-py
if python3 -m pip install --break-system-packages --no-deps markdown-it-py==4.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown-it-py==4.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.1.0  version=1.1.0  requires=-  unblocks=pkg:black==26.5.1,pkg:mypy==2.3.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:numpy==2.4.6  version=2.4.6  requires=-  unblocks=pkg:pandas==3.0.3
#@check python -m pip show numpy
if python3 -m pip install --break-system-packages --no-deps numpy==2.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:numpy==2.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:abi3audit==0.0.26,pkg:black==26.5.1,pkg:build==1.5.1,pkg:cibuildwheel==3.1.4,pkg:dependency-groups==1.3.1,pkg:pytest-rerunfailures==16.4,pkg:pytest==9.1.1,pkg:wheel==0.47.0
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:dependency-groups==1.3.1  version=1.3.1  requires=pkg:packaging==26.2  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show dependency-groups
if python3 -m pip install --break-system-packages --no-deps dependency-groups==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:dependency-groups==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:patchelf==0.17.2.4  version=0.17.2.4  requires=-  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show patchelf
if python3 -m pip install --break-system-packages --no-deps patchelf==0.17.2.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:patchelf==0.17.2.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pathspec==1.1.1  version=1.1.1  requires=-  unblocks=pkg:black==26.5.1,pkg:mypy==2.3.0
#@check python -m pip show pathspec
if python3 -m pip install --break-system-packages --no-deps pathspec==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pathspec==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pefile==2024.8.26  version=2024.8.26  requires=-  unblocks=pkg:abi3audit==0.0.26
#@check python -m pip show pefile
if python3 -m pip install --break-system-packages --no-deps pefile==2024.8.26
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pefile==2024.8.26" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=-  unblocks=pkg:black==26.5.1,pkg:cibuildwheel==3.1.4,pkg:requests-cache==1.3.3
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycodestyle==2.14.0  version=2.14.0  requires=-  unblocks=pkg:flake8==7.3.0
#@check python -m pip show pycodestyle
if python3 -m pip install --break-system-packages --no-deps pycodestyle==2.14.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycodestyle==2.14.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyelftools==0.33  version=0.33  requires=-  unblocks=pkg:abi3audit==0.0.26,pkg:cibuildwheel==3.1.4
#@check python -m pip show pyelftools
if python3 -m pip install --break-system-packages --no-deps pyelftools==0.33
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyelftools==0.33" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyflakes==3.4.0  version=3.4.0  requires=-  unblocks=pkg:flake8==7.3.0
#@check python -m pip show pyflakes
if python3 -m pip install --break-system-packages --no-deps pyflakes==3.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyflakes==3.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:flake8==7.3.0  version=7.3.0  requires=pkg:mccabe==0.7.0,pkg:pycodestyle==2.14.0,pkg:pyflakes==3.4.0
#@check python -m pip show flake8
if python3 -m pip install --break-system-packages --no-deps flake8==7.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:flake8==7.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:pytest==9.1.1,pkg:rich==15.0.0
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pymongo==4.17.0  version=4.17.0  requires=pkg:dnspython==2.8.0
#@check python -m pip show pymongo
if python3 -m pip install --break-system-packages --no-deps pymongo==4.17.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pymongo==4.17.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=-  unblocks=pkg:build==1.5.1
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:build==1.5.1  version=1.5.1  requires=pkg:packaging==26.2,pkg:pyproject-hooks==1.2.0  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show build
if python3 -m pip install --break-system-packages --no-deps build==1.5.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:build==1.5.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.1.1  version=9.1.1  requires=pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0  unblocks=pkg:pytest-asyncio==1.4.0,pkg:pytest-cov==2.10.1,pkg:pytest-rerunfailures==16.4
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==2.10.1  version=2.10.1  requires=pkg:coverage==7.15.1,pkg:pytest==9.1.1
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==2.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==2.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-rerunfailures==16.4  version=16.4  requires=pkg:packaging==26.2,pkg:pytest==9.1.1
#@check python -m pip show pytest-rerunfailures
if python3 -m pip install --break-system-packages --no-deps pytest-rerunfailures==16.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-rerunfailures==16.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytokens==0.4.1  version=0.4.1  requires=-  unblocks=pkg:black==26.5.1
#@check python -m pip show pytokens
if python3 -m pip install --break-system-packages --no-deps pytokens==0.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytokens==0.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:black==26.5.1  version=26.5.1  requires=pkg:click==8.4.2,pkg:mypy-extensions==1.1.0,pkg:packaging==26.2,pkg:pathspec==1.1.1,pkg:platformdirs==4.10.0,pkg:pytokens==0.4.1
#@check python -m pip show black
if python3 -m pip install --break-system-packages --no-deps black==26.5.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:black==26.5.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rich==15.0.0  version=15.0.0  requires=pkg:markdown-it-py==4.2.0,pkg:pygments==2.20.0  unblocks=pkg:abi3audit==0.0.26
#@check python -m pip show rich
if python3 -m pip install --break-system-packages --no-deps rich==15.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rich==15.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==83.0.0  version=83.0.0  requires=-
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==83.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==83.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:six==1.17.0  version=1.17.0  requires=-  unblocks=pkg:python-dateutil==2.9.0.post0
#@check python -m pip show six
if python3 -m pip install --break-system-packages --no-deps six==1.17.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:six==1.17.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-dateutil==2.9.0.post0  version=2.9.0.post0  requires=pkg:six==1.17.0  unblocks=pkg:pandas==3.0.3
#@check python -m pip show python-dateutil
if python3 -m pip install --break-system-packages --no-deps python-dateutil==2.9.0.post0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-dateutil==2.9.0.post0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pandas==3.0.3  version=3.0.3  requires=pkg:numpy==2.4.6,pkg:python-dateutil==2.9.0.post0
#@check python -m pip show pandas
if python3 -m pip install --break-system-packages --no-deps pandas==3.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pandas==3.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tornado==6.5.7  version=6.5.7  requires=-
#@check python -m pip show tornado
if python3 -m pip install --break-system-packages --no-deps tornado==6.5.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tornado==6.5.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.16.0  version=4.16.0  requires=-  unblocks=pkg:cattrs==26.1.0,pkg:mypy==2.3.0,pkg:pytest-asyncio==1.4.0
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cattrs==26.1.0  version=26.1.0  requires=pkg:attrs==26.1.0,pkg:typing-extensions==4.16.0  unblocks=pkg:requests-cache==1.3.3
#@check python -m pip show cattrs
if python3 -m pip install --break-system-packages --no-deps cattrs==26.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cattrs==26.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==2.3.0  version=2.3.0  requires=pkg:ast-serialize==0.6.0,pkg:librt==0.13.0,pkg:mypy-extensions==1.1.0,pkg:pathspec==1.1.1,pkg:typing-extensions==4.16.0
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-asyncio==1.4.0  version=1.4.0  requires=pkg:pytest==9.1.1,pkg:typing-extensions==4.16.0
#@check python -m pip show pytest-asyncio
if python3 -m pip install --break-system-packages --no-deps pytest-asyncio==1.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-asyncio==1.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:url-normalize==3.0.0  version=3.0.0  requires=pkg:idna==3.18  unblocks=pkg:requests-cache==1.3.3
#@check python -m pip show url-normalize
if python3 -m pip install --break-system-packages --no-deps url-normalize==3.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:url-normalize==3.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=-  unblocks=pkg:requests-cache==1.3.3,pkg:requests==2.34.2
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=pkg:certifi==2026.6.17,pkg:charset-normalizer==3.4.9,pkg:idna==3.18,pkg:urllib3==2.7.0  unblocks=pkg:abi3audit==0.0.26,pkg:codecov==2.1.13,pkg:requests-cache==1.3.3
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.34.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.34.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:codecov==2.1.13  version=2.1.13  requires=pkg:coverage==7.15.1,pkg:requests==2.34.2
#@check python -m pip show codecov
if python3 -m pip install --break-system-packages --no-deps codecov==2.1.13
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:codecov==2.1.13" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests-cache==1.3.3  version=1.3.3  requires=pkg:attrs==26.1.0,pkg:cattrs==26.1.0,pkg:platformdirs==4.10.0,pkg:requests==2.34.2,pkg:url-normalize==3.0.0,pkg:urllib3==2.7.0  unblocks=pkg:abi3audit==0.0.26
#@check python -m pip show requests-cache
if python3 -m pip install --break-system-packages --no-deps requests-cache==1.3.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests-cache==1.3.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:abi3audit==0.0.26  version=0.0.26  requires=pkg:abi3info==2025.11.29,pkg:kaitaistruct==0.11,pkg:packaging==26.2,pkg:pefile==2024.8.26,pkg:pyelftools==0.33,pkg:requests-cache==1.3.3,pkg:requests==2.34.2,pkg:rich==15.0.0
#@check python -m pip show abi3audit
if python3 -m pip install --break-system-packages --no-deps abi3audit==0.0.26
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:abi3audit==0.0.26" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wheel==0.47.0  version=0.47.0  requires=pkg:packaging==26.2  unblocks=pkg:cibuildwheel==3.1.4
#@check python -m pip show wheel
if python3 -m pip install --break-system-packages --no-deps wheel==0.47.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wheel==0.47.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cibuildwheel==3.1.4  version=3.1.4  requires=pkg:bashlex==0.18,pkg:bracex==3.0,pkg:build==1.5.1,pkg:certifi==2026.6.17,pkg:dependency-groups==1.3.1,pkg:filelock==3.29.7,pkg:humanize==4.16.0,pkg:packaging==26.2,pkg:patchelf==0.17.2.4,pkg:platformdirs==4.10.0,pkg:pyelftools==0.33,pkg:wheel==0.47.0
#@check python -m pip show cibuildwheel
if python3 -m pip install --break-system-packages --no-deps cibuildwheel==3.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cibuildwheel==3.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:zope-event==6.2  version=6.2  requires=-  unblocks=pkg:gevent==26.5.0
#@check python -m pip show zope-event
if python3 -m pip install --break-system-packages --no-deps zope-event==6.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:zope-event==6.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:zope-interface==8.5  version=8.5  requires=-  unblocks=pkg:gevent==26.5.0
#@check python -m pip show zope-interface
if python3 -m pip install --break-system-packages --no-deps zope-interface==8.5
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:zope-interface==8.5" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:gevent==26.5.0  version=26.5.0  requires=pkg:greenlet==3.5.3,pkg:zope-event==6.2,pkg:zope-interface==8.5
#@check python -m pip show gevent
if python3 -m pip install --break-system-packages --no-deps gevent==26.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:gevent==26.5.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:pyzmq  requires=aptdep:cmake,aptdep:libzmq3-dev,aptdep:pybuild-plugin-pyproject,binary:git,pkg:abi3audit==0.0.26,pkg:black==26.5.1,pkg:cibuildwheel==3.1.4,pkg:codecov==2.1.13,pkg:coverage==7.15.1,pkg:cython==3.2.8,pkg:flake8==7.3.0,pkg:gevent==26.5.0,pkg:mypy==2.3.0,pkg:pygments==2.20.0,pkg:pymongo==4.17.0,pkg:pytest-asyncio==1.4.0,pkg:pytest-cov==2.10.1,pkg:pytest-rerunfailures==16.4,pkg:pytest==9.1.1,pkg:setuptools==83.0.0,pkg:tornado==6.5.7,tool:build-essential
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:pyzmq" >> /tmp/v3_failed_nodes.log
fi
