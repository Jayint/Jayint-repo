#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 11 reciped (5 toolchain, 6 pip) + 0 needs
#   graph-hash: sha256:5167a84b3d7d   python: 3.11   platform: aarch64-manylinux_2_28
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:beautifulsoup==3.2.2  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libxml2-dev  provider=apt:libxml2-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libxml2-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libxml2-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libxml2-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libxslt1-dev  provider=apt:libxslt1-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libxslt1-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libxslt1-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libxslt1-dev" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:beautifulsoup==3.2.2  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:zlib1g-dev  provider=apt:zlib1g-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' zlib1g-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends zlib1g-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:zlib1g-dev" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:beautifulsoup==3.2.2  version=3.2.2  requires=binary:pkg-config,tool:build-essential  build-from-source  evidence=WARNING: Package(s) not found: beautifulsoup
#@check python -m pip show beautifulsoup
if python3 -m pip install --break-system-packages --no-deps beautifulsoup==3.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:beautifulsoup==3.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cython==3.2.8  version=3.2.8  requires=-
#@check python -m pip show cython
if python3 -m pip install --break-system-packages --no-deps cython==3.2.8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cython==3.2.8" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==83.0.0  version=83.0.0  requires=-
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==83.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==83.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:six==1.17.0  version=1.17.0  requires=-  unblocks=pkg:html5lib==1.1
#@check python -m pip show six
if python3 -m pip install --break-system-packages --no-deps six==1.17.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:six==1.17.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:webencodings==0.5.1  version=0.5.1  requires=-  unblocks=pkg:html5lib==1.1
#@check python -m pip show webencodings
if python3 -m pip install --break-system-packages --no-deps webencodings==0.5.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:webencodings==0.5.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:html5lib==1.1  version=1.1  requires=pkg:six==1.17.0,pkg:webencodings==0.5.1
#@check python -m pip show html5lib
if python3 -m pip install --break-system-packages --no-deps html5lib==1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:html5lib==1.1" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:lxml  requires=aptdep:libxml2-dev,aptdep:libxslt1-dev,aptdep:zlib1g-dev,pkg:cython==3.2.8,tool:build-essential
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:lxml" >> /tmp/v3_failed_nodes.log
fi
