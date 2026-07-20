#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 10 reciped (4 toolchain, 6 pip) + 0 needs
#   graph-hash: sha256:f862dbe274bf   python: 3.11   platform: aarch64-manylinux_2_28
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
#@node aptdep:libyaml-dev  provider=apt:libyaml-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libyaml-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libyaml-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libyaml-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pybuild-plugin-pyproject  provider=apt:pybuild-plugin-pyproject  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' pybuild-plugin-pyproject 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pybuild-plugin-pyproject
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pybuild-plugin-pyproject" >> /tmp/v3_failed_nodes.log
fi
#@node linker:yaml  provider=apt:libyaml-dev  requires=-  toolchain
#@check find /usr/lib /lib -name libyaml.so 2>/dev/null | grep -q .
if apt-get install -y --no-install-recommends libyaml-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED linker:yaml" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.1.1  version=9.1.1  requires=pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==83.0.0  version=83.0.0  requires=-
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==83.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==83.0.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:pyyaml  requires=aptdep:libyaml-dev,aptdep:pybuild-plugin-pyproject,linker:yaml,tool:build-essential
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:pyyaml" >> /tmp/v3_failed_nodes.log
fi
