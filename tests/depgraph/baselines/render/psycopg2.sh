#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 7 reciped (5 toolchain, 2 pip) + 0 needs
#   graph-hash: sha256:cecc6f89efea   python: 3.11   platform: aarch64-manylinux_2_28
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:psycopg2==2.9.12  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libpq-dev  provider=apt:libpq-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libpq-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libpq-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libpq-dev" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pg_config  provider=apt:libpq-dev  requires=-  unblocks=pkg:psycopg2==2.9.12  toolchain  evidence=Error: pg_config executable not found.
#@check command -v pg_config
if apt-get install -y --no-install-recommends libpq-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pg_config" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:psycopg2==2.9.12  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:postgresql  provider=apt:postgresql  requires=-  unblocks=pkg:psycopg2==2.9.12  toolchain
#@check dpkg-query -W -f='${Status}' postgresql 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends postgresql
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:postgresql" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:psycopg2==2.9.12  version=2.9.12  requires=aptdep:postgresql,binary:pg_config,binary:pkg-config,tool:build-essential  build-from-source  evidence=WARNING: Package(s) not found: psycopg2
#@check python -m pip show psycopg2
if python3 -m pip install --break-system-packages --no-deps psycopg2==2.9.12
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:psycopg2==2.9.12" >> /tmp/v3_failed_nodes.log
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
#@node project:psycopg2  requires=aptdep:libpq-dev,aptdep:postgresql,binary:pg_config,tool:build-essential
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:psycopg2" >> /tmp/v3_failed_nodes.log
fi
