"""Contract Graph — a planner-facing reasoning layer inside WorldModelMap.

MIGRATION NOTE (concise-contract-graph v2): the eager package re-exports of
``refresh_host_graph`` / ``render_graph_for_planner`` / ``serialize_graph_for_maintainer``
are temporarily removed during the clean-break v2 rewrite so that importing an
individual contracts submodule does not drag in not-yet-migrated siblings
(``projection``/``render``). All in-tree callers import these from their submodules
directly. The package-level re-exports are restored in the final integration task.
"""
