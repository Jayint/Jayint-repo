# Failure Analysis — py2many/py2many

**Harness status**: success | **True outcome**: pass_strong | **Category**: deps_installed_correctly | **Pytest**: pass_rate=0.813, total_tests=1603, passed=187, failed=43, errors=0

## Root cause

The environment is substantially complete and correctly configured. The 0.813 pass rate is driven by the nature of the repository itself—py2many is a Python-to-many-languages transpiler with expected failures in code-generation tests (38 AssertionErrors from generated Kotlin/C++/Rust mismatches), plus one legitimate missing test-only dependency (`adt` module imported by tests/cases/sealed.py) and minor test errors (NameError, OtherError).

## Environment / trajectory state at termination

**Steps used**: 10 (agent concluded at step 11 with final verification)

**Installed successfully**:
- Base Python 3.12 environment
- py2many package itself (editable: `pip install -e /testbed`)
- Test extra: `py2many[test]` (tree-sitter, tree-sitter-cpp, tree-sitter-rust, argparse_dataclass, black, pytest, pytest-cov, jgo, astpretty)
- All transitive test dependencies

**Missing / Incomplete**:
- `adt` module: imported by tests/cases/sealed.py (line 5: `from adt import adt as sealed`), not declared in py2many's test extras. This is a legitimate test-only dependency that should have been in the package's setup.py extras.
- V-lang formatter: errors about missing 'v' binary (FileNotFoundError), but these are non-fatal formatting checks during test runs, not test-collection blockers.

**Last action**: Step 10 successfully ran `pytest --collect-only -q --disable-warnings`, collecting all 1603 tests without errors. Step 11 concluded with verification: 0 runtime-prep commands, 1 test command committed, environment marked complete.

## Key evidence

From the digest:

```
"pytest_pass_rate": 0.813,
"pytest_total_tests": 1603,
"pytest_passed": 187,
"pytest_failed": 43,
"pytest_errors": 0,
"error_breakdown": {
  "AssertionError": 38,
  "ModuleNotFoundError": 1,
  "OtherError": 2,
  "NameError": 2
}
```

ModuleNotFoundError detail:
```
File "/testbed/tests/cases/sealed.py", line 5, in <module>
  from adt import adt as sealed
ModuleNotFoundError: No module named 'adt'
```

Failed tests are primarily code-generation mismatches:
```
tests.test_cli.TestCodeGenerator::test_generated[lambda-kotlin] - AssertionError
tests.test_cli.TestCodeGenerator::test_generated[bitops-kotlin] - AssertionError
...
tests.test_transpiler_cpp.py::test_void_function - AssertionError: assert
```

## Takeaway for DockerAgent

This is a **success case**. The environment was correctly synthesized, all core and test dependencies installed, tests collected without blockers, and the agent cleanly concluded. The 81.3% pass rate is genuine environment completeness (0 collection errors, working pytest), with failures rooted in the target repository's transpiler logic and test expectations, not missing runtime dependencies or misconfiguration. The single ModuleNotFoundError for `adt` reflects a legitimate gap in the upstream package's test_extra declaration, not a failure of DockerAgent to discover or install what was explicitly declared.

## Fixability

**already_working** — The harness produced a working, reproducible Dockerfile; tests execute and collect successfully; the majority of failures are code issues in the transpiler under test (Kotlin, C++, Rust output mismatches), not environment misconfigurations. The `adt` import failure is a package-level oversight unrelated to DockerAgent's synthesis.
