#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 86 reciped (3 toolchain, 83 pip) + 0 needs
#   graph-hash: sha256:b234de57849f   python: 3.11   platform: aarch64-manylinux_2_28   exclude-newer: 2024-12-02
#
set -Eeuo pipefail

# Normalize `python` -> python3 so bare-`python` checks (pip show / pytest) resolve.
command -v python >/dev/null 2>&1 || ln -sf "$(command -v python3)" /usr/local/bin/python

# Ensure the pytest test-runner (testability-gate precondition; not a graph node).
python3 -c "import pytest" >/dev/null 2>&1 || python3 -m pip install --break-system-packages pytest

# ==================== TOOLCHAIN ====================
export DEBIAN_FRONTEND=noninteractive
apt-get update
#@node aptdep:black  provider=apt:black  requires=-  unblocks=pkg:mkautodoc==0.2.0  toolchain
#@check dpkg-query -W -f='${Status}' black 2>/dev/null | grep -q 'install ok installed'
if apt-get install -y --no-install-recommends black
then
    :
else
    echo "V3_NODE_INSTALL_FAILED aptdep:black" >> /tmp/v3_failed_nodes.log
fi
#@node tool:build-essential  provider=apt:build-essential  requires=-  unblocks=pkg:mkautodoc==0.2.0  toolchain  evidence=dpkg-query: package 'build-essential' is not installed and no information is available
#@check dpkg -s build-essential
if apt-get install -y --no-install-recommends build-essential
then
    :
else
    echo "V3_NODE_INSTALL_FAILED tool:build-essential" >> /tmp/v3_failed_nodes.log
fi
#@node binary:pkg-config  provider=apt:pkgconf  requires=-  unblocks=pkg:mkautodoc==0.2.0  toolchain
#@check command -v pkg-config
if apt-get install -y --no-install-recommends pkgconf
then
    :
else
    echo "V3_NODE_INSTALL_FAILED binary:pkg-config" >> /tmp/v3_failed_nodes.log
fi

