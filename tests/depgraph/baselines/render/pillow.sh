#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 84 reciped (11 toolchain, 73 pip) + 0 needs
#   graph-hash: sha256:3cd38b16433a   python: 3.11   platform: aarch64-manylinux_2_28   exclude-newer: 2026-06-08
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
#@node aptdep:libfreetype6-dev  provider=apt:libfreetype-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libfreetype6-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libfreetype-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libfreetype6-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libimagequant-dev  provider=apt:libimagequant-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libimagequant-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libimagequant-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libimagequant-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libjpeg-dev  provider=apt:libjpeg-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libjpeg-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libjpeg-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libjpeg-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:liblcms2-dev  provider=apt:liblcms2-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' liblcms2-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends liblcms2-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:liblcms2-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libopenjp2-7-dev  provider=apt:libopenjp2-7-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libopenjp2-7-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libopenjp2-7-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libopenjp2-7-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libraqm-dev  provider=apt:libraqm-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libraqm-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libraqm-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libraqm-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libtiff5-dev  provider=apt:libtiff5-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libtiff5-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libtiff5-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libtiff5-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libwebp-dev  provider=apt:libwebp-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libwebp-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libwebp-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libwebp-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:tk-dev  provider=apt:tk-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' tk-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends tk-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:tk-dev" >> /tmp/v3_failed_nodes.log
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
#@node pkg:ast-serialize==0.5.0  version=0.5.0  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show ast-serialize
if python3 -m pip install --break-system-packages --no-deps ast-serialize==0.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ast-serialize==0.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:asttokens==3.0.1  version=3.0.1  requires=-  unblocks=pkg:stack-data==0.6.3
#@check python -m pip show asttokens
if python3 -m pip install --break-system-packages --no-deps asttokens==3.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:asttokens==3.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:attrs==26.1.0  version=26.1.0  requires=-  unblocks=pkg:jsonschema==4.26.0,pkg:referencing==0.37.0
#@check python -m pip show attrs
if python3 -m pip install --break-system-packages --no-deps attrs==26.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attrs==26.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:bashlex==0.18  version=0.18  requires=-  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show bashlex
if python3 -m pip install --break-system-packages --no-deps bashlex==0.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:bashlex==0.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:bracex==2.6  version=2.6  requires=-  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show bracex
if python3 -m pip install --break-system-packages --no-deps bracex==2.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:bracex==2.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2026.5.20  version=2026.5.20  requires=-  unblocks=pkg:cibuildwheel==4.0.0,pkg:requests==2.34.2
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2026.5.20
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2026.5.20" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.4.7  version=3.4.7  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.4.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.4.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.4.1  version=8.4.1  requires=-  unblocks=pkg:check-jsonschema==0.37.2
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.14.1  version=7.14.1  requires=-  unblocks=pkg:pytest-cov==7.1.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.14.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.14.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:decorator==5.3.1  version=5.3.1  requires=-  unblocks=pkg:ipython==9.14.1
#@check python -m pip show decorator
if python3 -m pip install --break-system-packages --no-deps decorator==5.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:decorator==5.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:defusedxml==0.7.1  version=0.7.1  requires=-
#@check python -m pip show defusedxml
if python3 -m pip install --break-system-packages --no-deps defusedxml==0.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:defusedxml==0.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:execnet==2.1.2  version=2.1.2  requires=-  unblocks=pkg:pytest-xdist==3.8.0
#@check python -m pip show execnet
if python3 -m pip install --break-system-packages --no-deps execnet==2.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:execnet==2.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:executing==2.2.1  version=2.2.1  requires=-  unblocks=pkg:stack-data==0.6.3
#@check python -m pip show executing
if python3 -m pip install --break-system-packages --no-deps executing==2.2.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:executing==2.2.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.1  version=3.29.1  requires=-  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:humanize==4.15.0  version=4.15.0  requires=-  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show humanize
if python3 -m pip install --break-system-packages --no-deps humanize==4.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:humanize==4.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:icespringpysidestubs-pyqt6==1.3.1  version=1.3.1  requires=-
#@check python -m pip show icespringpysidestubs-pyqt6
if python3 -m pip install --break-system-packages --no-deps icespringpysidestubs-pyqt6==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:icespringpysidestubs-pyqt6==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:icespringpysidestubs-pyside6==1.3.1  version=1.3.1  requires=-
#@check python -m pip show icespringpysidestubs-pyside6
if python3 -m pip install --break-system-packages --no-deps icespringpysidestubs-pyside6==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:icespringpysidestubs-pyside6==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.18  version=3.18  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=-  unblocks=pkg:pytest==9.0.3
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:librt==0.11.0  version=0.11.0  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show librt
if python3 -m pip install --break-system-packages --no-deps librt==0.11.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:librt==0.11.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown2==2.5.5  version=2.5.5  requires=-
#@check python -m pip show markdown2
if python3 -m pip install --break-system-packages --no-deps markdown2==2.5.5
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown2==2.5.5" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.1.0  version=1.1.0  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:numpy==2.4.6  version=2.4.6  requires=-
#@check python -m pip show numpy
if python3 -m pip install --break-system-packages --no-deps numpy==2.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:numpy==2.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:olefile==0.47  version=0.47  requires=-
#@check python -m pip show olefile
if python3 -m pip install --break-system-packages --no-deps olefile==0.47
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:olefile==0.47" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:build==1.5.0,pkg:cibuildwheel==4.0.0,pkg:dependency-groups==1.3.1,pkg:pytest==9.0.3
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:dependency-groups==1.3.1  version=1.3.1  requires=pkg:packaging==26.2  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show dependency-groups
if python3 -m pip install --break-system-packages --no-deps dependency-groups==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:dependency-groups==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:parso==0.8.7  version=0.8.7  requires=-  unblocks=pkg:jedi==0.20.0
#@check python -m pip show parso
if python3 -m pip install --break-system-packages --no-deps parso==0.8.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:parso==0.8.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jedi==0.20.0  version=0.20.0  requires=pkg:parso==0.8.7  unblocks=pkg:ipython==9.14.1
#@check python -m pip show jedi
if python3 -m pip install --break-system-packages --no-deps jedi==0.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jedi==0.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pathspec==1.1.1  version=1.1.1  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show pathspec
if python3 -m pip install --break-system-packages --no-deps pathspec==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pathspec==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=-  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest-cov==7.1.0,pkg:pytest==9.0.3
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:psutil==7.2.2  version=7.2.2  requires=-  unblocks=pkg:ipython==9.14.1
#@check python -m pip show psutil
if python3 -m pip install --break-system-packages --no-deps psutil==7.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:psutil==7.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ptyprocess==0.7.0  version=0.7.0  requires=-  unblocks=pkg:pexpect==4.9.0
#@check python -m pip show ptyprocess
if python3 -m pip install --break-system-packages --no-deps ptyprocess==0.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ptyprocess==0.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pexpect==4.9.0  version=4.9.0  requires=pkg:ptyprocess==0.7.0  unblocks=pkg:ipython==9.14.1
#@check python -m pip show pexpect
if python3 -m pip install --break-system-packages --no-deps pexpect==4.9.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pexpect==4.9.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pure-eval==0.2.3  version=0.2.3  requires=-  unblocks=pkg:stack-data==0.6.3
#@check python -m pip show pure-eval
if python3 -m pip install --break-system-packages --no-deps pure-eval==0.2.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pure-eval==0.2.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyarrow==24.0.0  version=24.0.0  requires=-  unblocks=pkg:pyarrow-stubs==20.0.0.20251215
#@check python -m pip show pyarrow
if python3 -m pip install --break-system-packages --no-deps pyarrow==24.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyarrow==24.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyarrow-stubs==20.0.0.20251215  version=20.0.0.20251215  requires=pkg:pyarrow==24.0.0
#@check python -m pip show pyarrow-stubs
if python3 -m pip install --break-system-packages --no-deps pyarrow-stubs==20.0.0.20251215
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyarrow-stubs==20.0.0.20251215" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pybind11==3.0.4  version=3.0.4  requires=-
#@check python -m pip show pybind11
if python3 -m pip install --break-system-packages --no-deps pybind11==3.0.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pybind11==3.0.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:ipython-pygments-lexers==1.1.1,pkg:ipython==9.14.1,pkg:pytest==9.0.3
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ipython-pygments-lexers==1.1.1  version=1.1.1  requires=pkg:pygments==2.20.0  unblocks=pkg:ipython==9.14.1
#@check python -m pip show ipython-pygments-lexers
if python3 -m pip install --break-system-packages --no-deps ipython-pygments-lexers==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ipython-pygments-lexers==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=-  unblocks=pkg:build==1.5.0
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:build==1.5.0  version=1.5.0  requires=pkg:packaging==26.2,pkg:pyproject-hooks==1.2.0  unblocks=pkg:cibuildwheel==4.0.0
#@check python -m pip show build
if python3 -m pip install --break-system-packages --no-deps build==1.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:build==1.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cibuildwheel==4.0.0  version=4.0.0  requires=pkg:bashlex==0.18,pkg:bracex==2.6,pkg:build==1.5.0,pkg:certifi==2026.5.20,pkg:dependency-groups==1.3.1,pkg:filelock==3.29.1,pkg:humanize==4.15.0,pkg:packaging==26.2,pkg:platformdirs==4.10.0
#@check python -m pip show cibuildwheel
if python3 -m pip install --break-system-packages --no-deps cibuildwheel==4.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cibuildwheel==4.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.0.3  version=9.0.3  requires=pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0  unblocks=pkg:pytest-cov==7.1.0,pkg:pytest-timeout==2.4.0,pkg:pytest-xdist==3.8.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==7.1.0  version=7.1.0  requires=pkg:coverage==7.14.1,pkg:pluggy==1.6.0,pkg:pytest==9.0.3
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==7.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==7.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-timeout==2.4.0  version=2.4.0  requires=pkg:pytest==9.0.3
#@check python -m pip show pytest-timeout
if python3 -m pip install --break-system-packages --no-deps pytest-timeout==2.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-timeout==2.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-xdist==3.8.0  version=3.8.0  requires=pkg:execnet==2.1.2,pkg:pytest==9.0.3
#@check python -m pip show pytest-xdist
if python3 -m pip install --break-system-packages --no-deps pytest-xdist==3.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-xdist==3.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:regress==2025.10.1  version=2025.10.1  requires=-  unblocks=pkg:check-jsonschema==0.37.2
#@check python -m pip show regress
if python3 -m pip install --break-system-packages --no-deps regress==2025.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:regress==2025.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rpds-py==2026.5.1  version=2026.5.1  requires=-  unblocks=pkg:jsonschema==4.26.0,pkg:referencing==0.37.0
#@check python -m pip show rpds-py
if python3 -m pip install --break-system-packages --no-deps rpds-py==2026.5.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rpds-py==2026.5.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruamel-yaml==0.19.1  version=0.19.1  requires=-  unblocks=pkg:check-jsonschema==0.37.2
#@check python -m pip show ruamel-yaml
if python3 -m pip install --break-system-packages --no-deps ruamel-yaml==0.19.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruamel-yaml==0.19.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==82.0.1  version=82.0.1  requires=-
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==82.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==82.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:stack-data==0.6.3  version=0.6.3  requires=pkg:asttokens==3.0.1,pkg:executing==2.2.1,pkg:pure-eval==0.2.3  unblocks=pkg:ipython==9.14.1
#@check python -m pip show stack-data
if python3 -m pip install --break-system-packages --no-deps stack-data==0.6.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:stack-data==0.6.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.4.1  version=2.4.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:traitlets==5.15.1  version=5.15.1  requires=-  unblocks=pkg:ipython==9.14.1,pkg:matplotlib-inline==0.2.2
#@check python -m pip show traitlets
if python3 -m pip install --break-system-packages --no-deps traitlets==5.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:traitlets==5.15.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:matplotlib-inline==0.2.2  version=0.2.2  requires=pkg:traitlets==5.15.1  unblocks=pkg:ipython==9.14.1
#@check python -m pip show matplotlib-inline
if python3 -m pip install --break-system-packages --no-deps matplotlib-inline==0.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:matplotlib-inline==0.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:trove-classifiers==2026.6.1.19  version=2026.6.1.19  requires=-
#@check python -m pip show trove-classifiers
if python3 -m pip install --break-system-packages --no-deps trove-classifiers==2026.6.1.19
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:trove-classifiers==2026.6.1.19" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-atheris==3.0.0.20260518  version=3.0.0.20260518  requires=-
#@check python -m pip show types-atheris
if python3 -m pip install --break-system-packages --no-deps types-atheris==3.0.0.20260518
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-atheris==3.0.0.20260518" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-defusedxml==0.7.0.20260504  version=0.7.0.20260504  requires=-
#@check python -m pip show types-defusedxml
if python3 -m pip install --break-system-packages --no-deps types-defusedxml==0.7.0.20260504
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-defusedxml==0.7.0.20260504" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-olefile==0.47.0.20260508  version=0.47.0.20260508  requires=-
#@check python -m pip show types-olefile
if python3 -m pip install --break-system-packages --no-deps types-olefile==0.47.0.20260508
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-olefile==0.47.0.20260508" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:types-setuptools==82.0.0.20260518  version=82.0.0.20260518  requires=-
#@check python -m pip show types-setuptools
if python3 -m pip install --break-system-packages --no-deps types-setuptools==82.0.0.20260518
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:types-setuptools==82.0.0.20260518" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.15.0  version=4.15.0  requires=-  unblocks=pkg:arro3-core==0.8.0,pkg:ipython==9.14.1,pkg:mypy==2.1.0,pkg:referencing==0.37.0
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:arro3-core==0.8.0  version=0.8.0  requires=pkg:typing-extensions==4.15.0  unblocks=pkg:arro3-compute==0.8.0
#@check python -m pip show arro3-core
if python3 -m pip install --break-system-packages --no-deps arro3-core==0.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:arro3-core==0.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:arro3-compute==0.8.0  version=0.8.0  requires=pkg:arro3-core==0.8.0
#@check python -m pip show arro3-compute
if python3 -m pip install --break-system-packages --no-deps arro3-compute==0.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:arro3-compute==0.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==2.1.0  version=2.1.0  requires=pkg:ast-serialize==0.5.0,pkg:librt==0.11.0,pkg:mypy-extensions==1.1.0,pkg:pathspec==1.1.1,pkg:typing-extensions==4.15.0
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:referencing==0.37.0  version=0.37.0  requires=pkg:attrs==26.1.0,pkg:rpds-py==2026.5.1,pkg:typing-extensions==4.15.0  unblocks=pkg:jsonschema-specifications==2025.9.1,pkg:jsonschema==4.26.0
#@check python -m pip show referencing
if python3 -m pip install --break-system-packages --no-deps referencing==0.37.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:referencing==0.37.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jsonschema-specifications==2025.9.1  version=2025.9.1  requires=pkg:referencing==0.37.0  unblocks=pkg:jsonschema==4.26.0
#@check python -m pip show jsonschema-specifications
if python3 -m pip install --break-system-packages --no-deps jsonschema-specifications==2025.9.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jsonschema-specifications==2025.9.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jsonschema==4.26.0  version=4.26.0  requires=pkg:attrs==26.1.0,pkg:jsonschema-specifications==2025.9.1,pkg:referencing==0.37.0,pkg:rpds-py==2026.5.1  unblocks=pkg:check-jsonschema==0.37.2
#@check python -m pip show jsonschema
if python3 -m pip install --break-system-packages --no-deps jsonschema==4.26.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jsonschema==4.26.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=pkg:certifi==2026.5.20,pkg:charset-normalizer==3.4.7,pkg:idna==3.18,pkg:urllib3==2.7.0  unblocks=pkg:check-jsonschema==0.37.2
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.34.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.34.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:check-jsonschema==0.37.2  version=0.37.2  requires=pkg:click==8.4.1,pkg:jsonschema==4.26.0,pkg:regress==2025.10.1,pkg:requests==2.34.2,pkg:ruamel-yaml==0.19.1
#@check python -m pip show check-jsonschema
if python3 -m pip install --break-system-packages --no-deps check-jsonschema==0.37.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:check-jsonschema==0.37.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wcwidth==0.8.1  version=0.8.1  requires=-  unblocks=pkg:prompt-toolkit==3.0.52
#@check python -m pip show wcwidth
if python3 -m pip install --break-system-packages --no-deps wcwidth==0.8.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wcwidth==0.8.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:prompt-toolkit==3.0.52  version=3.0.52  requires=pkg:wcwidth==0.8.1  unblocks=pkg:ipython==9.14.1
#@check python -m pip show prompt-toolkit
if python3 -m pip install --break-system-packages --no-deps prompt-toolkit==3.0.52
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:prompt-toolkit==3.0.52" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ipython==9.14.1  version=9.14.1  requires=pkg:decorator==5.3.1,pkg:ipython-pygments-lexers==1.1.1,pkg:jedi==0.20.0,pkg:matplotlib-inline==0.2.2,pkg:pexpect==4.9.0,pkg:prompt-toolkit==3.0.52,pkg:psutil==7.2.2,pkg:pygments==2.20.0,pkg:stack-data==0.6.3,pkg:traitlets==5.15.1,pkg:typing-extensions==4.15.0
#@check python -m pip show ipython
if python3 -m pip install --break-system-packages --no-deps ipython==9.14.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ipython==9.14.1" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:pillow  requires=aptdep:libfreetype6-dev,aptdep:libimagequant-dev,aptdep:libjpeg-dev,aptdep:liblcms2-dev,aptdep:libopenjp2-7-dev,aptdep:libraqm-dev,aptdep:libtiff5-dev,aptdep:libwebp-dev,aptdep:tk-dev,aptdep:zlib1g-dev,pkg:arro3-compute==0.8.0,pkg:arro3-core==0.8.0,pkg:check-jsonschema==0.37.2,pkg:cibuildwheel==4.0.0,pkg:icespringpysidestubs-pyqt6==1.3.1,pkg:icespringpysidestubs-pyside6==1.3.1,pkg:ipython==9.14.1,pkg:mypy==2.1.0,pkg:numpy==2.4.6,pkg:packaging==26.2,pkg:pyarrow-stubs==20.0.0.20251215,pkg:pybind11==3.0.4,pkg:pytest==9.0.3,pkg:types-atheris==3.0.0.20260518,pkg:types-defusedxml==0.7.0.20260504,pkg:types-olefile==0.47.0.20260508,pkg:types-setuptools==82.0.0.20260518,tool:build-essential
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:pillow" >> /tmp/v3_failed_nodes.log
fi
