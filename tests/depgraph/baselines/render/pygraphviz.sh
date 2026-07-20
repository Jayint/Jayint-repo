#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 56 reciped (7 toolchain, 49 pip) + 0 needs
#   graph-hash: sha256:9f2e53d2a188   python: 3.11   platform: aarch64-manylinux_2_28
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
#@node linker:cdt  provider=apt:libgraphviz-dev  requires=-  toolchain
#@check find /usr/lib /lib -name libcdt.so 2>/dev/null | grep -q .
if apt-get install -y --no-install-recommends libgraphviz-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED linker:cdt" >> /tmp/v3_failed_nodes.log
fi
#@node linker:cgraph  provider=apt:libgraphviz-dev  requires=-  toolchain
#@check find /usr/lib /lib -name libcgraph.so 2>/dev/null | grep -q .
if apt-get install -y --no-install-recommends libgraphviz-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED linker:cgraph" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:graphviz  provider=apt:graphviz  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' graphviz 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends graphviz
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:graphviz" >> /tmp/v3_failed_nodes.log
fi
#@node linker:gvc  provider=apt:libgraphviz-dev  requires=-  toolchain
#@check find /usr/lib /lib -name libgvc.so 2>/dev/null | grep -q .
if apt-get install -y --no-install-recommends libgraphviz-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED linker:gvc" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libgraphviz-dev  provider=apt:libgraphviz-dev  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' libgraphviz-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libgraphviz-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libgraphviz-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pkgconf  provider=apt:pkgconf  requires=-  toolchain
#@check dpkg-query -W -f='${Status}' pkgconf 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pkgconf" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:backports-tarfile==1.2.0  version=1.2.0  requires=-  unblocks=pkg:jaraco-context==6.1.2
#@check python -m pip show backports-tarfile
if python3 -m pip install --break-system-packages --no-deps backports-tarfile==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:backports-tarfile==1.2.0" >> /tmp/v3_failed_nodes.log
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
#@node pkg:coverage==7.15.1  version=7.15.1  requires=-  unblocks=pkg:codecov==2.1.13,pkg:pytest-cov==7.1.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.15.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.4.3  version=0.4.3  requires=-  unblocks=pkg:virtualenv==21.6.1
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:docutils==0.23  version=0.23  requires=-  unblocks=pkg:readme-renderer==45.0
#@check python -m pip show docutils
if python3 -m pip install --break-system-packages --no-deps docutils==0.23
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:docutils==0.23" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.7  version=3.29.7  requires=-  unblocks=pkg:python-discovery==1.4.4,pkg:virtualenv==21.6.1
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
#@node pkg:idna==3.18  version=3.18  requires=-  unblocks=pkg:requests==2.34.2
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
#@node pkg:jaraco-context==6.1.2  version=6.1.2  requires=pkg:backports-tarfile==1.2.0  unblocks=pkg:keyring==25.7.0
#@check python -m pip show jaraco-context
if python3 -m pip install --break-system-packages --no-deps jaraco-context==6.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jaraco-context==6.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jeepney==0.9.0  version=0.9.0  requires=-  unblocks=pkg:keyring==25.7.0,pkg:secretstorage==3.5.0
#@check python -m pip show jeepney
if python3 -m pip install --break-system-packages --no-deps jeepney==0.9.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jeepney==0.9.0" >> /tmp/v3_failed_nodes.log
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
#@node pkg:more-itertools==11.1.0  version=11.1.0  requires=-  unblocks=pkg:jaraco-classes==3.4.0,pkg:jaraco-functools==4.5.0
#@check python -m pip show more-itertools
if python3 -m pip install --break-system-packages --no-deps more-itertools==11.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:more-itertools==11.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jaraco-classes==3.4.0  version=3.4.0  requires=pkg:more-itertools==11.1.0  unblocks=pkg:keyring==25.7.0
#@check python -m pip show jaraco-classes
if python3 -m pip install --break-system-packages --no-deps jaraco-classes==3.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jaraco-classes==3.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jaraco-functools==4.5.0  version=4.5.0  requires=pkg:more-itertools==11.1.0  unblocks=pkg:keyring==25.7.0
#@check python -m pip show jaraco-functools
if python3 -m pip install --break-system-packages --no-deps jaraco-functools==4.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jaraco-functools==4.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nh3==0.3.6  version=0.3.6  requires=-  unblocks=pkg:readme-renderer==45.0
#@check python -m pip show nh3
if python3 -m pip install --break-system-packages --no-deps nh3==0.3.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nh3==0.3.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.10.0  version=1.10.0  requires=-  unblocks=pkg:pre-commit==4.6.0
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:pytest==9.1.1,pkg:twine==6.2.0,pkg:wheel==0.47.0
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=-  unblocks=pkg:python-discovery==1.4.4,pkg:virtualenv==21.6.1
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest-cov==7.1.0,pkg:pytest==9.1.1
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycparser==3.0  version=3.0  requires=-  unblocks=pkg:cffi==2.1.0
#@check python -m pip show pycparser
if python3 -m pip install --break-system-packages --no-deps pycparser==3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycparser==3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cffi==2.1.0  version=2.1.0  requires=pkg:pycparser==3.0  unblocks=pkg:cryptography==49.0.0
#@check python -m pip show cffi
if python3 -m pip install --break-system-packages --no-deps cffi==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cffi==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cryptography==49.0.0  version=49.0.0  requires=pkg:cffi==2.1.0  unblocks=pkg:secretstorage==3.5.0
#@check python -m pip show cryptography
if python3 -m pip install --break-system-packages --no-deps cryptography==49.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cryptography==49.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=-  unblocks=pkg:pytest==9.1.1,pkg:readme-renderer==45.0,pkg:rich==15.0.0
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==9.1.1  version=9.1.1  requires=pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0  unblocks=pkg:pytest-cov==7.1.0
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
#@node pkg:python-discovery==1.4.4  version=1.4.4  requires=pkg:filelock==3.29.7,pkg:platformdirs==4.10.0  unblocks=pkg:virtualenv==21.6.1
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
#@node pkg:readme-renderer==45.0  version=45.0  requires=pkg:docutils==0.23,pkg:nh3==0.3.6,pkg:pygments==2.20.0  unblocks=pkg:twine==6.2.0
#@check python -m pip show readme-renderer
if python3 -m pip install --break-system-packages --no-deps readme-renderer==45.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:readme-renderer==45.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rfc3986==2.0.0  version=2.0.0  requires=-  unblocks=pkg:twine==6.2.0
#@check python -m pip show rfc3986
if python3 -m pip install --break-system-packages --no-deps rfc3986==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rfc3986==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rich==15.0.0  version=15.0.0  requires=pkg:markdown-it-py==4.2.0,pkg:pygments==2.20.0  unblocks=pkg:twine==6.2.0
#@check python -m pip show rich
if python3 -m pip install --break-system-packages --no-deps rich==15.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rich==15.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:secretstorage==3.5.0  version=3.5.0  requires=pkg:cryptography==49.0.0,pkg:jeepney==0.9.0  unblocks=pkg:keyring==25.7.0
#@check python -m pip show secretstorage
if python3 -m pip install --break-system-packages --no-deps secretstorage==3.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:secretstorage==3.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:setuptools==83.0.0  version=83.0.0  requires=-
#@check python -m pip show setuptools
if python3 -m pip install --break-system-packages --no-deps setuptools==83.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:setuptools==83.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.4.1  version=2.4.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=-  unblocks=pkg:id==1.6.1,pkg:requests==2.34.2,pkg:twine==6.2.0
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:id==1.6.1  version=1.6.1  requires=pkg:urllib3==2.7.0  unblocks=pkg:twine==6.2.0
#@check python -m pip show id
if python3 -m pip install --break-system-packages --no-deps id==1.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:id==1.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=pkg:certifi==2026.6.17,pkg:charset-normalizer==3.4.9,pkg:idna==3.18,pkg:urllib3==2.7.0  unblocks=pkg:codecov==2.1.13,pkg:requests-toolbelt==1.0.0,pkg:twine==6.2.0
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
#@node pkg:requests-toolbelt==1.0.0  version=1.0.0  requires=pkg:requests==2.34.2  unblocks=pkg:twine==6.2.0
#@check python -m pip show requests-toolbelt
if python3 -m pip install --break-system-packages --no-deps requests-toolbelt==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests-toolbelt==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==21.6.1  version=21.6.1  requires=pkg:distlib==0.4.3,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,pkg:python-discovery==1.4.4  unblocks=pkg:pre-commit==4.6.0
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
#@node pkg:wheel==0.47.0  version=0.47.0  requires=pkg:packaging==26.2
#@check python -m pip show wheel
if python3 -m pip install --break-system-packages --no-deps wheel==0.47.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:wheel==0.47.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:zipp==4.1.0  version=4.1.0  requires=-  unblocks=pkg:importlib-metadata==9.0.0
#@check python -m pip show zipp
if python3 -m pip install --break-system-packages --no-deps zipp==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:zipp==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:importlib-metadata==9.0.0  version=9.0.0  requires=pkg:zipp==4.1.0  unblocks=pkg:keyring==25.7.0
#@check python -m pip show importlib-metadata
if python3 -m pip install --break-system-packages --no-deps importlib-metadata==9.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:importlib-metadata==9.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:keyring==25.7.0  version=25.7.0  requires=pkg:importlib-metadata==9.0.0,pkg:jaraco-classes==3.4.0,pkg:jaraco-context==6.1.2,pkg:jaraco-functools==4.5.0,pkg:jeepney==0.9.0,pkg:secretstorage==3.5.0  unblocks=pkg:twine==6.2.0
#@check python -m pip show keyring
if python3 -m pip install --break-system-packages --no-deps keyring==25.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:keyring==25.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:twine==6.2.0  version=6.2.0  requires=pkg:id==1.6.1,pkg:keyring==25.7.0,pkg:packaging==26.2,pkg:readme-renderer==45.0,pkg:requests-toolbelt==1.0.0,pkg:requests==2.34.2,pkg:rfc3986==2.0.0,pkg:rich==15.0.0,pkg:urllib3==2.7.0
#@check python -m pip show twine
if python3 -m pip install --break-system-packages --no-deps twine==6.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:twine==6.2.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:pygraphviz  requires=aptdep:graphviz,aptdep:libgraphviz-dev,aptdep:pkgconf,linker:cdt,linker:cgraph,linker:gvc,pkg:codecov==2.1.13,pkg:pre-commit==4.6.0,pkg:pytest-cov==7.1.0,pkg:pytest==9.1.1,pkg:twine==6.2.0,pkg:wheel==0.47.0,tool:build-essential
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:pygraphviz" >> /tmp/v3_failed_nodes.log
fi
