from typing import Dict, Iterable, List

from src.planning.schemas import TaskNode


DEFAULT_ACTION_DESCRIPTIONS = {
    "runtime": "Select the runtime or base image before sandbox execution.",
    "system_package": "Prepare system packages if evidence or failures make them necessary.",
    "package_manager": "Prepare the project package manager.",
    "install_strategy": "Choose the dependency installation strategy before running high-risk setup commands.",
    "language_dependency": "Install language-level dependencies.",
    "project_build": "Prepare the project build or source-tree execution mode.",
    "service": "Prepare a required local service.",
    "env_var": "Prepare required environment variables.",
    "asset": "Prepare required data, browser, model, or fixture assets.",
    "runtime_command": "Run or inspect the project runtime command when needed.",
    "verification": "Run the repository verification command after setup.",
}


class TodoListGenerator:
    def generate(
        self,
        ordered_node_ids: Iterable[str],
        nodes: Iterable[TaskNode],
    ) -> List[Dict[str, object]]:
        node_map = {node.id: node for node in nodes}
        todo_items = []

        for index, node_id in enumerate(ordered_node_ids, start=1):
            node = node_map[node_id]
            todo_items.append(
                {
                    "step": index,
                    "node_id": node.id,
                    "task_type": node.type,
                    "description": DEFAULT_ACTION_DESCRIPTIONS.get(
                        node.type,
                        "Use this planned node as advisory setup context.",
                    ),
                    "command_hint": node.command_hint,
                    "command_hint_is_advisory": True,
                    "evidence": list(node.evidence),
                    "confidence": node.confidence,
                    "notes": [],
                }
            )

        return todo_items
