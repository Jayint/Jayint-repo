#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 47 reciped (47 pip) + 0 needs
#   graph-hash: sha256:8996b5ad2c7b   python: 3.11   platform: aarch64-manylinux_2_28
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== PIP ====================
#@node pkg:asttokens==3.0.2  version=3.0.2  requires=-  unblocks=pkg:stack-data==0.6.3
#@check python -m pip show asttokens
if python3 -m pip install --break-system-packages --no-deps asttokens==3.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:asttokens==3.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:bump2version==1.0.1  version=1.0.1  requires=-  unblocks=pkg:bumpversion==0.6.0
#@check python -m pip show bump2version
if python3 -m pip install --break-system-packages --no-deps bump2version==1.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:bump2version==1.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:bumpversion==0.6.0  version=0.6.0  requires=pkg:bump2version==1.0.1
#@check python -m pip show bumpversion
if python3 -m pip install --break-system-packages --no-deps bumpversion==0.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:bumpversion==0.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cachetools==7.1.4  version=7.1.4  requires=-  unblocks=pkg:tox==4.56.4
#@check python -m pip show cachetools
if python3 -m pip install --break-system-packages --no-deps cachetools==7.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cachetools==7.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cfgv==3.5.0  version=3.5.0  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show cfgv
if python3 -m pip install --break-system-packages --no-deps cfgv==3.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cfgv==3.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:colorama==0.4.6  version=0.4.6  requires=-  unblocks=pkg:build==1.5.1,pkg:click==8.4.2,pkg:ipython==9.15.0,pkg:pytest==9.1.1,pkg:tox==4.56.4
#@check python -m pip show colorama
if python3 -m pip install --break-system-packages --no-deps colorama==0.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorama==0.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.4.2  version=8.4.2  requires=pkg:colorama==0.4.6
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.15.1  version=7.15.1  requires=-  unblocks=pkg:pytest-cov==7.1.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.15.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:decorator==5.3.1  version=5.3.1  requires=-  unblocks=pkg:ipython==9.15.0
#@check python -m pip show decorator
if python3 -m pip install --break-system-packages --no-deps decorator==5.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:decorator==5.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.4.3  version=0.4.3  requires=-  unblocks=pkg:virtualenv==21.6.1
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:executing==2.2.1  version=2.2.1  requires=-  unblocks=pkg:stack-data==0.6.3
#@check python -m pip show executing
if python3 -m pip install --break-system-packages --no-deps executing==2.2.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:executing==2.2.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.7  version=3.29.7  requires=-  unblocks=pkg:python-discovery==1.4.4,pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:identify==2.6.19  version=2.6.19  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show identify
if python3 -m pip install --break-system-packages --no-deps identify==2.6.19
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:identify==2.6.19" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.10.0  version=1.10.0  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:build==1.5.1,pkg:pyproject-api==1.10.1,pkg:pytest==9.1.1,pkg:tox==4.56.4,pkg:wheel==0.47.0
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:parso==0.8.7  version=0.8.7  requires=-  unblocks=pkg:jedi==0.20.0
#@check python -m pip show parso
if python3 -m pip install --break-system-packages --no-deps parso==0.8.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:parso==0.8.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jedi==0.20.0  version=0.20.0  requires=pkg:parso==0.8.7  unblocks=pkg:ipython==9.15.0
#@check python -m pip show jedi
if python3 -m pip install --break-system-packages --no-deps jedi==0.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jedi==0.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=-  unblocks=pkg:python-discovery==1.4.4,pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest-cov==7.1.0,pkg:pytest==9.1.1,pkg:tox==4.56.4
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:psutil==7.2.2  version=7.2.2  requires=-  unblocks=pkg:ipython==9.15.0
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
#@node pkg:pexpect==4.9.0  version=4.9.0  requires=pkg:ptyprocess==0.7.0  unblocks=pkg:ipython==9.15.0
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
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:ipython-pygments-lexers==1.1.1,pkg:ipython==9.15.0,pkg:pytest==9.1.1
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ipython-pygments-lexers==1.1.1  version=1.1.1  requires=pkg:pygments==2.20.0  unblocks=pkg:ipython==9.15.0
#@check python -m pip show ipython-pygments-lexers
if python3 -m pip install --break-system-packages --no-deps ipython-pygments-lexers==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ipython-pygments-lexers==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-api==1.10.1  version=1.10.1  requires=pkg:packaging==26.2  unblocks=pkg:tox==4.56.4
#@check python -m pip show pyproject-api
if python3 -m pip install --break-system-packages --no-deps pyproject-api==1.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-api==1.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=-  unblocks=pkg:build==1.5.1
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:build==1.5.1  version=1.5.1  requires=pkg:colorama==0.4.6,pkg:packaging==26.2,pkg:pyproject-hooks==1.2.0
#@check python -m pip show build
if python3 -m pip install --break-system-packages --no-deps build==1.5.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:build==1.5.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.1.1  version=9.1.1  requires=pkg:colorama==0.4.6,pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0  unblocks=pkg:pytest-cov==7.1.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==7.1.0  version=7.1.0  requires=pkg:coverage==7.15.1,pkg:pluggy==1.6.0,pkg:pytest==9.1.1
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==7.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==7.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-discovery==1.4.4  version=1.4.4  requires=pkg:filelock==3.29.7,pkg:platformdirs==4.10.0  unblocks=pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show python-discovery
if python3 -m pip install --break-system-packages --no-deps python-discovery==1.4.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-discovery==1.4.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml==6.0.3  version=6.0.3  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.15.21  version=0.15.21  requires=-
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.15.21
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.15.21" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:stack-data==0.6.3  version=0.6.3  requires=pkg:asttokens==3.0.2,pkg:executing==2.2.1,pkg:pure-eval==0.2.3  unblocks=pkg:ipython==9.15.0
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
#@node pkg:tomli-w==1.2.0  version=1.2.0  requires=-  unblocks=pkg:tox==4.56.4
#@check python -m pip show tomli-w
if python3 -m pip install --break-system-packages --no-deps tomli-w==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli-w==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:traitlets==5.15.1  version=5.15.1  requires=-  unblocks=pkg:ipython==9.15.0,pkg:matplotlib-inline==0.2.2
#@check python -m pip show traitlets
if python3 -m pip install --break-system-packages --no-deps traitlets==5.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:traitlets==5.15.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:matplotlib-inline==0.2.2  version=0.2.2  requires=pkg:traitlets==5.15.1  unblocks=pkg:ipython==9.15.0
#@check python -m pip show matplotlib-inline
if python3 -m pip install --break-system-packages --no-deps matplotlib-inline==0.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:matplotlib-inline==0.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.16.0  version=4.16.0  requires=-  unblocks=pkg:ipython==9.15.0
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==21.6.1  version=21.6.1  requires=pkg:distlib==0.4.3,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,pkg:python-discovery==1.4.4  unblocks=pkg:pre-commit==4.6.0,pkg:tox==4.56.4
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==21.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==21.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit==4.6.0  version=4.6.0  requires=pkg:cfgv==3.5.0,pkg:identify==2.6.19,pkg:nodeenv==1.10.0,pkg:pyyaml==6.0.3,pkg:virtualenv==21.6.1
#@check python -m pip show pre-commit
if python3 -m pip install --break-system-packages --no-deps pre-commit==4.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit==4.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox==4.56.4  version=4.56.4  requires=pkg:cachetools==7.1.4,pkg:colorama==0.4.6,pkg:filelock==3.29.7,pkg:packaging==26.2,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pyproject-api==1.10.1,pkg:python-discovery==1.4.4,pkg:tomli-w==1.2.0,pkg:virtualenv==21.6.1
#@check python -m pip show tox
if python3 -m pip install --break-system-packages --no-deps tox==4.56.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox==4.56.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wcwidth==0.8.2  version=0.8.2  requires=-  unblocks=pkg:prompt-toolkit==3.0.52
#@check python -m pip show wcwidth
if python3 -m pip install --break-system-packages --no-deps wcwidth==0.8.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wcwidth==0.8.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:prompt-toolkit==3.0.52  version=3.0.52  requires=pkg:wcwidth==0.8.2  unblocks=pkg:ipython==9.15.0
#@check python -m pip show prompt-toolkit
if python3 -m pip install --break-system-packages --no-deps prompt-toolkit==3.0.52
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:prompt-toolkit==3.0.52" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ipython==9.15.0  version=9.15.0  requires=pkg:colorama==0.4.6,pkg:decorator==5.3.1,pkg:ipython-pygments-lexers==1.1.1,pkg:jedi==0.20.0,pkg:matplotlib-inline==0.2.2,pkg:pexpect==4.9.0,pkg:prompt-toolkit==3.0.52,pkg:psutil==7.2.2,pkg:pygments==2.20.0,pkg:stack-data==0.6.3,pkg:traitlets==5.15.1,pkg:typing-extensions==4.16.0
#@check python -m pip show ipython
if python3 -m pip install --break-system-packages --no-deps ipython==9.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ipython==9.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wheel==0.47.0  version=0.47.0  requires=pkg:packaging==26.2
#@check python -m pip show wheel
if python3 -m pip install --break-system-packages --no-deps wheel==0.47.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wheel==0.47.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:python-dotenv  requires=pkg:build==1.5.1,pkg:bumpversion==0.6.0,pkg:click==8.4.2,pkg:ipython==9.15.0,pkg:pre-commit==4.6.0,pkg:pytest-cov==7.1.0,pkg:pytest==9.1.1,pkg:ruff==0.15.21,pkg:tox==4.56.4,pkg:wheel==0.47.0
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:python-dotenv" >> /tmp/v3_failed_nodes.log
fi
