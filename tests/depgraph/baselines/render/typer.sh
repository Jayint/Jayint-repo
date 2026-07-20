#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 98 reciped (31 toolchain, 67 pip) + 0 needs
#   graph-hash: sha256:f6ce67ea5e69
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:annotated-types==0.7.0,pkg:anyio==4.14.2,pkg:blinker==1.9.0,pkg:certifi==2026.6.17,pkg:cffi==2.1.0,pkg:cfgv==3.5.0,pkg:charset-normalizer==3.4.9,pkg:click==8.4.2,pkg:coverage==7.15.1,pkg:cryptography==49.0.0,pkg:dep-logic==0.6.0,pkg:distlib==0.4.3,pkg:execnet==2.1.2,pkg:filelock==3.29.7,pkg:findpython==0.8.0,pkg:h11==0.16.0,pkg:hishel==0.1.1,pkg:httpcore==1.0.9,pkg:httpx==0.27.2,pkg:id==1.6.1,pkg:identify==2.6.19,pkg:idna==3.18,pkg:iniconfig==2.3.0,pkg:installer==0.7.0,pkg:markdown-it-py==4.2.0,pkg:mdurl==0.1.2,pkg:mypy-extensions==1.1.0,pkg:mypy==1.4.1,pkg:nodeenv==1.10.0,pkg:packaging==26.2,pkg:pbs-installer==2026.6.10,pkg:pdm==2.26.2,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pre-commit==3.8.0,pkg:pycparser==3.0,pkg:pydantic-core==2.46.4,pkg:pydantic-settings==2.14.2,pkg:pydantic==2.13.4,pkg:pygithub==2.9.1,pkg:pygments==2.20.0,pkg:pyjwt==2.13.0,pkg:pynacl==1.6.2,pkg:pyproject-hooks==1.2.0,pkg:pytest-cov==5.0.0,pkg:pytest-sugar==1.0.0,pkg:pytest-xdist==3.8.0,pkg:pytest==8.4.2,pkg:python-discovery==1.4.4,pkg:python-dotenv==1.2.2,pkg:pyyaml==6.0.3,pkg:requests==2.34.2,pkg:resolvelib==1.2.1,pkg:rich==15.0.0,pkg:ruff==0.2.0,pkg:shellingham==1.5.4,pkg:sniffio==1.3.1,pkg:socksio==1.0.0,pkg:termcolor==3.3.0,pkg:tomli==2.4.1,pkg:tomlkit==0.15.0,pkg:truststore==0.10.4,pkg:typing-extensions==4.16.0,pkg:typing-inspection==0.4.2,pkg:unearth==0.18.2,pkg:urllib3==2.7.0,pkg:virtualenv==21.6.1  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:cargo  provider=apt:cargo  requires=-  unblocks=pkg:cryptography==49.0.0,pkg:pydantic-core==2.46.4  toolchain
#@check dpkg-query -W -f='${Status}' cargo 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends cargo
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:cargo" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:dbus-test-runner  provider=apt:dbus-test-runner  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' dbus-test-runner 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends dbus-test-runner
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:dbus-test-runner" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:flake8  provider=apt:flake8  requires=-  unblocks=pkg:mypy-extensions==1.1.0,pkg:mypy==1.4.1  toolchain
#@check dpkg-query -W -f='${Status}' flake8 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends flake8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:flake8" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:flit  provider=apt:flit  requires=-  unblocks=pkg:blinker==1.9.0,pkg:id==1.6.1,pkg:idna==3.18,pkg:installer==0.7.0,pkg:markdown-it-py==4.2.0,pkg:mdurl==0.1.2,pkg:packaging==26.2,pkg:pyproject-hooks==1.2.0,pkg:socksio==1.0.0,pkg:tomli==2.4.1,pkg:truststore==0.10.4,pkg:typing-extensions==4.16.0  toolchain
#@check dpkg-query -W -f='${Status}' flit 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends flit
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:flit" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:furo  provider=apt:furo  requires=-  unblocks=pkg:mypy==1.4.1,pkg:pytest==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' furo 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends furo
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:furo" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:gir1.2-glib-2.0  provider=apt:gir1.2-glib-2.0  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' gir1.2-glib-2.0 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends gir1.2-glib-2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:gir1.2-glib-2.0" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:gobject-introspection  provider=apt:gobject-introspection  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' gobject-introspection 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends gobject-introspection
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:gobject-introspection" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:help2man  provider=apt:help2man  requires=-  unblocks=pkg:identify==2.6.19,pkg:mypy-extensions==1.1.0,pkg:mypy==1.4.1,pkg:python-dotenv==1.2.2  toolchain
#@check dpkg-query -W -f='${Status}' help2man 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends help2man
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:help2man" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libffi-dev  provider=apt:libffi-dev  requires=-  unblocks=pkg:cffi==2.1.0  toolchain
#@check dpkg-query -W -f='${Status}' libffi-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libffi-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libffi-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libgee-0.8-dev  provider=apt:libgee-0.8-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libgee-0.8-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libgee-0.8-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libgee-0.8-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libgirepository1.0-dev  provider=apt:libgirepository1.0-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libgirepository1.0-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libgirepository1.0-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libgirepository1.0-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libglib2.0-dev  provider=apt:libglib2.0-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libglib2.0-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libglib2.0-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libglib2.0-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libjson-glib-dev  provider=apt:libjson-glib-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libjson-glib-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libjson-glib-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libjson-glib-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libproperties-cpp-dev  provider=apt:libproperties-cpp-dev  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' libproperties-cpp-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libproperties-cpp-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libproperties-cpp-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libssl-dev  provider=apt:libssl-dev  requires=-  unblocks=pkg:cryptography==49.0.0  toolchain
#@check dpkg-query -W -f='${Status}' libssl-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libssl-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libssl-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:libyaml-dev  provider=apt:libyaml-dev  requires=-  unblocks=pkg:pyyaml==6.0.3  toolchain
#@check dpkg-query -W -f='${Status}' libyaml-dev 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends libyaml-dev
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:libyaml-dev" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:lsof  provider=apt:lsof  requires=-  unblocks=pkg:pytest==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' lsof 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends lsof
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:lsof" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:openssl  provider=apt:openssl  requires=-  unblocks=pkg:requests==2.34.2  toolchain
#@check dpkg-query -W -f='${Status}' openssl 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends openssl
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:openssl" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:openstack-pkg-tools  provider=apt:openstack-pkg-tools  requires=-  unblocks=pkg:termcolor==3.3.0  toolchain
#@check dpkg-query -W -f='${Status}' openstack-pkg-tools 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends openstack-pkg-tools
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:openstack-pkg-tools" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pandoc  provider=apt:pandoc  requires=-  unblocks=pkg:pytest-xdist==3.8.0  toolchain
#@check dpkg-query -W -f='${Status}' pandoc 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pandoc
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pandoc" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:annotated-types==0.7.0,pkg:anyio==4.14.2,pkg:blinker==1.9.0,pkg:certifi==2026.6.17,pkg:cffi==2.1.0,pkg:cfgv==3.5.0,pkg:charset-normalizer==3.4.9,pkg:click==8.4.2,pkg:coverage==7.15.1,pkg:cryptography==49.0.0,pkg:dep-logic==0.6.0,pkg:distlib==0.4.3,pkg:execnet==2.1.2,pkg:filelock==3.29.7,pkg:findpython==0.8.0,pkg:h11==0.16.0,pkg:hishel==0.1.1,pkg:httpcore==1.0.9,pkg:httpx==0.27.2,pkg:id==1.6.1,pkg:identify==2.6.19,pkg:idna==3.18,pkg:iniconfig==2.3.0,pkg:installer==0.7.0,pkg:markdown-it-py==4.2.0,pkg:mdurl==0.1.2,pkg:mypy-extensions==1.1.0,pkg:mypy==1.4.1,pkg:nodeenv==1.10.0,pkg:packaging==26.2,pkg:pbs-installer==2026.6.10,pkg:pdm==2.26.2,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pre-commit==3.8.0,pkg:pycparser==3.0,pkg:pydantic-core==2.46.4,pkg:pydantic-settings==2.14.2,pkg:pydantic==2.13.4,pkg:pygithub==2.9.1,pkg:pygments==2.20.0,pkg:pyjwt==2.13.0,pkg:pynacl==1.6.2,pkg:pyproject-hooks==1.2.0,pkg:pytest-cov==5.0.0,pkg:pytest-sugar==1.0.0,pkg:pytest-xdist==3.8.0,pkg:pytest==8.4.2,pkg:python-discovery==1.4.4,pkg:python-dotenv==1.2.2,pkg:pyyaml==6.0.3,pkg:requests==2.34.2,pkg:resolvelib==1.2.1,pkg:rich==15.0.0,pkg:ruff==0.2.0,pkg:shellingham==1.5.4,pkg:sniffio==1.3.1,pkg:socksio==1.0.0,pkg:termcolor==3.3.0,pkg:tomli==2.4.1,pkg:tomlkit==0.15.0,pkg:truststore==0.10.4,pkg:typing-extensions==4.16.0,pkg:typing-inspection==0.4.2,pkg:unearth==0.18.2,pkg:urllib3==2.7.0,pkg:virtualenv==21.6.1  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pybuild-plugin-pyproject  provider=apt:pybuild-plugin-pyproject  requires=-  unblocks=pkg:annotated-types==0.7.0,pkg:anyio==4.14.2,pkg:blinker==1.9.0,pkg:cffi==2.1.0,pkg:charset-normalizer==3.4.9,pkg:coverage==7.15.1,pkg:cryptography==49.0.0,pkg:dep-logic==0.6.0,pkg:execnet==2.1.2,pkg:filelock==3.29.7,pkg:findpython==0.8.0,pkg:hishel==0.1.1,pkg:httpcore==1.0.9,pkg:httpx==0.27.2,pkg:id==1.6.1,pkg:idna==3.18,pkg:installer==0.7.0,pkg:markdown-it-py==4.2.0,pkg:mdurl==0.1.2,pkg:mypy==1.4.1,pkg:packaging==26.2,pkg:pbs-installer==2026.6.10,pkg:pdm==2.26.2,pkg:platformdirs==4.10.0,pkg:pluggy==1.6.0,pkg:pydantic-core==2.46.4,pkg:pydantic-settings==2.14.2,pkg:pydantic==2.13.4,pkg:pygithub==2.9.1,pkg:pyjwt==2.13.0,pkg:pyproject-hooks==1.2.0,pkg:pytest-xdist==3.8.0,pkg:pytest==8.4.2,pkg:pyyaml==6.0.3,pkg:requests==2.34.2,pkg:resolvelib==1.2.1,pkg:rich==15.0.0,pkg:shellingham==1.5.4,pkg:sniffio==1.3.1,pkg:socksio==1.0.0,pkg:termcolor==3.3.0,pkg:tomli==2.4.1,pkg:tomlkit==0.15.0,pkg:truststore==0.10.4,pkg:typing-extensions==4.16.0,pkg:unearth==0.18.2,pkg:urllib3==2.7.0,pkg:virtualenv==21.6.1  toolchain
#@check dpkg-query -W -f='${Status}' pybuild-plugin-pyproject 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pybuild-plugin-pyproject
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pybuild-plugin-pyproject" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:pyflakes3  provider=apt:pyflakes3  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' pyflakes3 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends pyflakes3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:pyflakes3" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:python3:any  provider=apt:python3:any  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' python3:any 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends python3:any
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:python3:any" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:python3:native  provider=apt:python3:native  requires=-  unblocks=pkg:pydantic-core==2.46.4  toolchain
#@check dpkg-query -W -f='${Status}' python3:native 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends python3:native
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:python3:native" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:sphinx-common  provider=apt:sphinx-common  requires=-  unblocks=pkg:charset-normalizer==3.4.9  toolchain
#@check dpkg-query -W -f='${Status}' sphinx-common 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends sphinx-common
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:sphinx-common" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:unzip  provider=apt:unzip  requires=-  unblocks=pkg:virtualenv==21.6.1  toolchain
#@check dpkg-query -W -f='${Status}' unzip 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends unzip
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:unzip" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:valac  provider=apt:valac  requires=-  unblocks=pkg:click==8.4.2  toolchain
#@check dpkg-query -W -f='${Status}' valac 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends valac
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:valac" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:virtualenv  provider=apt:virtualenv  requires=-  unblocks=pkg:cffi==2.1.0  toolchain
#@check dpkg-query -W -f='${Status}' virtualenv 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends virtualenv
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:virtualenv" >> /tmp/v3_failed_nodes.log
fi
#@node aptdep:zip  provider=apt:zip  requires=-  unblocks=pkg:virtualenv==21.6.1  toolchain
#@check dpkg-query -W -f='${Status}' zip 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends zip
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:zip" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:annotated-types==0.7.0  version=0.7.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pydantic==2.13.4
#@check python -m pip show annotated-types
if python3 -m pip install --break-system-packages --no-deps annotated-types==0.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:annotated-types==0.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:blinker==1.9.0  version=1.9.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show blinker
if python3 -m pip install --break-system-packages --no-deps blinker==1.9.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:blinker==1.9.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2026.6.17  version=2026.6.17  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:httpcore==1.0.9,pkg:httpx==0.27.2,pkg:pdm==2.26.2,pkg:requests==2.34.2
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2026.6.17
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2026.6.17" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cfgv==3.5.0  version=3.5.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==3.8.0
#@check python -m pip show cfgv
if python3 -m pip install --break-system-packages --no-deps cfgv==3.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cfgv==3.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:charset-normalizer==3.4.9  version=3.4.9  requires=aptdep:pybuild-plugin-pyproject,aptdep:sphinx-common,binary:pkg-config,tool:build-essential  unblocks=pkg:requests==2.34.2
#@check python -m pip show charset-normalizer
if python3 -m pip install --break-system-packages --no-deps charset-normalizer==3.4.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:charset-normalizer==3.4.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.4.2  version=8.4.2  requires=aptdep:dbus-test-runner,aptdep:gir1.2-glib-2.0,aptdep:gobject-introspection,aptdep:libgee-0.8-dev,aptdep:libgirepository1.0-dev,aptdep:libglib2.0-dev,aptdep:libjson-glib-dev,aptdep:libproperties-cpp-dev,aptdep:pyflakes3,aptdep:python3:any,aptdep:valac,binary:pkg-config,tool:build-essential
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:distlib==0.4.3  version=0.4.3  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:virtualenv==21.6.1
#@check python -m pip show distlib
if python3 -m pip install --break-system-packages --no-deps distlib==0.4.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:distlib==0.4.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:execnet==2.1.2  version=2.1.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pytest-xdist==3.8.0
#@check python -m pip show execnet
if python3 -m pip install --break-system-packages --no-deps execnet==2.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:execnet==2.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:filelock==3.29.7  version=3.29.7  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2,pkg:python-discovery==1.4.4,pkg:virtualenv==21.6.1
#@check python -m pip show filelock
if python3 -m pip install --break-system-packages --no-deps filelock==3.29.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:filelock==3.29.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:h11==0.16.0  version=0.16.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:httpcore==1.0.9
#@check python -m pip show h11
if python3 -m pip install --break-system-packages --no-deps h11==0.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:h11==0.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:httpcore==1.0.9  version=1.0.9  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:certifi==2026.6.17,pkg:h11==0.16.0,tool:build-essential  unblocks=pkg:httpx==0.27.2,pkg:pdm==2.26.2
#@check python -m pip show httpcore
if python3 -m pip install --break-system-packages --no-deps httpcore==1.0.9
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:httpcore==1.0.9" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:identify==2.6.19  version=2.6.19  requires=aptdep:help2man,binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==3.8.0
#@check python -m pip show identify
if python3 -m pip install --break-system-packages --no-deps identify==2.6.19
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:identify==2.6.19" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.18  version=3.18  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:anyio==4.14.2,pkg:httpx==0.27.2,pkg:requests==2.34.2
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.18
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.18" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pytest==8.4.2
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:installer==0.7.0  version=0.7.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show installer
if python3 -m pip install --break-system-packages --no-deps installer==0.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:installer==0.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mdurl==0.1.2  version=0.1.2  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:markdown-it-py==4.2.0
#@check python -m pip show mdurl
if python3 -m pip install --break-system-packages --no-deps mdurl==0.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mdurl==0.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown-it-py==4.2.0  version=4.2.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:mdurl==0.1.2,tool:build-essential  unblocks=pkg:rich==15.0.0
#@check python -m pip show markdown-it-py
if python3 -m pip install --break-system-packages --no-deps markdown-it-py==4.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown-it-py==4.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.1.0  version=1.1.0  requires=aptdep:flake8,aptdep:help2man,binary:pkg-config,tool:build-essential  unblocks=pkg:mypy==1.4.1
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nodeenv==1.10.0  version=1.10.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==3.8.0
#@check python -m pip show nodeenv
if python3 -m pip install --break-system-packages --no-deps nodeenv==1.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nodeenv==1.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:dep-logic==0.6.0,pkg:findpython==0.8.0,pkg:pdm==2.26.2,pkg:pytest-sugar==1.0.0,pkg:pytest==8.4.2,pkg:unearth==0.18.2
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:dep-logic==0.6.0  version=0.6.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:packaging==26.2,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show dep-logic
if python3 -m pip install --break-system-packages --no-deps dep-logic==0.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:dep-logic==0.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pbs-installer==2026.6.10  version=2026.6.10  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show pbs-installer
if python3 -m pip install --break-system-packages --no-deps pbs-installer==2026.6.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pbs-installer==2026.6.10" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.10.0  version=4.10.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:findpython==0.8.0,pkg:pdm==2.26.2,pkg:python-discovery==1.4.4,pkg:virtualenv==21.6.1
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:findpython==0.8.0  version=0.8.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:packaging==26.2,pkg:platformdirs==4.10.0,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show findpython
if python3 -m pip install --break-system-packages --no-deps findpython==0.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:findpython==0.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pytest==8.4.2
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.6.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.6.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycparser==3.0  version=3.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:cffi==2.1.0
#@check python -m pip show pycparser
if python3 -m pip install --break-system-packages --no-deps pycparser==3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycparser==3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cffi==2.1.0  version=2.1.0  requires=aptdep:libffi-dev,aptdep:pybuild-plugin-pyproject,aptdep:virtualenv,binary:pkg-config,pkg:pycparser==3.0,tool:build-essential  unblocks=pkg:cryptography==49.0.0,pkg:pynacl==1.6.2
#@check python -m pip show cffi
if python3 -m pip install --break-system-packages --no-deps cffi==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cffi==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cryptography==49.0.0  version=49.0.0  requires=aptdep:cargo,aptdep:libssl-dev,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:cffi==2.1.0,tool:build-essential  unblocks=pkg:pyjwt==2.13.0
#@check python -m pip show cryptography
if python3 -m pip install --break-system-packages --no-deps cryptography==49.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cryptography==49.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.20.0  version=2.20.0  requires=binary:pkg-config,tool:build-essential  unblocks=pkg:pytest==8.4.2,pkg:rich==15.0.0
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.20.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.20.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyjwt==2.13.0  version=2.13.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:cryptography==49.0.0,tool:build-essential  unblocks=pkg:pygithub==2.9.1
#@check python -m pip show pyjwt
if python3 -m pip install --break-system-packages --no-deps pyjwt==2.13.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyjwt==2.13.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pynacl==1.6.2  version=1.6.2  requires=binary:pkg-config,pkg:cffi==2.1.0,tool:build-essential  unblocks=pkg:pygithub==2.9.1
#@check python -m pip show pynacl
if python3 -m pip install --break-system-packages --no-deps pynacl==1.6.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pynacl==1.6.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest==8.4.2  version=8.4.2  requires=aptdep:furo,aptdep:lsof,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:iniconfig==2.3.0,pkg:packaging==26.2,pkg:pluggy==1.6.0,pkg:pygments==2.20.0,tool:build-essential  unblocks=pkg:pytest-cov==5.0.0,pkg:pytest-sugar==1.0.0,pkg:pytest-xdist==3.8.0
#@check python -m pip show pytest
if python3 -m pip install --break-system-packages --no-deps pytest==8.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest==8.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-xdist==3.8.0  version=3.8.0  requires=aptdep:pandoc,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:execnet==2.1.2,pkg:pytest==8.4.2,tool:build-essential
#@check python -m pip show pytest-xdist
if python3 -m pip install --break-system-packages --no-deps pytest-xdist==3.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-xdist==3.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-discovery==1.4.4  version=1.4.4  requires=binary:pkg-config,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,tool:build-essential  unblocks=pkg:virtualenv==21.6.1
#@check python -m pip show python-discovery
if python3 -m pip install --break-system-packages --no-deps python-discovery==1.4.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-discovery==1.4.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-dotenv==1.2.2  version=1.2.2  requires=aptdep:help2man,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2,pkg:pydantic-settings==2.14.2
#@check python -m pip show python-dotenv
if python3 -m pip install --break-system-packages --no-deps python-dotenv==1.2.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-dotenv==1.2.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml==6.0.3  version=6.0.3  requires=aptdep:libyaml-dev,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pre-commit==3.8.0
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:resolvelib==1.2.1  version=1.2.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show resolvelib
if python3 -m pip install --break-system-packages --no-deps resolvelib==1.2.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:resolvelib==1.2.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rich==15.0.0  version=15.0.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:markdown-it-py==4.2.0,pkg:pygments==2.20.0,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show rich
if python3 -m pip install --break-system-packages --no-deps rich==15.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rich==15.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.2.0  version=0.2.0  requires=binary:pkg-config,tool:build-essential
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:shellingham==1.5.4  version=1.5.4  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show shellingham
if python3 -m pip install --break-system-packages --no-deps shellingham==1.5.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:shellingham==1.5.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sniffio==1.3.1  version=1.3.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:httpx==0.27.2
#@check python -m pip show sniffio
if python3 -m pip install --break-system-packages --no-deps sniffio==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sniffio==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:socksio==1.0.0  version=1.0.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:httpx==0.27.2
#@check python -m pip show socksio
if python3 -m pip install --break-system-packages --no-deps socksio==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:socksio==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:termcolor==3.3.0  version=3.3.0  requires=aptdep:openstack-pkg-tools,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pytest-sugar==1.0.0
#@check python -m pip show termcolor
if python3 -m pip install --break-system-packages --no-deps termcolor==3.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:termcolor==3.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-sugar==1.0.0  version=1.0.0  requires=binary:pkg-config,pkg:packaging==26.2,pkg:pytest==8.4.2,pkg:termcolor==3.3.0,tool:build-essential
#@check python -m pip show pytest-sugar
if python3 -m pip install --break-system-packages --no-deps pytest-sugar==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-sugar==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.4.1  version=2.4.1  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:coverage==7.15.1
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.15.1  version=7.15.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:tomli==2.4.1,tool:build-essential  unblocks=pkg:pytest-cov==5.0.0
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.15.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.15.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pytest-cov==5.0.0  version=5.0.0  requires=binary:pkg-config,pkg:coverage==7.15.1,pkg:pytest==8.4.2,tool:build-essential
#@check python -m pip show pytest-cov
if python3 -m pip install --break-system-packages --no-deps pytest-cov==5.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pytest-cov==5.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomlkit==0.15.0  version=0.15.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show tomlkit
if python3 -m pip install --break-system-packages --no-deps tomlkit==0.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomlkit==0.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:truststore==0.10.4  version=0.10.4  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show truststore
if python3 -m pip install --break-system-packages --no-deps truststore==0.10.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:truststore==0.10.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.16.0  version=4.16.0  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:anyio==4.14.2,pkg:mypy==1.4.1,pkg:pydantic-core==2.46.4,pkg:pydantic==2.13.4,pkg:pygithub==2.9.1,pkg:typing-inspection==0.4.2
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:anyio==4.14.2  version=4.14.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:idna==3.18,pkg:typing-extensions==4.16.0,tool:build-essential  unblocks=pkg:httpx==0.27.2
#@check python -m pip show anyio
if python3 -m pip install --break-system-packages --no-deps anyio==4.14.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:anyio==4.14.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:httpx==0.27.2  version=0.27.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:anyio==4.14.2,pkg:certifi==2026.6.17,pkg:httpcore==1.0.9,pkg:idna==3.18,pkg:sniffio==1.3.1,pkg:socksio==1.0.0,tool:build-essential  unblocks=pkg:hishel==0.1.1,pkg:pdm==2.26.2,pkg:unearth==0.18.2
#@check python -m pip show httpx
if python3 -m pip install --break-system-packages --no-deps httpx==0.27.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:httpx==0.27.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:hishel==0.1.1  version=0.1.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:httpx==0.27.2,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show hishel
if python3 -m pip install --break-system-packages --no-deps hishel==0.1.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:hishel==0.1.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==1.4.1  version=1.4.1  requires=aptdep:flake8,aptdep:furo,aptdep:help2man,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:mypy-extensions==1.1.0,pkg:typing-extensions==4.16.0,tool:build-essential
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==1.4.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==1.4.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pydantic-core==2.46.4  version=2.46.4  requires=aptdep:cargo,aptdep:pybuild-plugin-pyproject,aptdep:python3:native,binary:pkg-config,pkg:typing-extensions==4.16.0,tool:build-essential  unblocks=pkg:pydantic==2.13.4
#@check python -m pip show pydantic-core
if python3 -m pip install --break-system-packages --no-deps pydantic-core==2.46.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pydantic-core==2.46.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-inspection==0.4.2  version=0.4.2  requires=binary:pkg-config,pkg:typing-extensions==4.16.0,tool:build-essential  unblocks=pkg:pydantic-settings==2.14.2,pkg:pydantic==2.13.4
#@check python -m pip show typing-inspection
if python3 -m pip install --break-system-packages --no-deps typing-inspection==0.4.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-inspection==0.4.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pydantic==2.13.4  version=2.13.4  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:annotated-types==0.7.0,pkg:pydantic-core==2.46.4,pkg:typing-extensions==4.16.0,pkg:typing-inspection==0.4.2,tool:build-essential  unblocks=pkg:pydantic-settings==2.14.2
#@check python -m pip show pydantic
if python3 -m pip install --break-system-packages --no-deps pydantic==2.13.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pydantic==2.13.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pydantic-settings==2.14.2  version=2.14.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:pydantic==2.13.4,pkg:python-dotenv==1.2.2,pkg:typing-inspection==0.4.2,tool:build-essential
#@check python -m pip show pydantic-settings
if python3 -m pip install --break-system-packages --no-deps pydantic-settings==2.14.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pydantic-settings==2.14.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:unearth==0.18.2  version=0.18.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:httpx==0.27.2,pkg:packaging==26.2,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show unearth
if python3 -m pip install --break-system-packages --no-deps unearth==0.18.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:unearth==0.18.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.7.0  version=2.7.0  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,tool:build-essential  unblocks=pkg:id==1.6.1,pkg:pygithub==2.9.1,pkg:requests==2.34.2
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.7.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.7.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:id==1.6.1  version=1.6.1  requires=aptdep:flit,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:urllib3==2.7.0,tool:build-essential  unblocks=pkg:pdm==2.26.2
#@check python -m pip show id
if python3 -m pip install --break-system-packages --no-deps id==1.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:id==1.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.34.2  version=2.34.2  requires=aptdep:openssl,aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:certifi==2026.6.17,pkg:charset-normalizer==3.4.9,pkg:idna==3.18,pkg:urllib3==2.7.0,tool:build-essential  unblocks=pkg:pygithub==2.9.1
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.34.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.34.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygithub==2.9.1  version=2.9.1  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:pyjwt==2.13.0,pkg:pynacl==1.6.2,pkg:requests==2.34.2,pkg:typing-extensions==4.16.0,pkg:urllib3==2.7.0,tool:build-essential
#@check python -m pip show pygithub
if python3 -m pip install --break-system-packages --no-deps pygithub==2.9.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygithub==2.9.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:virtualenv==21.6.1  version=21.6.1  requires=aptdep:pybuild-plugin-pyproject,aptdep:unzip,aptdep:zip,binary:pkg-config,pkg:distlib==0.4.3,pkg:filelock==3.29.7,pkg:platformdirs==4.10.0,pkg:python-discovery==1.4.4,tool:build-essential  unblocks=pkg:pdm==2.26.2,pkg:pre-commit==3.8.0
#@check python -m pip show virtualenv
if python3 -m pip install --break-system-packages --no-deps virtualenv==21.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:virtualenv==21.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pdm==2.26.2  version=2.26.2  requires=aptdep:pybuild-plugin-pyproject,binary:pkg-config,pkg:blinker==1.9.0,pkg:certifi==2026.6.17,pkg:dep-logic==0.6.0,pkg:filelock==3.29.7,pkg:findpython==0.8.0,pkg:hishel==0.1.1,pkg:httpcore==1.0.9,pkg:httpx==0.27.2,pkg:id==1.6.1,pkg:installer==0.7.0,pkg:packaging==26.2,pkg:pbs-installer==2026.6.10,pkg:platformdirs==4.10.0,pkg:pyproject-hooks==1.2.0,pkg:python-dotenv==1.2.2,pkg:resolvelib==1.2.1,pkg:rich==15.0.0,pkg:shellingham==1.5.4,pkg:tomlkit==0.15.0,pkg:truststore==0.10.4,pkg:unearth==0.18.2,pkg:virtualenv==21.6.1,tool:build-essential
#@check python -m pip show pdm
if python3 -m pip install --break-system-packages --no-deps pdm==2.26.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pdm==2.26.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pre-commit==3.8.0  version=3.8.0  requires=binary:pkg-config,pkg:cfgv==3.5.0,pkg:identify==2.6.19,pkg:nodeenv==1.10.0,pkg:pyyaml==6.0.3,pkg:virtualenv==21.6.1,tool:build-essential
#@check python -m pip show pre-commit
if python3 -m pip install --break-system-packages --no-deps pre-commit==3.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pre-commit==3.8.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:typer  requires=pkg:click==8.4.2,pkg:coverage==7.15.1,pkg:httpx==0.27.2,pkg:mypy==1.4.1,pkg:pre-commit==3.8.0,pkg:pydantic-settings==2.14.2,pkg:pydantic==2.13.4,pkg:pygithub==2.9.1,pkg:pytest-cov==5.0.0,pkg:pytest-sugar==1.0.0,pkg:pytest-xdist==3.8.0,pkg:pytest==8.4.2,pkg:pyyaml==6.0.3,pkg:rich==15.0.0,pkg:ruff==0.2.0,pkg:shellingham==1.5.4,pkg:typing-extensions==4.16.0
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:typer" >> /tmp/v3_failed_nodes.log
fi
