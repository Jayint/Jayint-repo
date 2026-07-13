"""Unit tests for the graph_quality corpus parser (plan Task 2, Step 1).

These fixtures are hand-built on purpose -- this is the one place hand-built
fixtures are right, because we are testing the parser itself, not the system.
The real-corpus smoke check lives in `corpus.py`'s `--smoke` entry point.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.eval.graph_quality.corpus import (
    Label, _failure, dockerfile_installs, label_for, load_pairs,
)


def _failure_text(payload):
    return _failure(payload)[0]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESULTS = _REPO_ROOT / "outputs" / "repo2run_benchmark" / "results"
_ARTIFACTS = _REPO_ROOT / "outputs" / "repo2run_benchmark" / "eval_artifacts"


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


def test_dockerfile_installs_sees_apt_flags_BEFORE_the_verb():
    """`apt-get -y --no-install-recommends install libgomp1` is real (nlmatics__nlm-ingestor).

    A parser anchored on `apt-get install` misses it -- and a missed apt name does not surface
    as an error, it silently demotes the pair to `not-an-env-fix` and DELETES it from the
    eval's denominator. With only a handful of os-package pairs in the whole corpus, one
    dropped name is a double-digit error on that slice.
    """
    apt, _pip = dockerfile_installs("RUN apt-get -y --no-install-recommends install libgomp1\n")
    assert apt == {"libgomp1"}


def test_dockerfile_installs_does_not_mistake_no_install_recommends_FOR_the_verb():
    # `--no-install-recommends` contains the word `install`. A sloppier regex matches it and
    # then reads the flag's own tail as package names.
    apt, _pip = dockerfile_installs("RUN apt-get -y --no-install-recommends update\n")
    assert apt == frozenset()


# --------------------------------------------------------------------------- #
# the measuring path: _failure_text / load_pairs
#
# Terra's review: nothing here touched load_pairs, so a stub returning [] -- or one reading
# the FORBIDDEN observation_summary -- passed the whole suite. These tests make the
# instrument itself falsifiable.
# --------------------------------------------------------------------------- #

_POISON = "[SYSTEM] The build failed because a required package could not be installed."


def test_failure_text_reads_the_BUILD_STDERR_when_the_build_failed():
    payload = {
        "docker_build": {"returncode": 1, "stderr": "E: Unable to locate package libpq-dev\n"},
        "observation_summary": _POISON,
    }
    assert "Unable to locate package" in _failure_text(payload)


def test_failure_text_falls_back_to_PYTEST_OUTPUT_when_the_build_SUCCEEDED():
    """Half this corpus fails at test time, not build time. For those rounds the build log is
    clean and carries no error signal at all -- reading only docker_build.stderr would feed the
    replay an empty string and score a false zero."""
    payload = {
        "docker_build": {"returncode": 0, "stderr": ""},
        "test_execution": [{"returncode": 2,
                            "stdout": "ModuleNotFoundError: No module named 'yaml'\n",
                            "stderr": ""}],
    }
    assert "No module named 'yaml'" in _failure_text(payload)


def test_failure_text_NEVER_returns_repo2runs_observation_summary():
    """THE trap (spec §2.1). observation_summary is repo2run's own [SYSTEM] wrapper prose.
    An eval that replays it measures THEIR message templates, not the graph. This test is the
    regression guard: it poisons every payload shape with prose and demands it never appear."""
    shapes = [
        {"docker_build": {"returncode": 1, "stderr": "real stderr\n"},
         "observation_summary": _POISON},
        {"docker_build": {"returncode": 0, "stderr": ""},
         "test_execution": [{"returncode": 1, "stdout": "real pytest output\n", "stderr": ""}],
         "observation_summary": _POISON},
        {"docker_build": {"returncode": 0, "stderr": ""}, "observation_summary": _POISON},
    ]
    for payload in shapes:
        assert "[SYSTEM]" not in _failure_text(payload)


def test_failure_text_strips_repo2runs_own_SENTINEL_from_captured_output():
    # `__REPO2RUN_TEST_EXIT_CODE__=2` is echoed by the harness, not emitted by the container.
    payload = {
        "docker_build": {"returncode": 0, "stderr": ""},
        "test_execution": [{"returncode": 2,
                            "stdout": "E   ImportError: cannot import name 'x'\n"
                                      "__REPO2RUN_TEST_EXIT_CODE__=2\n",
                            "stderr": ""}],
    }
    out = _failure_text(payload)
    assert "ImportError" in out
    assert "__REPO2RUN" not in out


def _write_fake_corpus(tmp: Path) -> tuple[Path, Path]:
    results, artifacts = tmp / "results", tmp / "eval_artifacts"
    (artifacts / "acme__widget").mkdir(parents=True)
    results.mkdir()
    before = "FROM python:3.10\nRUN pip install psycopg2==2.9.12\n"
    after = before + "RUN apt-get install -y libpq-dev\n"
    (results / "acme__widget.json").write_text(json.dumps({
        "dockerfile_repair_rounds": [
            {"round": 1, "dockerfile_text": after},
            {"round": 2, "source": "llm_error", "dockerfile_text": ""},   # must be SKIPPED
        ]
    }))
    (artifacts / "acme__widget" / "dockerfile_repair_round_1.md").write_text(
        "## Prompt\nInput JSON:\n" + json.dumps({
            "dockerfile": before,
            "docker_build": {"returncode": 1,
                             "stderr": "Error: pg_config executable not found.\n"},
            "observation_summary": _POISON,
        }) + "\n")
    return results, artifacts


def test_load_pairs_builds_a_labelled_pair_end_to_end(tmp_path):
    """A stub `load_pairs` returning [] used to pass every test in this file."""
    results, artifacts = _write_fake_corpus(tmp_path)
    pairs = load_pairs(str(results), str(artifacts))

    assert len(pairs) == 1, "the llm_error round (no dockerfile_text) must not become a Pair"
    p = pairs[0]
    assert p.repo == "acme__widget" and p.round_index == 1
    assert "pg_config executable not found" in p.stderr
    assert "[SYSTEM]" not in p.stderr
    assert p.label.kind == "os-package" and p.label.apt == {"libpq-dev"}


def test_failure_reports_WHICH_STREAM_the_error_came_from():
    """`enrich` ingests a build failure and a pytest failure through two mutually exclusive
    streams. The replay has to know which one it is holding -- routing a pytest failure down the
    build path skips the phase-gated cause ingestion entirely and manufactures a false miss. Only
    the corpus still knows, so it must say."""
    build = {"docker_build": {"returncode": 1, "stderr": "E: no such package\n"}}
    test = {"docker_build": {"returncode": 0, "stderr": ""},
            "test_execution": [{"returncode": 2, "stdout": "ModuleNotFoundError\n", "stderr": ""}]}
    assert _failure(build)[1] == "build"
    assert _failure(test)[1] == "test"


def test_an_ENV_VAR_repair_is_its_own_kind_not_a_source_patch():
    """`ENV DATASET=webarena` IS an environment fact -- the graph models it as a Config node.

    Filing it under not-an-env-fix did damage in BOTH directions: it hid the pair from the
    eval's denominator, and it turned the graph's CORRECT Config discovery into a counted
    "false positive" on the negative control. (Real shape: fructose, search-agents,
    visualwebarena. 25 pairs in this corpus -- not a rounding error.)"""
    before = "FROM python:3.10\nRUN pip install torch\n"
    after = before + "ENV DATASET=webarena\n"
    lab = label_for(before, after)
    assert lab.kind == "env-var"
    assert lab.env == {"DATASET"}


def test_a_package_install_still_OUTRANKS_an_env_var_in_the_same_repair():
    # A repair that adds BOTH is labelled by the package: that is the part the graph can pre-empt.
    before = "FROM python:3.10\n"
    after = "FROM python:3.10\nENV FOO=bar\nRUN apt-get install -y libpq-dev\n"
    assert label_for(before, after).kind == "os-package"


def test_a_real_source_patch_is_STILL_not_an_env_fix():
    # The negative control must stay a real negative control.
    before = "FROM python:3.10\nRUN pip install torch\n"
    after = before + "RUN printf '%s' 'import sys' > tests/conftest.py\n"
    assert label_for(before, after).kind == "not-an-env-fix"


@pytest.mark.skipif(not _RESULTS.is_dir(), reason="repo2run_benchmark corpus not on disk")
def test_load_pairs_against_the_REAL_corpus_is_nonempty_and_prose_free():
    """Guards the instrument against the corpus itself moving underneath it: if a refactor or a
    data reshuffle makes the parser silently stop finding rounds, the eval would report a
    perfect score over zero pairs."""
    pairs = load_pairs(str(_RESULTS), str(_ARTIFACTS))
    assert len(pairs) > 100, f"expected ~111 pairs, got {len(pairs)}"
    assert all(p.stderr.strip() for p in pairs), "a pair with no error text scores a false zero"
    assert not [p for p in pairs if "[SYSTEM]" in p.stderr], "observation_summary prose leaked in"
    assert not [p for p in pairs if "__REPO2RUN" in p.stderr], "harness sentinel leaked in"
    assert {p.label.kind for p in pairs} <= {"os-package", "python-package", "env-var",
                                             "not-an-env-fix"}
    assert {p.failed_at for p in pairs} <= {"build", "test"}
    assert any(p.failed_at == "test" for p in pairs), (
        "half this corpus fails at TEST time; if none are tagged so, the replay routes them all "
        "down the build stream and silently scores false misses"
    )
