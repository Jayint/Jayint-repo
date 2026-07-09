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


def test_root_compose_unreferenced_is_ambiguous():
    assert compute_relevance(_decl("docker-compose.yml"), frozenset()) == "root_compose"
    assert compute_relevance(_decl("compose.yaml"), frozenset()) == "root_compose"


def test_nested_unreferenced_compose_is_lowest_confidence():
    # testcontainers/tests/core/compose_fixtures/basic/docker-compose.yaml
    d = _decl("tests/core/compose_fixtures/basic/docker-compose.yaml")
    assert compute_relevance(d, frozenset()) == "unreferenced_compose"
