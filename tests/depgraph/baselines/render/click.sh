#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 63 reciped (63 pip) + 0 needs
#   graph-hash: sha256:88ad84fa28e0   python: 3.11   platform: aarch64-manylinux_2_28
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== PIP ====================
#@node pkg:alabaster==1.0.0  version=1.0.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show alabaster
if python3 -m pip install --break-system-packages --no-deps alabaster==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:alabaster==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ast-serialize==0.6.0  version=0.6.0  requires=-  unblocks=pkg:mypy==2.3.0
#@check python -m pip show ast-serialize
if python3 -m pip install --break-system-packages --no-deps ast-serialize==0.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ast-serialize==0.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:babel==2.18.0  version=2.18.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show babel
if python3 -m pip install --break-system-packages --no-deps babel==2.18.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:babel==2.18.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cachetools==7.1.4  version=7.1.4  requires=-  unblocks=pkg:tox==4.56.4
#@check python -m pip show cachetools
if python3 -m pip install --break-system-packages --no-deps cachetools==7.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cachetools==7.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2026.6.17  version=2026.6.17  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2026.6.17
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2026.6.17" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cfgv==3.5.0  version=3.5.0  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show cfgv
if python3 -m pip install --break-system-packages --no-deps cfgv==3.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cfgv==3.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.4.9  version=3.4.9  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.4.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.4.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:colorama==0.4.6  version=0.4.6  requires=-  unblocks=pkg:click==8.4.2,pkg:pytest==9.1.1,pkg:sphinx-autobuild==2025.8.25,pkg:sphinx==9.0.4,pkg:tox==4.56.4
#@check python -m pip show colorama
if python3 -m pip install --break-system-packages --no-deps colorama==0.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorama==0.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.4.2  version=8.4.2  requires=pkg:colorama==0.4.6  unblocks=pkg:uvicorn==0.51.0
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.4.3  version=0.4.3  requires=-  unblocks=pkg:virtualenv==21.6.1
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:docutils==0.22.4  version=0.22.4  requires=-  unblocks=pkg:sphinx==9.0.4
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
#@node pkg:filelock==3.29.7  version=3.29.7  requires=-  unblocks=pkg:python-discovery==1.4.4,pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:h11==0.16.0  version=0.16.0  requires=-  unblocks=pkg:uvicorn==0.51.0
#@check python -m pip show h11
if python3 -m pip install --break-system-packages --no-deps h11==0.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:h11==0.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:identify==2.6.19  version=2.6.19  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show identify
if python3 -m pip install --break-system-packages --no-deps identify==2.6.19
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:identify==2.6.19" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.18  version=3.18  requires=-  unblocks=pkg:anyio==4.14.2,pkg:requests==2.34.2
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
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:librt==0.13.0  version=0.13.0  requires=-  unblocks=pkg:mypy==2.3.0
#@check python -m pip show librt
if python3 -m pip install --break-system-packages --no-deps librt==0.13.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:librt==0.13.0" >> /tmp/v3_failed_nodes.log
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
#@node pkg:mypy-extensions==1.1.0  version=1.1.0  requires=-  unblocks=pkg:mypy==2.3.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.10.0  version=1.10.0  requires=-  unblocks=pkg:pre-commit==4.6.0,pkg:pyright==1.1.411
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:pyproject-api==1.10.1,pkg:pytest==9.1.1,pkg:sphinx==9.0.4,pkg:tox-uv-bare==1.35.2,pkg:tox==4.56.4
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pathspec==1.1.1  version=1.1.1  requires=-  unblocks=pkg:mypy==2.3.0
#@check python -m pip show pathspec
if python3 -m pip install --break-system-packages --no-deps pathspec==1.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pathspec==1.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=-  unblocks=pkg:python-discovery==1.4.4,pkg:tox==4.56.4,pkg:virtualenv==21.6.1
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest==9.1.1,pkg:tox==4.56.4
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:pytest==9.1.1,pkg:sphinx==9.0.4
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-api==1.10.1  version=1.10.1  requires=pkg:packaging==26.2  unblocks=pkg:tox==4.56.4
#@check python -m pip show pyproject-api
if python3 -m pip install --break-system-packages --no-deps pyproject-api==1.10.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-api==1.10.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.1.1  version=9.1.1  requires=pkg:colorama==0.4.6,pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0  unblocks=pkg:pytest-randomly==4.1.0,pkg:pytest-xdist==3.8.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==9.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==9.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-randomly==4.1.0  version=4.1.0  requires=pkg:pytest==9.1.1
#@check python -m pip show pytest-randomly
if python3 -m pip install --break-system-packages --no-deps pytest-randomly==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-randomly==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-xdist==3.8.0  version=3.8.0  requires=pkg:execnet==2.1.2,pkg:pytest==9.1.1
#@check python -m pip show pytest-xdist
if python3 -m pip install --break-system-packages --no-deps pytest-xdist==3.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-xdist==3.8.0" >> /tmp/v3_failed_nodes.log
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
#@node pkg:roman-numerals==4.1.0  version=4.1.0  requires=-  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show roman-numerals
if python3 -m pip install --break-system-packages --no-deps roman-numerals==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:roman-numerals==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.15.21  version=0.15.21  requires=-
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.15.21
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.15.21" >> /tmp/v3_failed_nodes.log
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
#@node pkg:tomli-w==1.2.0  version=1.2.0  requires=-  unblocks=pkg:tox==4.56.4
#@check python -m pip show tomli-w
if python3 -m pip install --break-system-packages --no-deps tomli-w==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli-w==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.16.0  version=4.16.0  requires=-  unblocks=pkg:anyio==4.14.2,pkg:mypy==2.3.0,pkg:pyright==1.1.411,pkg:starlette==1.3.1
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:anyio==4.14.2  version=4.14.2  requires=pkg:idna==3.18,pkg:typing-extensions==4.16.0  unblocks=pkg:starlette==1.3.1,pkg:watchfiles==1.2.0
#@check python -m pip show anyio
if python3 -m pip install --break-system-packages --no-deps anyio==4.14.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:anyio==4.14.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==2.3.0  version=2.3.0  requires=pkg:ast-serialize==0.6.0,pkg:librt==0.13.0,pkg:mypy-extensions==1.1.0,pkg:pathspec==1.1.1,pkg:typing-extensions==4.16.0
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyright==1.1.411  version=1.1.411  requires=pkg:nodeenv==1.10.0,pkg:typing-extensions==4.16.0
#@check python -m pip show pyright
if python3 -m pip install --break-system-packages --no-deps pyright==1.1.411
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyright==1.1.411" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:starlette==1.3.1  version=1.3.1  requires=pkg:anyio==4.14.2,pkg:typing-extensions==4.16.0  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show starlette
if python3 -m pip install --break-system-packages --no-deps starlette==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:starlette==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=-  unblocks=pkg:requests==2.34.2
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=pkg:certifi==2026.6.17,pkg:charset-normalizer==3.4.9,pkg:idna==3.18,pkg:urllib3==2.7.0  unblocks=pkg:sphinx==9.0.4
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.34.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.34.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx==9.0.4  version=9.0.4  requires=pkg:alabaster==1.0.0,pkg:babel==2.18.0,pkg:colorama==0.4.6,pkg:docutils==0.22.4,pkg:imagesize==2.0.0,pkg:jinja2==3.1.6,pkg:packaging==26.2,pkg:pygments==2.20.0,pkg:requests==2.34.2,pkg:roman-numerals==4.1.0,pkg:snowballstemmer==3.1.1,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show sphinx
if python3 -m pip install --break-system-packages --no-deps sphinx==9.0.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx==9.0.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:uv==0.11.28  version=0.11.28  requires=-  unblocks=pkg:pre-commit-uv==4.2.2,pkg:tox-uv==1.35.2
#@check python -m pip show uv
if python3 -m pip install --break-system-packages --no-deps uv==0.11.28
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:uv==0.11.28" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:uvicorn==0.51.0  version=0.51.0  requires=pkg:click==8.4.2,pkg:h11==0.16.0  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show uvicorn
if python3 -m pip install --break-system-packages --no-deps uvicorn==0.51.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:uvicorn==0.51.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==21.6.1  version=21.6.1  requires=pkg:distlib==0.4.3,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,pkg:python-discovery==1.4.4  unblocks=pkg:pre-commit==4.6.0,pkg:tox==4.56.4
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==21.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==21.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit==4.6.0  version=4.6.0  requires=pkg:cfgv==3.5.0,pkg:identify==2.6.19,pkg:nodeenv==1.10.0,pkg:pyyaml==6.0.3,pkg:virtualenv==21.6.1  unblocks=pkg:pre-commit-uv==4.2.2
#@check python -m pip show pre-commit
if python3 -m pip install --break-system-packages --no-deps pre-commit==4.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit==4.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit-uv==4.2.2  version=4.2.2  requires=pkg:pre-commit==4.6.0,pkg:uv==0.11.28
#@check python -m pip show pre-commit-uv
if python3 -m pip install --break-system-packages --no-deps pre-commit-uv==4.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit-uv==4.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox==4.56.4  version=4.56.4  requires=pkg:cachetools==7.1.4,pkg:colorama==0.4.6,pkg:filelock==3.29.7,pkg:packaging==26.2,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pyproject-api==1.10.1,pkg:python-discovery==1.4.4,pkg:tomli-w==1.2.0,pkg:virtualenv==21.6.1  unblocks=pkg:tox-uv-bare==1.35.2
#@check python -m pip show tox
if python3 -m pip install --break-system-packages --no-deps tox==4.56.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox==4.56.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox-uv-bare==1.35.2  version=1.35.2  requires=pkg:packaging==26.2,pkg:tox==4.56.4  unblocks=pkg:tox-uv==1.35.2
#@check python -m pip show tox-uv-bare
if python3 -m pip install --break-system-packages --no-deps tox-uv-bare==1.35.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox-uv-bare==1.35.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox-uv==1.35.2  version=1.35.2  requires=pkg:tox-uv-bare==1.35.2,pkg:uv==0.11.28
#@check python -m pip show tox-uv
if python3 -m pip install --break-system-packages --no-deps tox-uv==1.35.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox-uv==1.35.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:watchfiles==1.2.0  version=1.2.0  requires=pkg:anyio==4.14.2  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show watchfiles
if python3 -m pip install --break-system-packages --no-deps watchfiles==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:watchfiles==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:websockets==16.1  version=16.1  requires=-  unblocks=pkg:sphinx-autobuild==2025.8.25
#@check python -m pip show websockets
if python3 -m pip install --break-system-packages --no-deps websockets==16.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:websockets==16.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx-autobuild==2025.8.25  version=2025.8.25  requires=pkg:colorama==0.4.6,pkg:sphinx==9.0.4,pkg:starlette==1.3.1,pkg:uvicorn==0.51.0,pkg:watchfiles==1.2.0,pkg:websockets==16.1
#@check python -m pip show sphinx-autobuild
if python3 -m pip install --break-system-packages --no-deps sphinx-autobuild==2025.8.25
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx-autobuild==2025.8.25" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:click  requires=pkg:colorama==0.4.6,pkg:mypy==2.3.0,pkg:pre-commit-uv==4.2.2,pkg:pre-commit==4.6.0,pkg:pyright==1.1.411,pkg:pytest-randomly==4.1.0,pkg:pytest-xdist==3.8.0,pkg:pytest==9.1.1,pkg:ruff==0.15.21,pkg:sphinx-autobuild==2025.8.25,pkg:sphinx==9.0.4,pkg:tox-uv==1.35.2,pkg:tox==4.56.4
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:click" >> /tmp/v3_failed_nodes.log
fi
