#!/usr/bin/env bash
#
# setup.sh — COMPILED from the certified dependency graph. DO NOT EDIT.
# Edit the graph and re-render; this file is an artifact, not a source.
#
#   nodes: 30 reciped (30 pip) + 0 needs
#   graph-hash: sha256:e6cafd5d1bd5   python: 3.11   platform: aarch64-manylinux_2_28
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
#@node pkg:attr==0.3.2  version=0.3.2  requires=-
#@check python -m pip show attr
if python3 -m pip install --break-system-packages --no-deps attr==0.3.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:attr==0.3.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:comm==0.2.3  version=0.2.3  requires=-  unblocks=pkg:ipywidgets==8.1.8
#@check python -m pip show comm
if python3 -m pip install --break-system-packages --no-deps comm==0.2.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:comm==0.2.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:decorator==5.3.1  version=5.3.1  requires=-  unblocks=pkg:ipython==9.15.0
#@check python -m pip show decorator
if python3 -m pip install --break-system-packages --no-deps decorator==5.3.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:decorator==5.3.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:executing==2.2.1  version=2.2.1  requires=-  unblocks=pkg:stack-data==0.6.3
#@check python -m pip show executing
if python3 -m pip install --break-system-packages --no-deps executing==2.2.1
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:executing==2.2.1" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:iniconfig==2.3.0  version=2.3.0  requires=-  unblocks=pkg:pytest==9.1.1
#@check python -m pip show iniconfig
if python3 -m pip install --break-system-packages --no-deps iniconfig==2.3.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:iniconfig==2.3.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:jupyterlab-widgets==3.0.16  version=3.0.16  requires=-  unblocks=pkg:ipywidgets==8.1.8
#@check python -m pip show jupyterlab-widgets
if python3 -m pip install --break-system-packages --no-deps jupyterlab-widgets==3.0.16
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:jupyterlab-widgets==3.0.16" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:mdurl==0.1.2  version=0.1.2  requires=-  unblocks=pkg:markdown-it-py==4.2.0
#@check python -m pip show mdurl
if python3 -m pip install --break-system-packages --no-deps mdurl==0.1.2
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:mdurl==0.1.2" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:markdown-it-py==4.2.0  version=4.2.0  requires=pkg:mdurl==0.1.2
#@check python -m pip show markdown-it-py
if python3 -m pip install --break-system-packages --no-deps markdown-it-py==4.2.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:markdown-it-py==4.2.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:packaging==26.2  version=26.2  requires=-  unblocks=pkg:pytest==9.1.1
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
#@node pkg:pluggy==1.6.0  version=1.6.0  requires=-  unblocks=pkg:pytest==9.1.1
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
#@node pkg:stack-data==0.6.3  version=0.6.3  requires=pkg:asttokens==3.0.2,pkg:executing==2.2.1,pkg:pure-eval==0.2.3  unblocks=pkg:ipython==9.15.0
#@check python -m pip show stack-data
if python3 -m pip install --break-system-packages --no-deps stack-data==0.6.3
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:stack-data==0.6.3" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:traitlets==5.15.1  version=5.15.1  requires=-  unblocks=pkg:ipython==9.15.0,pkg:ipywidgets==8.1.8,pkg:matplotlib-inline==0.2.2
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
#@node pkg:ipython==9.15.0  version=9.15.0  requires=pkg:decorator==5.3.1,pkg:ipython-pygments-lexers==1.1.1,pkg:jedi==0.20.0,pkg:matplotlib-inline==0.2.2,pkg:pexpect==4.9.0,pkg:prompt-toolkit==3.0.52,pkg:psutil==7.2.2,pkg:pygments==2.20.0,pkg:stack-data==0.6.3,pkg:traitlets==5.15.1,pkg:typing-extensions==4.16.0  unblocks=pkg:ipywidgets==8.1.8
#@check python -m pip show ipython
if python3 -m pip install --break-system-packages --no-deps ipython==9.15.0
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ipython==9.15.0" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:widgetsnbextension==4.0.15  version=4.0.15  requires=-  unblocks=pkg:ipywidgets==8.1.8
#@check python -m pip show widgetsnbextension
if python3 -m pip install --break-system-packages --no-deps widgetsnbextension==4.0.15
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:widgetsnbextension==4.0.15" >> /tmp/v3_failed_nodes.log
fi
#@node pkg:ipywidgets==8.1.8  version=8.1.8  requires=pkg:comm==0.2.3,pkg:ipython==9.15.0,pkg:jupyterlab-widgets==3.0.16,pkg:traitlets==5.15.1,pkg:widgetsnbextension==4.0.15
#@check python -m pip show ipywidgets
if python3 -m pip install --break-system-packages --no-deps ipywidgets==8.1.8
then
    :
else
    echo "V3_NODE_INSTALL_FAILED pkg:ipywidgets==8.1.8" >> /tmp/v3_failed_nodes.log
fi

# ==================== PROJECT (editable) ====================
#@node project:rich  requires=pkg:ipywidgets==8.1.8,pkg:markdown-it-py==4.2.0,pkg:pygments==2.20.0
if python3 -m pip install --break-system-packages --no-deps -e . || python3 -m pip install --break-system-packages --no-deps .
then
    :
else
    echo "V3_NODE_INSTALL_FAILED project:rich" >> /tmp/v3_failed_nodes.log
fi
