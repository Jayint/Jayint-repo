#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 63 reciped (63 pip) + 0 needs
#   graph-hash: sha256:2ba1f84cf7e2   python: 3.11   platform: aarch64-manylinux_2_28   exclude-newer: 2024-12-21
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== PIP ====================
#@node pkg:alabaster==1.0.0  version=1.0.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show alabaster
if python3 -m pip install --break-system-packages --no-deps alabaster==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:alabaster==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:attrs==24.3.0  version=24.3.0  requires=-  unblocks=pkg:outcome==1.3.0.post0,pkg:trio==0.27.0
#@check python -m pip show attrs
if python3 -m pip install --break-system-packages --no-deps attrs==24.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attrs==24.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:babel==2.16.0  version=2.16.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show babel
if python3 -m pip install --break-system-packages --no-deps babel==2.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:babel==2.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cachetools==5.5.0  version=5.5.0  requires=-  unblocks=pkg:tox==4.23.2
#@check python -m pip show cachetools
if python3 -m pip install --break-system-packages --no-deps cachetools==5.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cachetools==5.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2024.12.14  version=2024.12.14  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2024.12.14
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2024.12.14" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cfgv==3.4.0  version=3.4.0  requires=-  unblocks=pkg:pre-commit==4.0.1
#@check python -m pip show cfgv
if python3 -m pip install --break-system-packages --no-deps cfgv==3.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cfgv==3.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:chardet==5.2.0  version=5.2.0  requires=-  unblocks=pkg:tox==4.23.2
#@check python -m pip show chardet
if python3 -m pip install --break-system-packages --no-deps chardet==5.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:chardet==5.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.4.0  version=3.4.0  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:colorama==0.4.6  version=0.4.6  requires=-  unblocks=pkg:build==1.2.2.post1,pkg:click==8.1.7,pkg:pytest==8.3.4,pkg:sphinx==8.1.3,pkg:tox==4.23.2
#@check python -m pip show colorama
if python3 -m pip install --break-system-packages --no-deps colorama==0.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorama==0.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.1.7  version=8.1.7  requires=pkg:colorama==0.4.6  unblocks=pkg:pip-compile-multi==2.7.1,pkg:pip-tools==7.4.1
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.1.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.1.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.3.9  version=0.3.9  requires=-  unblocks=pkg:virtualenv==20.28.0
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.3.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.3.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:docutils==0.21.2  version=0.21.2  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show docutils
if python3 -m pip install --break-system-packages --no-deps docutils==0.21.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:docutils==0.21.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:exceptiongroup==1.2.2  version=1.2.2  requires=-
#@check python -m pip show exceptiongroup
if python3 -m pip install --break-system-packages --no-deps exceptiongroup==1.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:exceptiongroup==1.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.16.1  version=3.16.1  requires=-  unblocks=pkg:tox==4.23.2,pkg:virtualenv==20.28.0
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.16.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.16.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:identify==2.6.3  version=2.6.3  requires=-  unblocks=pkg:pre-commit==4.0.1
#@check python -m pip show identify
if python3 -m pip install --break-system-packages --no-deps identify==2.6.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:identify==2.6.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.10  version=3.10  requires=-  unblocks=pkg:requests==2.32.3,pkg:trio==0.27.0
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.10" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:imagesize==1.4.1  version=1.4.1  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show imagesize
if python3 -m pip install --break-system-packages --no-deps imagesize==1.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:imagesize==1.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.0.0  version=2.0.0  requires=-  unblocks=pkg:pytest==8.3.4
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markupsafe==3.0.2  version=3.0.2  requires=-  unblocks=pkg:jinja2==3.1.4
#@check python -m pip show markupsafe
if python3 -m pip install --break-system-packages --no-deps markupsafe==3.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markupsafe==3.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jinja2==3.1.4  version=3.1.4  requires=pkg:markupsafe==3.0.2  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show jinja2
if python3 -m pip install --break-system-packages --no-deps jinja2==3.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jinja2==3.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.0.0  version=1.0.0  requires=-  unblocks=pkg:mypy==1.14.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.9.1  version=1.9.1  requires=-  unblocks=pkg:pre-commit==4.0.1
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.9.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.9.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:outcome==1.3.0.post0  version=1.3.0.post0  requires=pkg:attrs==24.3.0  unblocks=pkg:trio==0.27.0
#@check python -m pip show outcome
if python3 -m pip install --break-system-packages --no-deps outcome==1.3.0.post0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:outcome==1.3.0.post0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==24.2  version=24.2  requires=-  unblocks=pkg:build==1.2.2.post1,pkg:pallets-sphinx-themes==2.3.0,pkg:pyproject-api==1.8.0,pkg:pytest==8.3.4,pkg:sphinx==8.1.3,pkg:tox==4.23.2
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==24.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==24.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pip==24.3.1  version=24.3.1  requires=-  unblocks=pkg:pip-tools==7.4.1
#@check python -m pip show pip
if python3 -m pip install --break-system-packages --no-deps pip==24.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pip==24.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.3.6  version=4.3.6  requires=-  unblocks=pkg:tox==4.23.2,pkg:virtualenv==20.28.0
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.3.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.3.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.5.0  version=1.5.0  requires=-  unblocks=pkg:pytest==8.3.4,pkg:tox==4.23.2
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.18.0  version=2.18.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.18.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.18.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-api==1.8.0  version=1.8.0  requires=pkg:packaging==24.2  unblocks=pkg:tox==4.23.2
#@check python -m pip show pyproject-api
if python3 -m pip install --break-system-packages --no-deps pyproject-api==1.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-api==1.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=-  unblocks=pkg:build==1.2.2.post1,pkg:pip-tools==7.4.1
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:build==1.2.2.post1  version=1.2.2.post1  requires=pkg:colorama==0.4.6,pkg:packaging==24.2,pkg:pyproject-hooks==1.2.0  unblocks=pkg:pip-tools==7.4.1
#@check python -m pip show build
if python3 -m pip install --break-system-packages --no-deps build==1.2.2.post1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:build==1.2.2.post1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==8.3.4  version=8.3.4  requires=pkg:colorama==0.4.6,pkg:iniconfig==2.0.0,pkg:packaging==24.2,pkg:pluggy==1.5.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==8.3.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==8.3.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml==6.0.2  version=6.0.2  requires=-  unblocks=pkg:pre-commit==4.0.1
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==75.6.0  version=75.6.0  requires=-  unblocks=pkg:pip-tools==7.4.1
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==75.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==75.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sniffio==1.3.1  version=1.3.1  requires=-  unblocks=pkg:trio==0.27.0
#@check python -m pip show sniffio
if python3 -m pip install --break-system-packages --no-deps sniffio==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sniffio==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:snowballstemmer==2.2.0  version=2.2.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show snowballstemmer
if python3 -m pip install --break-system-packages --no-deps snowballstemmer==2.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:snowballstemmer==2.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sortedcontainers==2.4.0  version=2.4.0  requires=-  unblocks=pkg:trio==0.27.0
#@check python -m pip show sortedcontainers
if python3 -m pip install --break-system-packages --no-deps sortedcontainers==2.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sortedcontainers==2.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-applehelp==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-applehelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-applehelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-applehelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-devhelp==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-devhelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-devhelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-devhelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-htmlhelp==2.1.0  version=2.1.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-htmlhelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-htmlhelp==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-htmlhelp==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-jsmath==1.0.1  version=1.0.1  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-jsmath
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-jsmath==1.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-jsmath==1.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-qthelp==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-qthelp
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-qthelp==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-qthelp==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-serializinghtml==2.0.0  version=2.0.0  requires=-  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-serializinghtml
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-serializinghtml==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-serializinghtml==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.0.1  version=2.0.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:toposort==1.10  version=1.10  requires=-  unblocks=pkg:pip-compile-multi==2.7.1
#@check python -m pip show toposort
if python3 -m pip install --break-system-packages --no-deps toposort==1.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:toposort==1.10" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:trio==0.27.0  version=0.27.0  requires=pkg:attrs==24.3.0,pkg:idna==3.10,pkg:outcome==1.3.0.post0,pkg:sniffio==1.3.1,pkg:sortedcontainers==2.4.0
#@check python -m pip show trio
if python3 -m pip install --break-system-packages --no-deps trio==0.27.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:trio==0.27.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.12.2  version=4.12.2  requires=-  unblocks=pkg:mypy==1.14.0
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.12.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.12.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==1.14.0  version=1.14.0  requires=pkg:mypy-extensions==1.0.0,pkg:typing-extensions==4.12.2
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==1.14.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==1.14.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.2.3  version=2.2.3  requires=-  unblocks=pkg:requests==2.32.3
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.2.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.2.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.32.3  version=2.32.3  requires=pkg:certifi==2024.12.14,pkg:charset-normalizer==3.4.0,pkg:idna==3.10,pkg:urllib3==2.2.3  unblocks=pkg:sphinx==8.1.3
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.32.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.32.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx==8.1.3  version=8.1.3  requires=pkg:alabaster==1.0.0,pkg:babel==2.16.0,pkg:colorama==0.4.6,pkg:docutils==0.21.2,pkg:imagesize==1.4.1,pkg:jinja2==3.1.4,pkg:packaging==24.2,pkg:pygments==2.18.0,pkg:requests==2.32.3,pkg:snowballstemmer==2.2.0,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0  unblocks=pkg:pallets-sphinx-themes==2.3.0,pkg:sphinx-issues==5.0.0,pkg:sphinx-notfound-page==1.0.4,pkg:sphinxcontrib-log-cabinet==1.0.1
#@check python -m pip show sphinx
if python3 -m pip install --break-system-packages --no-deps sphinx==8.1.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx==8.1.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx-issues==5.0.0  version=5.0.0  requires=pkg:sphinx==8.1.3
#@check python -m pip show sphinx-issues
if python3 -m pip install --break-system-packages --no-deps sphinx-issues==5.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx-issues==5.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinx-notfound-page==1.0.4  version=1.0.4  requires=pkg:sphinx==8.1.3  unblocks=pkg:pallets-sphinx-themes==2.3.0
#@check python -m pip show sphinx-notfound-page
if python3 -m pip install --break-system-packages --no-deps sphinx-notfound-page==1.0.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinx-notfound-page==1.0.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pallets-sphinx-themes==2.3.0  version=2.3.0  requires=pkg:packaging==24.2,pkg:sphinx-notfound-page==1.0.4,pkg:sphinx==8.1.3
#@check python -m pip show pallets-sphinx-themes
if python3 -m pip install --break-system-packages --no-deps pallets-sphinx-themes==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pallets-sphinx-themes==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sphinxcontrib-log-cabinet==1.0.1  version=1.0.1  requires=pkg:sphinx==8.1.3
#@check python -m pip show sphinxcontrib-log-cabinet
if python3 -m pip install --break-system-packages --no-deps sphinxcontrib-log-cabinet==1.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sphinxcontrib-log-cabinet==1.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==20.28.0  version=20.28.0  requires=pkg:distlib==0.3.9,pkg:filelock==3.16.1,pkg:platformdirs==4.3.6  unblocks=pkg:pre-commit==4.0.1,pkg:tox==4.23.2
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==20.28.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==20.28.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit==4.0.1  version=4.0.1  requires=pkg:cfgv==3.4.0,pkg:identify==2.6.3,pkg:nodeenv==1.9.1,pkg:pyyaml==6.0.2,pkg:virtualenv==20.28.0
#@check python -m pip show pre-commit
if python3 -m pip install --break-system-packages --no-deps pre-commit==4.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit==4.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tox==4.23.2  version=4.23.2  requires=pkg:cachetools==5.5.0,pkg:chardet==5.2.0,pkg:colorama==0.4.6,pkg:filelock==3.16.1,pkg:packaging==24.2,pkg:platformdirs==4.3.6,pkg:pluggy==1.5.0,pkg:pyproject-api==1.8.0,pkg:virtualenv==20.28.0
#@check python -m pip show tox
if python3 -m pip install --break-system-packages --no-deps tox==4.23.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tox==4.23.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:wheel==0.45.1  version=0.45.1  requires=-  unblocks=pkg:pip-tools==7.4.1
#@check python -m pip show wheel
if python3 -m pip install --break-system-packages --no-deps wheel==0.45.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wheel==0.45.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pip-tools==7.4.1  version=7.4.1  requires=pkg:build==1.2.2.post1,pkg:click==8.1.7,pkg:pip==24.3.1,pkg:pyproject-hooks==1.2.0,pkg:setuptools==75.6.0,pkg:wheel==0.45.1  unblocks=pkg:pip-compile-multi==2.7.1
#@check python -m pip show pip-tools
if python3 -m pip install --break-system-packages --no-deps pip-tools==7.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pip-tools==7.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pip-compile-multi==2.7.1  version=2.7.1  requires=pkg:click==8.1.7,pkg:pip-tools==7.4.1,pkg:toposort==1.10
#@check python -m pip show pip-compile-multi
if python3 -m pip install --break-system-packages --no-deps pip-compile-multi==2.7.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pip-compile-multi==2.7.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:zipp==3.15.0  version=3.15.0  requires=-  unblocks=pkg:importlib-metadata==6.7.0
#@check python -m pip show zipp
if python3 -m pip install --break-system-packages --no-deps zipp==3.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:zipp==3.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:importlib-metadata==6.7.0  version=6.7.0  requires=pkg:zipp==3.15.0
#@check python -m pip show importlib-metadata
if python3 -m pip install --break-system-packages --no-deps importlib-metadata==6.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:importlib-metadata==6.7.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:Jinja2  requires=pkg:alabaster==1.0.0,pkg:attrs==24.3.0,pkg:babel==2.16.0,pkg:build==1.2.2.post1,pkg:cachetools==5.5.0,pkg:certifi==2024.12.14,pkg:cfgv==3.4.0,pkg:chardet==5.2.0,pkg:charset-normalizer==3.4.0,pkg:click==8.1.7,pkg:colorama==0.4.6,pkg:distlib==0.3.9,pkg:docutils==0.21.2,pkg:exceptiongroup==1.2.2,pkg:filelock==3.16.1,pkg:identify==2.6.3,pkg:idna==3.10,pkg:imagesize==1.4.1,pkg:importlib-metadata==6.7.0,pkg:iniconfig==2.0.0,pkg:jinja2==3.1.4,pkg:markupsafe==3.0.2,pkg:mypy-extensions==1.0.0,pkg:mypy==1.14.0,pkg:nodeenv==1.9.1,pkg:outcome==1.3.0.post0,pkg:packaging==24.2,pkg:pallets-sphinx-themes==2.3.0,pkg:pip-compile-multi==2.7.1,pkg:pip-tools==7.4.1,pkg:platformdirs==4.3.6,pkg:pluggy==1.5.0,pkg:pre-commit==4.0.1,pkg:pygments==2.18.0,pkg:pyproject-api==1.8.0,pkg:pyproject-hooks==1.2.0,pkg:pytest==8.3.4,pkg:pyyaml==6.0.2,pkg:requests==2.32.3,pkg:sniffio==1.3.1,pkg:snowballstemmer==2.2.0,pkg:sortedcontainers==2.4.0,pkg:sphinx-issues==5.0.0,pkg:sphinx-notfound-page==1.0.4,pkg:sphinx==8.1.3,pkg:sphinxcontrib-applehelp==2.0.0,pkg:sphinxcontrib-devhelp==2.0.0,pkg:sphinxcontrib-htmlhelp==2.1.0,pkg:sphinxcontrib-jsmath==1.0.1,pkg:sphinxcontrib-log-cabinet==1.0.1,pkg:sphinxcontrib-qthelp==2.0.0,pkg:sphinxcontrib-serializinghtml==2.0.0,pkg:tomli==2.0.1,pkg:toposort==1.10,pkg:tox==4.23.2,pkg:trio==0.27.0,pkg:typing-extensions==4.12.2,pkg:urllib3==2.2.3,pkg:virtualenv==20.28.0,pkg:wheel==0.45.1,pkg:zipp==3.15.0
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:Jinja2" >> /tmp/v3_failed_nodes.log
fi
