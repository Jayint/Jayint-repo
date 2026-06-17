from src.envstate.contracts import ids


def test_slug_lowercases_and_replaces_unsafe():
    assert ids.slug("Torch >= 2.0") == "torch-2-0"
    assert ids.slug("tests/unit") == "tests-unit"


def test_id_builders():
    assert ids.artifact_id("requirements.txt") == "artifact:requirements.txt"
    assert ids.requirement_id("python_dependency", "torch") == "requirement:python_dependency:torch"
    assert ids.contract_id("python_package_importable", "torch") == "contract:python_package_importable:torch"
    assert ids.goal_contract_id("repo_tests_run") == "contract:goal:repo_tests_run"
    assert ids.capability_id("python_package_importable", "torch", 4) == "capability:python_package_importable:torch@envrev:004"
    assert ids.command_id(17) == "cmd:017"
    assert ids.revision_id(4) == "envrev:004"
    assert ids.transition_id("install_python_package", "torch") == "transition:install_python_package:torch"
    assert ids.validator_id("python_import_check", "torch") == "validator:python_import_check:torch"
    assert ids.verification_target_id("pytest_run") == "verify:pytest_run"
    assert ids.open_problem_id("ModuleNotFoundError: torch") == "openproblem:modulenotfounderror-torch"


def test_failure_id_uses_command_and_kind():
    assert ids.failure_id(17, "module_not_found", "torch") == "failure:cmd017:module_not_found:torch"
