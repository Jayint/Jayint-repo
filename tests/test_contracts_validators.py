# tests/test_contracts_validators.py
from src.envstate.contracts import validators
from src.envstate.contracts.graph import ContractGraph
from src.envstate.contracts.nodes import Node


def _exec_factory(results):
    def _exec(cmd):
        return results.get(cmd, (1, "not found"))
    return _exec


def test_runs_import_validator_and_marks_satisfied():
    g = ContractGraph(nodes=(
        Node("contract:python_package_importable:torch", "Contract",
             {"level": "atomic", "kind": "python_package_importable", "subject": "torch"}),
    ))
    # torch is not in the static map; use the actual command the validator will generate
    cmd, _ = validators._build_import_command("torch")
    ex = _exec_factory({cmd: (0, "")})
    nodes, edges, events = validators.run_confirmed_validators(g, ex, revision=4)
    assert any(n.type == "Validator" for n in nodes)
    assert any(n.type == "CommandExecution" and n.data["exit_code"] == 0 for n in nodes)
    assert any(e.type == "verified_by" for e in edges)
    ev = next(e for e in events if e.contract_id == "contract:python_package_importable:torch")
    assert ev.status == "satisfied"
    assert any(n.id in ev.evidence_ids for n in nodes if n.type == "CommandExecution")


def test_failing_import_marks_violated():
    g = ContractGraph(nodes=(
        Node("contract:python_package_importable:missing", "Contract",
             {"level": "atomic", "kind": "python_package_importable", "subject": "missing"}),
    ))
    ex = _exec_factory({})  # everything returns rc=1
    _n, _e, events = validators.run_confirmed_validators(g, ex, revision=4)
    assert events[0].status == "violated"


def test_unknown_kind_is_skipped():
    g = ContractGraph(nodes=(Node("contract:x", "Contract", {"level": "atomic", "kind": "mystery"}),))
    nodes, edges, events = validators.run_confirmed_validators(g, _exec_factory({}), revision=4)
    assert nodes == [] and edges == [] and events == []


# ---------------------------------------------------------------------------
# Bug 1: dist-name vs import-name resolution
# ---------------------------------------------------------------------------

def test_resolve_import_name_static_map():
    """The static map resolves the common offenders to their real import names."""
    assert validators.resolve_import_name("beautifulsoup4") == "bs4"
    assert validators.resolve_import_name("Jinja2") == "jinja2"
    assert validators.resolve_import_name("PyYAML") == "yaml"
    assert validators.resolve_import_name("scikit-learn") == "sklearn"


def test_validator_command_for_beautifulsoup4_uses_bs4():
    """beautifulsoup4 contract must call `import bs4`, not `import beautifulsoup4`."""
    g = ContractGraph(nodes=(
        Node(
            "contract:python_package_importable:beautifulsoup4",
            "Contract",
            {"level": "atomic", "kind": "python_package_importable", "subject": "beautifulsoup4"},
        ),
    ))
    cmd, import_name = validators._build_import_command("beautifulsoup4")
    assert import_name == "bs4"
    assert "bs4" in cmd
    assert "beautifulsoup4" not in cmd

    # Mock exec_readonly to succeed ONLY when the resolved name (bs4) is used
    ex = _exec_factory({cmd: (0, "")})
    _, _, events = validators.run_confirmed_validators(g, ex, revision=1)
    ev = next(e for e in events if "beautifulsoup4" in e.contract_id)
    assert ev.status == "satisfied"


_MICROSEARCH_DEPS = [
    "aiohttp", "beautifulsoup4", "fastapi", "feedparser",
    "Jinja2", "pandas", "pyarrow", "uvicorn",
]


def test_microsearch_deps_no_false_violations():
    """None of the microsearch deps are falsely violated when resolved imports succeed."""
    from src.envstate.contracts import ids as _ids

    contract_nodes = [
        Node(
            _ids.contract_id("python_package_importable", _ids.slug(dep) or dep),
            "Contract",
            {"level": "atomic", "kind": "python_package_importable", "subject": dep},
        )
        for dep in _MICROSEARCH_DEPS
    ]
    g = ContractGraph(nodes=tuple(contract_nodes))

    # Build mock using the actual commands the validator will generate
    mock_results: dict = {}
    for dep in _MICROSEARCH_DEPS:
        cmd, _ = validators._build_import_command(dep)
        mock_results[cmd] = (0, "")

    ex = _exec_factory(mock_results)
    _, _, events = validators.run_confirmed_validators(g, ex, revision=1)

    assert len(events) == len(_MICROSEARCH_DEPS), (
        f"Expected {len(_MICROSEARCH_DEPS)} events, got {len(events)}"
    )
    for ev in events:
        assert ev.status == "satisfied", f"{ev.contract_id} was falsely violated"
