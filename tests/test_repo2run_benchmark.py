from run_repo2run_benchmark import (
    classify_test_execution,
    render_eval_dockerfile,
)


def test_render_eval_dockerfile_injects_repo_copy_after_workdir():
    agent_dockerfile = """FROM python:3.11
WORKDIR /app

RUN pip install -e .
"""

    rendered = render_eval_dockerfile(agent_dockerfile)

    assert "WORKDIR /app\nCOPY . /app\n\nRUN pip install -e ." in rendered


def test_classify_test_execution_accepts_real_test_failures():
    result = classify_test_execution(
        {
            "returncode": 1,
            "timed_out": False,
            "stdout": "collected 10 items\n================ 9 passed, 1 failed in 0.42s ================\n",
            "stderr": "",
        }
    )

    assert result["effective"] is True
    assert result["reason"] == "tests_executed_with_failures"


def test_classify_test_execution_accepts_internal_repo_collection_failures():
    result = classify_test_execution(
        {
            "returncode": 2,
            "timed_out": False,
            "stdout": "============================= test session starts =============================\n"
            "collected 23 items / 1 error\n",
            "stderr": "ERROR collecting tests/test_app.py\n"
            "ImportError while importing test module\n"
            "ModuleNotFoundError: No module named 'src.common.missing_module'\n",
        },
        internal_import_prefixes={"src", "tests"},
    )

    assert result["effective"] is True
    assert result["reason"] == "tests_executed_with_collection_failures"


def test_classify_test_execution_rejects_external_dependency_collection_errors():
    result = classify_test_execution(
        {
            "returncode": 2,
            "timed_out": False,
            "stdout": "============================= test session starts =============================\n"
            "collected 23 items / 1 error\n",
            "stderr": "ERROR collecting tests/test_app.py\n"
            "ImportError while importing test module\n"
            "ModuleNotFoundError: No module named 'pandas'\n",
        },
        internal_import_prefixes={"src", "tests"},
    )

    assert result["effective"] is False
    assert result["reason"] == "collection_or_env_error"
