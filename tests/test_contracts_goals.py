from src.envstate.contracts.goals import seed_backbone, BACKBONE_EDGES, GOAL_IDS, FOUNDATIONAL_IDS

def test_seed_emits_seven_goals_and_four_foundational():
    nodes, edges = seed_backbone()
    ids = {n.id for n in nodes}
    assert GOAL_IDS <= ids and FOUNDATIONAL_IDS <= ids
    assert len(GOAL_IDS) == 7 and len(FOUNDATIONAL_IDS) == 4

def test_top_goal_is_required_and_named_repo_tests_pass():
    nodes, _ = seed_backbone()
    top = next(n for n in nodes if n.id == "contract:goal:repo_tests_pass")
    assert top.data["level"] == "goal" and top.data["required"] is True

def test_no_per_dep_contracts_seeded():
    nodes, _ = seed_backbone()
    # backbone never mints python_import contracts at cold-start
    assert not any(n.data.get("kind") == "python_import" for n in nodes)

def test_backbone_edges_wire_tests_pass_to_phases():
    _, edges = seed_backbone()
    pairs = {(e.source, e.target) for e in edges}
    assert ("contract:goal:repo_tests_pass", "contract:goal:repo_imports_work") in pairs
    assert ("contract:goal:repo_deps_installed", "contract:package_manager_available") in pairs
