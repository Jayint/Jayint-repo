graph = initial_depgraph
plan = compile_build_blocks(graph)

while True:
    node = next_missing_node(graph)
    result = execute_block(plan.block_for(node))

    if host_check(node):
        graph = certify(node)
        maybe_create_checkpoint(node)
        continue

    context = build_execution_context(node, result, graph, plan)
    action = execute_agent.next_action(context)

    if action.type == "probe":
        observation = run_readonly_probe(action)
        continue

    if action.type == "patch":
        patch = patch_gate.validate(action.patch)
        graph = apply_patch(graph, patch)
        plan = compile_build_blocks(graph)
        restore_nearest_valid_checkpoint(graph, patch)
        continue

    if action.type == "non_environment":
        return GIVE_UP

    if final_test_gate_passes(graph):
        break

final_script = render_build_script(graph)
fresh_replay_from_base(final_script)