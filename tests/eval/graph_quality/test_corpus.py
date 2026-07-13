"""Unit tests for the graph_quality corpus parser (plan Task 2, Step 1).

These fixtures are hand-built on purpose -- this is the one place hand-built
fixtures are right, because we are testing the parser itself, not the system.
The real-corpus smoke check lives in `corpus.py`'s `--smoke` entry point.
"""
from __future__ import annotations

from src.eval.graph_quality.corpus import Label, dockerfile_installs, label_for


def test_dockerfile_installs_extracts_apt_and_pip_names():
    text = (
        "FROM python:3.10\n"
        "RUN apt-get update && apt-get install -y libpq-dev pkg-config\n"
        "RUN pip install psycopg2==2.9.12 asyncpg\n"
    )
    apt, pip = dockerfile_installs(text)
    assert apt == {"libpq-dev", "pkg-config"}
    assert pip == {"psycopg2", "asyncpg"}


def test_dockerfile_installs_ignores_apt_get_update_and_flags():
    apt, _ = dockerfile_installs("RUN apt-get update && apt-get install -y --no-install-recommends git\n")
    assert apt == {"git"}          # not {"update", "-y", "--no-install-recommends"}


def test_dockerfile_installs_survives_the_real_corpus_retry_wrapper():
    # The corpus wraps every install in a JAYINT_PIP_ATTEMPT retry loop with the real
    # command buried inside `/bin/sh -lc '...'`. A naive line parser finds NOTHING
    # here, and the eval would score 0% pre-emption while reporting itself healthy.
    text = (
        "RUN JAYINT_PIP_ATTEMPT=1; while [ ... ]; do PIP_NO_CACHE_DIR=1 /bin/sh -lc "
        "'pip install torch==2.7.0 einops omegaconf' && JAYINT_PIP_STATUS=0 && break; done\n"
    )
    _apt, pip = dockerfile_installs(text)
    assert pip == {"torch", "einops", "omegaconf"}


def test_label_of_a_source_patch_is_NOT_AN_ENV_FIX():
    # Much of this corpus is heavy-ML repos whose fixes are `write a conftest.py that
    # mocks the triton driver` or `git clone mamba && pip install .`. Those are source
    # patches, not environment facts, and the graph is RIGHT to model none of them.
    # Counting them as misses would punish it for correctly declining to hallucinate.
    before = "FROM python:3.10\nRUN pip install torch\n"
    after = before + "RUN printf '%s' 'BASE64==' | base64 -d > tests/conftest.py\n"
    assert label_for(before, after).kind == "not-an-env-fix"


def test_label_of_an_added_apt_package_is_OS_PACKAGE():
    before = "FROM python:3.10\nRUN pip install psycopg2\n"
    after = "FROM python:3.10\nRUN apt-get install -y libpq-dev\nRUN pip install psycopg2\n"
    lab = label_for(before, after)
    assert lab.kind == "os-package"
    assert lab.apt == {"libpq-dev"}


def test_label_ignores_packages_that_were_ALREADY_there():
    # Only ADDITIONS are the label. A package present before and after did not fix anything.
    before = "RUN pip install torch\n"
    after = "RUN pip install torch\nRUN pip install einops\n"
    assert label_for(before, after).pip == {"einops"}
