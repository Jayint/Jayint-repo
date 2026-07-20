#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 68 reciped (1 toolchain, 67 pip) + 0 needs
#   graph-hash: sha256:427b1921d23b   python: 3.11   platform: aarch64-manylinux_2_28   exclude-newer: 2026-06-13
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node binary:git  provider=apt:git  requires=-  toolchain  evidence=release.py:49  run("git", "tag", "-s", version, "-m", f"{version} release")
#@check command -v git
if apt-get install -y --no-install-recommends git
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:git" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:alabaster==1.0.0  version=1.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show alabaster
if python3 -m pip install --break-system-packages --no-deps alabaster==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:alabaster==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:argcomplete==3.6.3  version=3.6.3  requires=-  unblocks=pkg:nox==2026.4.10
#@check python -m pip show argcomplete
if python3 -m pip install --break-system-packages --no-deps argcomplete==3.6.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:argcomplete==3.6.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ast-serialize==0.5.0  version=0.5.0  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show ast-serialize
if python3 -m pip install --break-system-packages --no-deps ast-serialize==0.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ast-serialize==0.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:attrs==26.1.0  version=26.1.0  requires=-  unblocks=pkg:nox==2026.4.10
#@check python -m pip show attrs
if python3 -m pip install --break-system-packages --no-deps attrs==26.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attrs==26.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:babel==2.18.0  version=2.18.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show babel
if python3 -m pip install --break-system-packages --no-deps babel==2.18.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:babel==2.18.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2026.5.20  version=2026.5.20  requires=-  unblocks=pkg:requests==2.34.2
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
#@node pkg:click==8.4.1  version=8.4.1  requires=-
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:colorlog==6.10.1  version=6.10.1  requires=-  unblocks=pkg:nox==2026.4.10
#@check python -m pip show colorlog
if python3 -m pip install --break-system-packages --no-deps colorlog==6.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorlog==6.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.14.1  version=7.14.1  requires=-  unblocks=pkg:pytest-cov==7.1.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.14.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.14.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cryptography-vectors==49.0.0  version=49.0.0  requires=-
#@check python -m pip show cryptography-vectors
if python3 -m pip install --break-system-packages --no-deps cryptography-vectors==49.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cryptography-vectors==49.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.4.3  version=0.4.3  requires=-  unblocks=pkg:virtualenv==21.4.3
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:docutils==0.22.4  version=0.22.4  requires=-  unblocks=pkg:readme-renderer==45.0,pkg:sphinx-rtd-theme==3.1.0,pkg:sphinx==9.0.4
#@check python -m pip show docutils
if python3 -m pip install --break-system-packages --no-deps docutils==0.22.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:docutils==0.22.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:execnet==2.1.2  version=2.1.2  requires=-  unblocks=pkg:pytest-xdist==3.8.0
#@check python -m pip show execnet
if python3 -m pip install --break-system-packages --no-deps execnet==2.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:execnet==2.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.3  version=3.29.3  requires=-  unblocks=pkg:python-discovery==1.4.2,pkg:virtualenv==21.4.3
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:humanize==4.15.0  version=4.15.0  requires=-  unblocks=pkg:nox==2026.4.10
#@check python -m pip show humanize
if python3 -m pip install --break-system-packages --no-deps humanize==4.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:humanize==4.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.18  version=3.18  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:imagesize==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show imagesize
if python3 -m pip install --break-system-packages --no-deps imagesize==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:imagesize==2.0.0" >> /tmp/v3_failed_nodes.log
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
#@node pkg:markupsafe==3.0.3  version=3.0.3  requires=-  unblocks=pkg:jinja2==3.1.6
#@check python -m pip show markupsafe
if python3 -m pip install --break-system-packages --no-deps markupsafe==3.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markupsafe==3.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jinja2==3.1.6  version=3.1.6  requires=pkg:markupsafe==3.0.3  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show jinja2
if python3 -m pip install --break-system-packages --no-deps jinja2==3.1.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jinja2==3.1.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.1.0  version=1.1.0  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nh3==0.3.5  version=0.3.5  requires=-  unblocks=pkg:readme-renderer==45.0
#@check python -m pip show nh3
if python3 -m pip install --break-system-packages --no-deps nh3==0.3.5
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nh3==0.3.5" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:build==1.5.0,pkg:dependency-groups==1.3.1,pkg:nox==2026.4.10,pkg:pytest==9.0.3,pkg:sphinx==9.0.4
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:dependency-groups==1.3.1  version=1.3.1  requires=pkg:packaging==26.2  unblocks=pkg:nox==2026.4.10
#@check python -m pip show dependency-groups
if python3 -m pip install --break-system-packages --no-deps dependency-groups==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:dependency-groups==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pathspec==1.1.1  version=1.1.1  requires=-  unblocks=pkg:check-sdist==1.4.0,pkg:mypy==2.1.0
#@check python -m pip show pathspec
if python3 -m pip install --break-system-packages --no-deps pathspec==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pathspec==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=-  unblocks=pkg:python-discovery==1.4.2,pkg:virtualenv==21.4.3
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
#@node pkg:pretend==1.0.9  version=1.0.9  requires=-
#@check python -m pip show pretend
if python3 -m pip install --break-system-packages --no-deps pretend==1.0.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pretend==1.0.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:py-cpuinfo==9.0.0  version=9.0.0  requires=-  unblocks=pkg:pytest-benchmark==5.2.3
#@check python -m pip show py-cpuinfo
if python3 -m pip install --break-system-packages --no-deps py-cpuinfo==9.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:py-cpuinfo==9.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycparser==3.0  version=3.0  requires=-  unblocks=pkg:cffi==2.0.0
#@check python -m pip show pycparser
if python3 -m pip install --break-system-packages --no-deps pycparser==3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycparser==3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cffi==2.0.0  version=2.0.0  requires=pkg:pycparser==3.0
#@check python -m pip show cffi
if python3 -m pip install --break-system-packages --no-deps cffi==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cffi==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyenchant==3.3.0  version=3.3.0  requires=-  unblocks=pkg:sphinxcontrib-spelling==8.0.2
#@check python -m pip show pyenchant
if python3 -m pip install --break-system-packages --no-deps pyenchant==3.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyenchant==3.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:pytest==9.0.3,pkg:readme-renderer==45.0,pkg:sphinx==9.0.4
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=-  unblocks=pkg:build==1.5.0
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:build==1.5.0  version=1.5.0  requires=pkg:packaging==26.2,pkg:pyproject-hooks==1.2.0  unblocks=pkg:check-sdist==1.4.0
#@check python -m pip show build
if python3 -m pip install --break-system-packages --no-deps build==1.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:build==1.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:check-sdist==1.4.0  version=1.4.0  requires=pkg:build==1.5.0,pkg:pathspec==1.1.1
#@check python -m pip show check-sdist
if python3 -m pip install --break-system-packages --no-deps check-sdist==1.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:check-sdist==1.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.0.3  version=9.0.3  requires=pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0  unblocks=pkg:pytest-benchmark==5.2.3,pkg:pytest-cov==7.1.0,pkg:pytest-randomly==4.1.0,pkg:pytest-xdist==3.8.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-benchmark==5.2.3  version=5.2.3  requires=pkg:py-cpuinfo==9.0.0,pkg:pytest==9.0.3
#@check python -m pip show pytest-benchmark
if python3 -m pip install --break-system-packages --no-deps pytest-benchmark==5.2.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-benchmark==5.2.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==7.1.0  version=7.1.0  requires=pkg:coverage==7.14.1,pkg:pluggy==1.6.0,pkg:pytest==9.0.3
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==7.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==7.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-randomly==4.1.0  version=4.1.0  requires=pkg:pytest==9.0.3
#@check python -m pip show pytest-randomly
if python3 -m pip install --break-system-packages --no-deps pytest-randomly==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-randomly==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-xdist==3.8.0  version=3.8.0  requires=pkg:execnet==2.1.2,pkg:pytest==9.0.3
#@check python -m pip show pytest-xdist
if python3 -m pip install --break-system-packages --no-deps pytest-xdist==3.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-xdist==3.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-discovery==1.4.2  version=1.4.2  requires=pkg:filelock==3.29.3,pkg:platformdirs==4.10.0  unblocks=pkg:virtualenv==21.4.3
#@check python -m pip show python-discovery
if python3 -m pip install --break-system-packages --no-deps python-discovery==1.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-discovery==1.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:readme-renderer==45.0  version=45.0  requires=pkg:docutils==0.22.4,pkg:nh3==0.3.5,pkg:pygments==2.20.0
#@check python -m pip show readme-renderer
if python3 -m pip install --break-system-packages --no-deps readme-renderer==45.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:readme-renderer==45.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:roman-numerals==4.1.0  version=4.1.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show roman-numerals
if python3 -m pip install --break-system-packages --no-deps roman-numerals==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:roman-numerals==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.15.17  version=0.15.17  requires=-
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.15.17
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.15.17" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:snowballstemmer==3.1.1  version=3.1.1  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show snowballstemmer
if python3 -m pip install --break-system-packages --no-deps snowballstemmer==3.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:snowballstemmer==3.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-applehelp==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-applehelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-applehelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-applehelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-devhelp==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-devhelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-devhelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-devhelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-htmlhelp==2.1.0  version=2.1.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-htmlhelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-htmlhelp==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-htmlhelp==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-jsmath==1.0.1  version=1.0.1  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-jsmath
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-jsmath==1.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-jsmath==1.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-qthelp==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-qthelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-qthelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-qthelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-serializinghtml==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-serializinghtml
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-serializinghtml==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-serializinghtml==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.4.1  version=2.4.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.15.0  version=4.15.0  requires=-  unblocks=pkg:mypy==2.1.0
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==2.1.0  version=2.1.0  requires=pkg:ast-serialize==0.5.0,pkg:librt==0.11.0,pkg:mypy-extensions==1.1.0,pkg:pathspec==1.1.1,pkg:typing-extensions==4.15.0
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=pkg:certifi==2026.5.20,pkg:charset-normalizer==3.4.7,pkg:idna==3.18,pkg:urllib3==2.7.0  unblocks=pkg:sphinx==9.0.4,pkg:sphinxcontrib-spelling==8.0.2
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.34.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.34.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx==9.0.4  version=9.0.4  requires=pkg:alabaster==1.0.0,pkg:babel==2.18.0,pkg:docutils==0.22.4,pkg:imagesize==2.0.0,pkg:jinja2==3.1.6,pkg:packaging==26.2,pkg:pygments==2.20.0,pkg:requests==2.34.2,pkg:roman-numerals==4.1.0,pkg:snowballstemmer==3.1.1,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0  unblocks=pkg:sphinx-inline-tabs==2025.12.21.14,pkg:sphinx-rtd-theme==3.1.0,pkg:sphinxcontrib-jquery==4.1,pkg:sphinxcontrib-spelling==8.0.2
#@check python -m pip show sphinx
if python3 -m pip install --break-system-packages --no-deps sphinx==9.0.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx==9.0.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx-inline-tabs==2025.12.21.14  version=2025.12.21.14  requires=pkg:sphinx==9.0.4
#@check python -m pip show sphinx-inline-tabs
if python3 -m pip install --break-system-packages --no-deps sphinx-inline-tabs==2025.12.21.14
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx-inline-tabs==2025.12.21.14" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-jquery==4.1  version=4.1  requires=pkg:sphinx==9.0.4  unblocks=pkg:sphinx-rtd-theme==3.1.0
#@check python -m pip show sphinxcontrib-jquery
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-jquery==4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-jquery==4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx-rtd-theme==3.1.0  version=3.1.0  requires=pkg:docutils==0.22.4,pkg:sphinx==9.0.4,pkg:sphinxcontrib-jquery==4.1
#@check python -m pip show sphinx-rtd-theme
if python3 -m pip install --break-system-packages --no-deps sphinx-rtd-theme==3.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx-rtd-theme==3.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-spelling==8.0.2  version=8.0.2  requires=pkg:pyenchant==3.3.0,pkg:requests==2.34.2,pkg:sphinx==9.0.4
#@check python -m pip show sphinxcontrib-spelling
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-spelling==8.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-spelling==8.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:uv==0.11.21  version=0.11.21  requires=-
#@check python -m pip show uv
if python3 -m pip install --break-system-packages --no-deps uv==0.11.21
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:uv==0.11.21" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==21.4.3  version=21.4.3  requires=pkg:distlib==0.4.3,pkg:filelock==3.29.3,pkg:platformdirs==4.10.0,pkg:python-discovery==1.4.2  unblocks=pkg:nox==2026.4.10
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==21.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==21.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nox==2026.4.10  version=2026.4.10  requires=pkg:argcomplete==3.6.3,pkg:attrs==26.1.0,pkg:colorlog==6.10.1,pkg:dependency-groups==1.3.1,pkg:humanize==4.15.0,pkg:packaging==26.2,pkg:virtualenv==21.4.3
#@check python -m pip show nox
if python3 -m pip install --break-system-packages --no-deps nox==2026.4.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nox==2026.4.10" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:cryptography  requires=binary:git,pkg:alabaster==1.0.0,pkg:argcomplete==3.6.3,pkg:ast-serialize==0.5.0,pkg:attrs==26.1.0,pkg:babel==2.18.0,pkg:build==1.5.0,pkg:certifi==2026.5.20,pkg:cffi==2.0.0,pkg:charset-normalizer==3.4.7,pkg:check-sdist==1.4.0,pkg:click==8.4.1,pkg:colorlog==6.10.1,pkg:coverage==7.14.1,pkg:cryptography-vectors==49.0.0,pkg:dependency-groups==1.3.1,pkg:distlib==0.4.3,pkg:docutils==0.22.4,pkg:execnet==2.1.2,pkg:filelock==3.29.3,pkg:humanize==4.15.0,pkg:idna==3.18,pkg:imagesize==2.0.0,pkg:iniconfig==2.3.0,pkg:jinja2==3.1.6,pkg:librt==0.11.0,pkg:markupsafe==3.0.3,pkg:mypy-extensions==1.1.0,pkg:mypy==2.1.0,pkg:nh3==0.3.5,pkg:nox==2026.4.10,pkg:packaging==26.2,pkg:pathspec==1.1.1,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pretend==1.0.9,pkg:py-cpuinfo==9.0.0,pkg:pyenchant==3.3.0,pkg:pygments==2.20.0,pkg:pyproject-hooks==1.2.0,pkg:pytest-benchmark==5.2.3,pkg:pytest-cov==7.1.0,pkg:pytest-randomly==4.1.0,pkg:pytest-xdist==3.8.0,pkg:pytest==9.0.3,pkg:python-discovery==1.4.2,pkg:readme-renderer==45.0,pkg:requests==2.34.2,pkg:roman-numerals==4.1.0,pkg:ruff==0.15.17,pkg:snowballstemmer==3.1.1,pkg:sphinx-inline-tabs==2025.12.21.14,pkg:sphinx-rtd-theme==3.1.0,pkg:sphinx==9.0.4,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jquery==4.1,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,pkg:sphinxcontrib-spelling==8.0.2,pkg:tomli==2.4.1,pkg:typing-extensions==4.15.0,pkg:urllib3==2.7.0,pkg:uv==0.11.21,pkg:virtualenv==21.4.3
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:cryptography" >> /tmp/v3_failed_nodes.log
fi
