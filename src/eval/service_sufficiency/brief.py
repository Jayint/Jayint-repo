"""Project a ServiceNode into the agent-facing brief (spec §4.3 / §6).

This is the same projection that becomes ``service_graph_context(graph)`` in the
react-arm plan -- keep it reusable.
"""
from __future__ import annotations

# The install constraint is a property of the ENVIRONMENT (a Debian container with
# no third-party sources), NOT evidence about the service. Per correction D1 it must
# be identical across every condition -- C0 included -- so a surviving C0->C1 policy
# delta is attributable to the declared image and check, not to "we told C1 the rule."
_CONSTRAINT = ("Constraint: install from the base distro's package manager. "
               "Do not add third-party apt sources and do not download from URLs.")


def render_brief(n: dict, condition: str) -> str:
    if condition == "C0":                       # what a reactive agent actually sees
        port = n.get("port") or "?"
        return (f"The repo's tests fail with:\n"
                f"  ConnectionError: [Errno 111] connecting to localhost:{port}\n"
                f"Provision whatever is needed so the tests can run.\n"
                f"{_CONSTRAINT}")

    lines = [f"Service `{n['name']}` is required by this repo's tests.",
             f"Declared image: {n['image']}"]
    if n.get("endpoint"):
        lines.append(f"It must answer at: {n['endpoint']}")
    if n.get("env"):
        kv = " ".join(f"{k}={v}" for k, v in list(n["env"].items())[:6])
        lines.append(f"Declared config: {kv}")
    if n.get("command"):
        lines.append(f"Declared start args: {n['command']}")
    if n.get("seed"):
        lines.append(f"Seed mounts: {n['seed']}")
    if condition != "C3" and n["check"]["command"]:
        lines.append(f"You will know it is up when this returns 0: {n['check']['command']}")
    if condition != "C2":
        lines.append(f"Verbatim declaration: {n['raw']}")
    lines.append(_CONSTRAINT)
    return "\n".join(lines)
