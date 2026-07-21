from src.envstate.done_gate import verified_test_command_passed


def test_non_python_test_summaries_satisfy_the_same_anti_hollow_gate():
    assert verified_test_command_passed(
        "go test ./...",
        0,
        "ok  example.com/demo  0.014s\n",
    )
    assert verified_test_command_passed(
        "cargo test --all-targets",
        0,
        "running 2 tests\n"
        "test one ... ok\n"
        "test two ... ok\n"
        "test result: ok. 2 passed; 0 failed; 0 ignored\n",
    )
    assert verified_test_command_passed(
        "./mvnw -B test",
        0,
        "Tests run: 3, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS\n",
    )
    assert verified_test_command_passed(
        "npm test",
        0,
        "1 test passed\n",
    )


def test_polyglot_gate_rejects_empty_go_test_run():
    assert not verified_test_command_passed(
        "go test ./...",
        0,
        "?  example.com/demo  [no test files]\n",
    )


def test_grunt_validation_suite_satisfies_polyglot_gate():
    output = (
        'Running "eslint:all" (eslint) task\n'
        "✖ 89 problems (0 errors, 89 warnings)\n\n"
        'Running "stylelint:all" (stylelint) task\n'
        ">> Linted 19 files without errors\n\n"
        'Running "banana:UploadWizard" (banana) task\n'
        ">> 2 message directories checked.\n\n"
        "Done.\n"
    )

    assert verified_test_command_passed("npm test", 0, output)


def test_grunt_gate_requires_task_and_terminal_success_markers():
    assert not verified_test_command_passed("npm test", 0, "Done.\n")
    assert not verified_test_command_passed(
        "npm test",
        0,
        'Running "eslint:all" (eslint) task\n',
    )