# ==================== PIP ====================
#@node pkg:async-generator==1.10  version=1.10  requires=-  unblocks=pkg:trio-typing==0.10.0
#@check python -m pip show async-generator
if python3 -m pip install --break-system-packages --no-deps async-generator==1.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:async-generator==1.10" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:attrs==24.2.0  version=24.2.0  requires=-  unblocks=pkg:outcome==1.3.0.post0,pkg:trio==0.27.0
#@check python -m pip show attrs
if python3 -m pip install --break-system-packages --no-deps attrs==24.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attrs==24.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:babel==2.16.0  version=2.16.0  requires=-  unblocks=pkg:mkdocs-material==9.5.47
#@check python -m pip show babel
if python3 -m pip install --break-system-packages --no-deps babel==2.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:babel==2.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:backports-tarfile==1.2.0  version=1.2.0  requires=-  unblocks=pkg:jaraco-context==6.0.1
#@check python -m pip show backports-tarfile
if python3 -m pip install --break-system-packages --no-deps backports-tarfile==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:backports-tarfile==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:brotli==1.1.0  version=1.1.0  requires=-
#@check python -m pip show brotli
if python3 -m pip install --break-system-packages --no-deps brotli==1.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:brotli==1.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:certifi==2024.8.30  version=2024.8.30  requires=-  unblocks=pkg:httpcore==1.0.7,pkg:requests==2.32.3
#@check python -m pip show certifi
if python3 -m pip install --break-system-packages --no-deps certifi==2024.8.30
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:certifi==2024.8.30" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:chardet==5.2.0  version=5.2.0  requires=-
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
#@node pkg:colorama==0.4.6  version=0.4.6  requires=-  unblocks=pkg:build==1.2.2.post1,pkg:click==8.1.7,pkg:mkdocs-material==9.5.47,pkg:mkdocs==1.6.1,pkg:pytest==8.3.4
#@check python -m pip show colorama
if python3 -m pip install --break-system-packages --no-deps colorama==0.4.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:colorama==0.4.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:click==8.1.7  version=8.1.7  requires=pkg:colorama==0.4.6  unblocks=pkg:mkdocs==1.6.1,pkg:uvicorn==0.32.1
#@check python -m pip show click
if python3 -m pip install --break-system-packages --no-deps click==8.1.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:click==8.1.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:coverage==7.6.1  version=7.6.1  requires=-
#@check python -m pip show coverage
if python3 -m pip install --break-system-packages --no-deps coverage==7.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:coverage==7.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:docutils==0.21.2  version=0.21.2  requires=-  unblocks=pkg:readme-renderer==44.0
#@check python -m pip show docutils
if python3 -m pip install --break-system-packages --no-deps docutils==0.21.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:docutils==0.21.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:h11==0.14.0  version=0.14.0  requires=-  unblocks=pkg:httpcore==1.0.7,pkg:uvicorn==0.32.1
#@check python -m pip show h11
if python3 -m pip install --break-system-packages --no-deps h11==0.14.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:h11==0.14.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:hpack==4.0.0  version=4.0.0  requires=-  unblocks=pkg:h2==4.1.0
#@check python -m pip show hpack
if python3 -m pip install --break-system-packages --no-deps hpack==4.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:hpack==4.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:httpcore==1.0.7  version=1.0.7  requires=pkg:certifi==2024.8.30,pkg:h11==0.14.0
#@check python -m pip show httpcore
if python3 -m pip install --break-system-packages --no-deps httpcore==1.0.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:httpcore==1.0.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:hyperframe==6.0.1  version=6.0.1  requires=-  unblocks=pkg:h2==4.1.0
#@check python -m pip show hyperframe
if python3 -m pip install --break-system-packages --no-deps hyperframe==6.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:hyperframe==6.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:h2==4.1.0  version=4.1.0  requires=pkg:hpack==4.0.0,pkg:hyperframe==6.0.1
#@check python -m pip show h2
if python3 -m pip install --break-system-packages --no-deps h2==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:h2==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:idna==3.10  version=3.10  requires=-  unblocks=pkg:anyio==4.6.2.post1,pkg:requests==2.32.3,pkg:trio==0.27.0,pkg:trustme==1.2.0
#@check python -m pip show idna
if python3 -m pip install --break-system-packages --no-deps idna==3.10
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:idna==3.10" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.0.0  version=2.0.0  requires=-  unblocks=pkg:pytest==8.3.4
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jaraco-context==6.0.1  version=6.0.1  requires=pkg:backports-tarfile==1.2.0  unblocks=pkg:keyring==25.5.0
#@check python -m pip show jaraco-context
if python3 -m pip install --break-system-packages --no-deps jaraco-context==6.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jaraco-context==6.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jeepney==0.8.0  version=0.8.0  requires=-  unblocks=pkg:keyring==25.5.0,pkg:secretstorage==3.3.3
#@check python -m pip show jeepney
if python3 -m pip install --break-system-packages --no-deps jeepney==0.8.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jeepney==0.8.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown==3.7  version=3.7  requires=-  unblocks=pkg:mkautodoc==0.2.0,pkg:mkdocs-material==9.5.47,pkg:mkdocs==1.6.1,pkg:pymdown-extensions==10.12
#@check python -m pip show markdown
if python3 -m pip install --break-system-packages --no-deps markdown==3.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown==3.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markupsafe==3.0.2  version=3.0.2  requires=-  unblocks=pkg:jinja2==3.1.4,pkg:mkdocs==1.6.1
#@check python -m pip show markupsafe
if python3 -m pip install --break-system-packages --no-deps markupsafe==3.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markupsafe==3.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jinja2==3.1.4  version=3.1.4  requires=pkg:markupsafe==3.0.2  unblocks=pkg:mkdocs-material==9.5.47,pkg:mkdocs==1.6.1
#@check python -m pip show jinja2
if python3 -m pip install --break-system-packages --no-deps jinja2==3.1.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jinja2==3.1.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mdurl==0.1.2  version=0.1.2  requires=-  unblocks=pkg:markdown-it-py==3.0.0
#@check python -m pip show mdurl
if python3 -m pip install --break-system-packages --no-deps mdurl==0.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mdurl==0.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown-it-py==3.0.0  version=3.0.0  requires=pkg:mdurl==0.1.2  unblocks=pkg:rich==13.9.4
#@check python -m pip show markdown-it-py
if python3 -m pip install --break-system-packages --no-deps markdown-it-py==3.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown-it-py==3.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mergedeep==1.3.4  version=1.3.4  requires=-  unblocks=pkg:mkdocs-get-deps==0.2.0,pkg:mkdocs==1.6.1
#@check python -m pip show mergedeep
if python3 -m pip install --break-system-packages --no-deps mergedeep==1.3.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mergedeep==1.3.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mkautodoc==0.2.0  version=0.2.0  requires=aptdep:black,binary:pkg-config,pkg:markdown==3.7,tool:build-essential  build-from-source
#@check python -m pip show mkautodoc
if python3 -m pip install --break-system-packages --no-deps mkautodoc==0.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mkautodoc==0.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mkdocs-material-extensions==1.3.1  version=1.3.1  requires=-  unblocks=pkg:mkdocs-material==9.5.47
#@check python -m pip show mkdocs-material-extensions
if python3 -m pip install --break-system-packages --no-deps mkdocs-material-extensions==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mkdocs-material-extensions==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:more-itertools==10.5.0  version=10.5.0  requires=-  unblocks=pkg:jaraco-classes==3.4.0,pkg:jaraco-functools==4.1.0
#@check python -m pip show more-itertools
if python3 -m pip install --break-system-packages --no-deps more-itertools==10.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:more-itertools==10.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jaraco-classes==3.4.0  version=3.4.0  requires=pkg:more-itertools==10.5.0  unblocks=pkg:keyring==25.5.0
#@check python -m pip show jaraco-classes
if python3 -m pip install --break-system-packages --no-deps jaraco-classes==3.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jaraco-classes==3.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jaraco-functools==4.1.0  version=4.1.0  requires=pkg:more-itertools==10.5.0  unblocks=pkg:keyring==25.5.0
#@check python -m pip show jaraco-functools
if python3 -m pip install --break-system-packages --no-deps jaraco-functools==4.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jaraco-functools==4.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy-extensions==1.0.0  version=1.0.0  requires=-  unblocks=pkg:mypy==1.13.0,pkg:trio-typing==0.10.0
#@check python -m pip show mypy-extensions
if python3 -m pip install --break-system-packages --no-deps mypy-extensions==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy-extensions==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:nh3==0.2.19  version=0.2.19  requires=-  unblocks=pkg:readme-renderer==44.0
#@check python -m pip show nh3
if python3 -m pip install --break-system-packages --no-deps nh3==0.2.19
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:nh3==0.2.19" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:outcome==1.3.0.post0  version=1.3.0.post0  requires=pkg:attrs==24.2.0  unblocks=pkg:trio==0.27.0
#@check python -m pip show outcome
if python3 -m pip install --break-system-packages --no-deps outcome==1.3.0.post0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:outcome==1.3.0.post0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==24.2  version=24.2  requires=-  unblocks=pkg:build==1.2.2.post1,pkg:mkdocs==1.6.1,pkg:pytest==8.3.4,pkg:trio-typing==0.10.0,pkg:twine==6.0.1
#@check python -m pip show packaging
if python3 -m pip install --break-system-packages --no-deps packaging==24.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:packaging==24.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:paginate==0.5.7  version=0.5.7  requires=-  unblocks=pkg:mkdocs-material==9.5.47
#@check python -m pip show paginate
if python3 -m pip install --break-system-packages --no-deps paginate==0.5.7
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:paginate==0.5.7" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pathspec==0.12.1  version=0.12.1  requires=-  unblocks=pkg:mkdocs==1.6.1
#@check python -m pip show pathspec
if python3 -m pip install --break-system-packages --no-deps pathspec==0.12.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pathspec==0.12.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pkginfo==1.11.2  version=1.11.2  requires=-  unblocks=pkg:twine==6.0.1
#@check python -m pip show pkginfo
if python3 -m pip install --break-system-packages --no-deps pkginfo==1.11.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pkginfo==1.11.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:platformdirs==4.3.6  version=4.3.6  requires=-  unblocks=pkg:mkdocs-get-deps==0.2.0
#@check python -m pip show platformdirs
if python3 -m pip install --break-system-packages --no-deps platformdirs==4.3.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:platformdirs==4.3.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pluggy==1.5.0  version=1.5.0  requires=-  unblocks=pkg:pytest==8.3.4
#@check python -m pip show pluggy
if python3 -m pip install --break-system-packages --no-deps pluggy==1.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pluggy==1.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pycparser==2.22  version=2.22  requires=-  unblocks=pkg:cffi==1.17.1
#@check python -m pip show pycparser
if python3 -m pip install --break-system-packages --no-deps pycparser==2.22
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pycparser==2.22" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cffi==1.17.1  version=1.17.1  requires=pkg:pycparser==2.22  unblocks=pkg:cryptography==44.0.0,pkg:trio==0.27.0,pkg:zstandard==0.23.0
#@check python -m pip show cffi
if python3 -m pip install --break-system-packages --no-deps cffi==1.17.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cffi==1.17.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:cryptography==44.0.0  version=44.0.0  requires=pkg:cffi==1.17.1  unblocks=pkg:secretstorage==3.3.3,pkg:trustme==1.2.0
#@check python -m pip show cryptography
if python3 -m pip install --break-system-packages --no-deps cryptography==44.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:cryptography==44.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pygments==2.18.0  version=2.18.0  requires=-  unblocks=pkg:mkdocs-material==9.5.47,pkg:readme-renderer==44.0,pkg:rich==13.9.4
#@check python -m pip show pygments
if python3 -m pip install --break-system-packages --no-deps pygments==2.18.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pygments==2.18.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyproject-hooks==1.2.0  version=1.2.0  requires=-  unblocks=pkg:build==1.2.2.post1
#@check python -m pip show pyproject-hooks
if python3 -m pip install --break-system-packages --no-deps pyproject-hooks==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyproject-hooks==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:build==1.2.2.post1  version=1.2.2.post1  requires=pkg:colorama==0.4.6,pkg:packaging==24.2,pkg:pyproject-hooks==1.2.0
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
#@node pkg:pyyaml==6.0.2  version=6.0.2  requires=-  unblocks=pkg:mkdocs-get-deps==0.2.0,pkg:mkdocs==1.6.1,pkg:pymdown-extensions==10.12,pkg:pyyaml-env-tag==0.1
#@check python -m pip show pyyaml
if python3 -m pip install --break-system-packages --no-deps pyyaml==6.0.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml==6.0.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mkdocs-get-deps==0.2.0  version=0.2.0  requires=pkg:mergedeep==1.3.4,pkg:platformdirs==4.3.6,pkg:pyyaml==6.0.2  unblocks=pkg:mkdocs==1.6.1
#@check python -m pip show mkdocs-get-deps
if python3 -m pip install --break-system-packages --no-deps mkdocs-get-deps==0.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mkdocs-get-deps==0.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pymdown-extensions==10.12  version=10.12  requires=pkg:markdown==3.7,pkg:pyyaml==6.0.2  unblocks=pkg:mkdocs-material==9.5.47
#@check python -m pip show pymdown-extensions
if python3 -m pip install --break-system-packages --no-deps pymdown-extensions==10.12
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pymdown-extensions==10.12" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:pyyaml-env-tag==0.1  version=0.1  requires=pkg:pyyaml==6.0.2  unblocks=pkg:mkdocs==1.6.1
#@check python -m pip show pyyaml-env-tag
if python3 -m pip install --break-system-packages --no-deps pyyaml-env-tag==0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:pyyaml-env-tag==0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:readme-renderer==44.0  version=44.0  requires=pkg:docutils==0.21.2,pkg:nh3==0.2.19,pkg:pygments==2.18.0  unblocks=pkg:twine==6.0.1
#@check python -m pip show readme-renderer
if python3 -m pip install --break-system-packages --no-deps readme-renderer==44.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:readme-renderer==44.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:regex==2024.11.6  version=2024.11.6  requires=-  unblocks=pkg:mkdocs-material==9.5.47
#@check python -m pip show regex
if python3 -m pip install --break-system-packages --no-deps regex==2024.11.6
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:regex==2024.11.6" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rfc3986==2.0.0  version=2.0.0  requires=-  unblocks=pkg:twine==6.0.1
#@check python -m pip show rfc3986
if python3 -m pip install --break-system-packages --no-deps rfc3986==2.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rfc3986==2.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:rich==13.9.4  version=13.9.4  requires=pkg:markdown-it-py==3.0.0,pkg:pygments==2.18.0  unblocks=pkg:twine==6.0.1
#@check python -m pip show rich
if python3 -m pip install --break-system-packages --no-deps rich==13.9.4
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:rich==13.9.4" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ruff==0.8.1  version=0.8.1  requires=-
#@check python -m pip show ruff
if python3 -m pip install --break-system-packages --no-deps ruff==0.8.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ruff==0.8.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:secretstorage==3.3.3  version=3.3.3  requires=pkg:cryptography==44.0.0,pkg:jeepney==0.8.0  unblocks=pkg:keyring==25.5.0
#@check python -m pip show secretstorage
if python3 -m pip install --break-system-packages --no-deps secretstorage==3.3.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:secretstorage==3.3.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:six==1.16.0  version=1.16.0  requires=-  unblocks=pkg:python-dateutil==2.9.0.post0
#@check python -m pip show six
if python3 -m pip install --break-system-packages --no-deps six==1.16.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:six==1.16.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:python-dateutil==2.9.0.post0  version=2.9.0.post0  requires=pkg:six==1.16.0  unblocks=pkg:ghp-import==2.1.0
#@check python -m pip show python-dateutil
if python3 -m pip install --break-system-packages --no-deps python-dateutil==2.9.0.post0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:python-dateutil==2.9.0.post0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ghp-import==2.1.0  version=2.1.0  requires=pkg:python-dateutil==2.9.0.post0  unblocks=pkg:mkdocs==1.6.1
#@check python -m pip show ghp-import
if python3 -m pip install --break-system-packages --no-deps ghp-import==2.1.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ghp-import==2.1.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sniffio==1.3.1  version=1.3.1  requires=-  unblocks=pkg:anyio==4.6.2.post1,pkg:trio==0.27.0
#@check python -m pip show sniffio
if python3 -m pip install --break-system-packages --no-deps sniffio==1.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sniffio==1.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:anyio==4.6.2.post1  version=4.6.2.post1  requires=pkg:idna==3.10,pkg:sniffio==1.3.1
#@check python -m pip show anyio
if python3 -m pip install --break-system-packages --no-deps anyio==4.6.2.post1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:anyio==4.6.2.post1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:socksio==1.0.0  version=1.0.0  requires=-
#@check python -m pip show socksio
if python3 -m pip install --break-system-packages --no-deps socksio==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:socksio==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:sortedcontainers==2.4.0  version=2.4.0  requires=-  unblocks=pkg:trio==0.27.0
#@check python -m pip show sortedcontainers
if python3 -m pip install --break-system-packages --no-deps sortedcontainers==2.4.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:sortedcontainers==2.4.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:tomli==2.2.1  version=2.2.1  requires=-
#@check python -m pip show tomli
if python3 -m pip install --break-system-packages --no-deps tomli==2.2.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:tomli==2.2.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:trio==0.27.0  version=0.27.0  requires=pkg:attrs==24.2.0,pkg:cffi==1.17.1,pkg:idna==3.10,pkg:outcome==1.3.0.post0,pkg:sniffio==1.3.1,pkg:sortedcontainers==2.4.0  unblocks=pkg:trio-typing==0.10.0
#@check python -m pip show trio
if python3 -m pip install --break-system-packages --no-deps trio==0.27.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:trio==0.27.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:trustme==1.2.0  version=1.2.0  requires=pkg:cryptography==44.0.0,pkg:idna==3.10
#@check python -m pip show trustme
if python3 -m pip install --break-system-packages --no-deps trustme==1.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:trustme==1.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:typing-extensions==4.12.2  version=4.12.2  requires=-  unblocks=pkg:mypy==1.13.0,pkg:trio-typing==0.10.0
#@check python -m pip show typing-extensions
if python3 -m pip install --break-system-packages --no-deps typing-extensions==4.12.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:typing-extensions==4.12.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mypy==1.13.0  version=1.13.0  requires=pkg:mypy-extensions==1.0.0,pkg:typing-extensions==4.12.2
#@check python -m pip show mypy
if python3 -m pip install --break-system-packages --no-deps mypy==1.13.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mypy==1.13.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:urllib3==2.2.3  version=2.2.3  requires=-  unblocks=pkg:requests==2.32.3,pkg:twine==6.0.1
#@check python -m pip show urllib3
if python3 -m pip install --break-system-packages --no-deps urllib3==2.2.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:urllib3==2.2.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests==2.32.3  version=2.32.3  requires=pkg:certifi==2024.8.30,pkg:charset-normalizer==3.4.0,pkg:idna==3.10,pkg:urllib3==2.2.3  unblocks=pkg:mkdocs-material==9.5.47,pkg:requests-toolbelt==1.0.0,pkg:twine==6.0.1
#@check python -m pip show requests
if python3 -m pip install --break-system-packages --no-deps requests==2.32.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests==2.32.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:requests-toolbelt==1.0.0  version=1.0.0  requires=pkg:requests==2.32.3  unblocks=pkg:twine==6.0.1
#@check python -m pip show requests-toolbelt
if python3 -m pip install --break-system-packages --no-deps requests-toolbelt==1.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:requests-toolbelt==1.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:uvicorn==0.32.1  version=0.32.1  requires=pkg:click==8.1.7,pkg:h11==0.14.0
#@check python -m pip show uvicorn
if python3 -m pip install --break-system-packages --no-deps uvicorn==0.32.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:uvicorn==0.32.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:watchdog==6.0.0  version=6.0.0  requires=-  unblocks=pkg:mkdocs==1.6.1
#@check python -m pip show watchdog
if python3 -m pip install --break-system-packages --no-deps watchdog==6.0.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:watchdog==6.0.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mkdocs==1.6.1  version=1.6.1  requires=pkg:click==8.1.7,pkg:colorama==0.4.6,pkg:ghp-import==2.1.0,pkg:jinja2==3.1.4,pkg:markdown==3.7,pkg:markupsafe==3.0.2,pkg:mergedeep==1.3.4,pkg:mkdocs-get-deps==0.2.0,pkg:packaging==24.2,pkg:pathspec==0.12.1,pkg:pyyaml-env-tag==0.1,pkg:pyyaml==6.0.2,pkg:watchdog==6.0.0  unblocks=pkg:mkdocs-material==9.5.47
#@check python -m pip show mkdocs
if python3 -m pip install --break-system-packages --no-deps mkdocs==1.6.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mkdocs==1.6.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mkdocs-material==9.5.47  version=9.5.47  requires=pkg:babel==2.16.0,pkg:colorama==0.4.6,pkg:jinja2==3.1.4,pkg:markdown==3.7,pkg:mkdocs-material-extensions==1.3.1,pkg:mkdocs==1.6.1,pkg:paginate==0.5.7,pkg:pygments==2.18.0,pkg:pymdown-extensions==10.12,pkg:regex==2024.11.6,pkg:requests==2.32.3
#@check python -m pip show mkdocs-material
if python3 -m pip install --break-system-packages --no-deps mkdocs-material==9.5.47
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mkdocs-material==9.5.47" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:zipp==3.21.0  version=3.21.0  requires=-  unblocks=pkg:importlib-metadata==8.5.0
#@check python -m pip show zipp
if python3 -m pip install --break-system-packages --no-deps zipp==3.21.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:zipp==3.21.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:importlib-metadata==8.5.0  version=8.5.0  requires=pkg:zipp==3.21.0  unblocks=pkg:keyring==25.5.0,pkg:trio-typing==0.10.0
#@check python -m pip show importlib-metadata
if python3 -m pip install --break-system-packages --no-deps importlib-metadata==8.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:importlib-metadata==8.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:keyring==25.5.0  version=25.5.0  requires=pkg:importlib-metadata==8.5.0,pkg:jaraco-classes==3.4.0,pkg:jaraco-context==6.0.1,pkg:jaraco-functools==4.1.0,pkg:jeepney==0.8.0,pkg:secretstorage==3.3.3  unblocks=pkg:twine==6.0.1
#@check python -m pip show keyring
if python3 -m pip install --break-system-packages --no-deps keyring==25.5.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:keyring==25.5.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:trio-typing==0.10.0  version=0.10.0  requires=pkg:async-generator==1.10,pkg:importlib-metadata==8.5.0,pkg:mypy-extensions==1.0.0,pkg:packaging==24.2,pkg:trio==0.27.0,pkg:typing-extensions==4.12.2
#@check python -m pip show trio-typing
if python3 -m pip install --break-system-packages --no-deps trio-typing==0.10.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:trio-typing==0.10.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:twine==6.0.1  version=6.0.1  requires=pkg:keyring==25.5.0,pkg:packaging==24.2,pkg:pkginfo==1.11.2,pkg:readme-renderer==44.0,pkg:requests-toolbelt==1.0.0,pkg:requests==2.32.3,pkg:rfc3986==2.0.0,pkg:rich==13.9.4,pkg:urllib3==2.2.3
#@check python -m pip show twine
if python3 -m pip install --break-system-packages --no-deps twine==6.0.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:twine==6.0.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:zstandard==0.23.0  version=0.23.0  requires=pkg:cffi==1.17.1
#@check python -m pip show zstandard
if python3 -m pip install --break-system-packages --no-deps zstandard==0.23.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:zstandard==0.23.0" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:httpx  requires=pkg:anyio==4.6.2.post1,pkg:build==1.2.2.post1,pkg:certifi==2024.8.30,pkg:chardet==5.2.0,pkg:coverage==7.6.1,pkg:cryptography==44.0.0,pkg:httpcore==1.0.7,pkg:idna==3.10,pkg:mkautodoc==0.2.0,pkg:mkdocs-material==9.5.47,pkg:mkdocs==1.6.1,pkg:mypy==1.13.0,pkg:pytest==8.3.4,pkg:ruff==0.8.1,pkg:trio-typing==0.10.0,pkg:trio==0.27.0,pkg:trustme==1.2.0,pkg:twine==6.0.1,pkg:uvicorn==0.32.1
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:httpx" >> /tmp/v3_failed_nodes.log
fi
