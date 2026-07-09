import textwrap

from python_deps.depgraph.service_relevance import (
    ci_referenced_compose_files, compute_relevance,
)
from python_deps.depgraph.service_sources import RawDeclaration


def _write(tmp_path, rel, src):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(src))


def _decl(file, kind="compose"):
    return RawDeclaration("db", {"image": "postgres:16"}, file, "services.db", kind, "")


def test_ci_referenced_compose_files_finds_both_spellings(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f tests/db/compose.yml up -d
              - run: docker-compose --file docker-compose.dev.yml up
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"tests/db/compose.yml", "docker-compose.dev.yml"})


def test_multiple_dash_f_in_one_command_are_all_referenced(tmp_path):
    # `docker compose -f base.yml -f override.yml up` names TWO environments in
    # one invocation; capturing only the first would demote the override.
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"docker-compose.yml", "docker-compose.ci.yml"})


def test_ci_service_declarations_are_intrinsically_relevant(tmp_path):
    d = _decl(".github/workflows/ci.yml", kind="ci")
    assert compute_relevance(d, frozenset()) == "ci_service"


def test_a_tests_dir_compose_named_by_CI_is_the_test_environment(tmp_path):
    # mlflow/tests/db/compose.yml — a path heuristic would WRONGLY drop this.
    d = _decl("tests/db/compose.yml")
    assert compute_relevance(d, frozenset({"tests/db/compose.yml"})) == "ci_referenced_compose"


def test_reference_paths_are_normalized_not_char_stripped(tmp_path):
    """`lstrip("./")` is character stripping. `../compose.yml` must NOT match a root
    declaration, and `deploy/../compose.yml` MUST normalize to `compose.yml`."""
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f ./deploy/../docker-compose.yml up
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"docker-compose.yml"})
    assert compute_relevance(_decl("docker-compose.yml"), refs) == "ci_referenced_compose"


def test_a_reference_escaping_the_repo_is_not_a_reference(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose -f ../outside/docker-compose.yml up
    """)
    assert ci_referenced_compose_files(str(tmp_path)) == frozenset()


def test_only_run_step_bodies_are_scanned(tmp_path):
    """Scanning raw file text would turn a step NAME into a compose reference."""
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - name: docker compose -f decoy.yml up
                uses: actions/checkout@v4
    """)
    assert ci_referenced_compose_files(str(tmp_path)) == frozenset()


def test_equals_form_and_quoted_paths(tmp_path):
    _write(tmp_path, ".github/workflows/ci.yml", """
        jobs:
          t:
            steps:
              - run: docker compose --file=a.yml up
              - run: docker compose -f "dir with space/b.yml" up
    """)
    refs = ci_referenced_compose_files(str(tmp_path))
    assert refs == frozenset({"a.yml", "dir with space/b.yml"})


def test_root_override_compose_is_the_default_environment():
    """`docker compose up` with no -f auto-loads docker-compose.override.yml."""
    assert compute_relevance(_decl("docker-compose.override.yml"), frozenset()) == "root_compose"


def test_root_compose_unreferenced_is_ambiguous():
    assert compute_relevance(_decl("docker-compose.yml"), frozenset()) == "root_compose"
    assert compute_relevance(_decl("compose.yaml"), frozenset()) == "root_compose"


def test_nested_unreferenced_compose_is_lowest_confidence():
    # testcontainers/tests/core/compose_fixtures/basic/docker-compose.yaml
    d = _decl("tests/core/compose_fixtures/basic/docker-compose.yaml")
    assert compute_relevance(d, frozenset()) == "unreferenced_compose"
