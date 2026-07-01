import json
import sys
from pathlib import Path


RUNANYTHING_SRC = Path(__file__).resolve().parents[1] / "runanything" / "src"
if str(RUNANYTHING_SRC) not in sys.path:
    sys.path.insert(0, str(RUNANYTHING_SRC))

from eval.models.dockeragent_model import (  # noqa: E402
    _agent_run_summary_warning,
    _validate_agent_run_summary,
    _write_docker_build_artifacts,
)


def test_validate_agent_run_summary_requires_bundle_but_not_config_success(tmp_path):
    summary_path = tmp_path / "agent_run_summary.json"

    assert (
        _validate_agent_run_summary(
            {"configuration_success": True, "verification_bundle": {"test_commands": ["pytest"]}},
            summary_path,
        )
        is None
    )
    assert (
        _validate_agent_run_summary(
            {"configuration_success": False, "verification_bundle": {"test_commands": ["pytest"]}},
            summary_path,
        )
        is None
    )
    assert "verification_bundle" in _validate_agent_run_summary(
        {"configuration_success": True},
        summary_path,
    )


def test_agent_run_summary_warning_flags_unsuccessful_configuration():
    assert _agent_run_summary_warning({"configuration_success": True}) is None
    assert "configuration_success" in _agent_run_summary_warning(
        {"configuration_success": False}
    )


def test_write_docker_build_artifacts_records_command_and_streams(tmp_path):
    log_path = tmp_path / "docker_build.log"
    result_path = tmp_path / "docker_build_result.json"

    _write_docker_build_artifacts(
        log_path=log_path,
        result_path=result_path,
        command=["docker", "build", "-t", "image", "."],
        returncode=17,
        stdout="build stdout\n",
        stderr="build stderr\n",
        timed_out=False,
    )

    log_text = log_path.read_text(encoding="utf-8")
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert "$ docker build -t image ." in log_text
    assert "build stderr" in log_text
    assert result["returncode"] == 17
    assert result["stderr"] == "build stderr\n"
