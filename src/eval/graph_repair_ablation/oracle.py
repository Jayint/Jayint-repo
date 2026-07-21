"""Declarative injection manifest for the graph-repair-ablation pilot. Each row is
a KNOWN-root-cause build failure produced by mutating the rendered setup.sh (so it
survives construction). The `correct_action` is the oracle the grader matches an
agent's diagnosis against."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.model import package_id  # noqa: E402

FAILURE_CLASSES: frozenset[str] = frozenset({
    "SYSLIB_MISSING", "COMPILER_ABSENT", "VERSION_CONFLICT", "OVERINCLUDE", "TOOL_ABSENT",
})


@dataclass(frozen=True)
class Injection:
    injection_id: str
    repo: str                 # corpus dir name (fetched under the build_script_eval smoke root)
    base_image: str
    failure_class: str        # one of FAILURE_CLASSES
    # how the rendered setup.sh is perturbed:
    #   {"op":"strip_line","match":"<substr>"}         -> drop the apt/tool line
    #   {"op":"add_install_pkg","pkg":"<name>"}         -> append a bad pip pkg to install
    #   {"op":"add_pin","pkg":"<name>","spec":"==x.y"}  -> append a conflicting pin
    mutation: dict
    # the KNOWN cause + the fix a correct agent should propose:
    #   {"kind":"install","target":"apt:libX-dev"} | {"kind":"drop","target":"<pkg>"}
    #   | {"kind":"repin","target":"<pkg>"}
    correct_action: dict
    note: str = ""
    correct_anchor: str = ""    # symptom node parse->integrate should ground to; "" == REFUSE


# One injection per class. Repos are already fetched by the build_script_eval corpus.
PILOT_INJECTIONS: tuple[Injection, ...] = (
    Injection("syslib_pygraphviz", "pygraphviz", "python:3.11-slim", "SYSLIB_MISSING",
              {"op": "strip_line", "match": "libgraphviz-dev"},
              {"kind": "install", "target": "apt:libgraphviz-dev"},
              "strip the graphviz -dev apt line -> import fails on libcgraph.so"),
    Injection("compiler_pyzmq", "pyzmq", "python:3.11-slim", "COMPILER_ABSENT",
              {"op": "strip_line", "match": "build-essential"},
              {"kind": "install", "target": "apt:build-essential"},
              "strip build-essential -> native build 'gcc failed'"),
    Injection("conflict_requests", "requests", "python:3.11-slim", "VERSION_CONFLICT",
              {"op": "add_pin", "pkg": "urllib3", "spec": "==1.20"},
              {"kind": "repin", "target": "urllib3"},
              "append an incompatible urllib3 pin -> resolver/runtime conflict"),
    Injection("overinclude_dotenv", "python-dotenv", "python:3.11-slim", "OVERINCLUDE",
              {"op": "add_install_pkg", "pkg": "this-optional-pkg-fails-to-build==0.0.0"},
              {"kind": "drop", "target": "this-optional-pkg-fails-to-build"},
              "append an unbuildable OPTIONAL dep -> install fails; correct action is DROP"),
    Injection("tool_semrel", "python-semantic-release", "python:3.11-slim", "TOOL_ABSENT",
              {"op": "strip_line", "match": "git"},
              {"kind": "install", "target": "apt:git"},
              "strip git -> GitPython GIT_PYTHON refresh error"),
)


def select(only: frozenset[str] = frozenset(),
           classes: frozenset[str] = frozenset()) -> list["Injection"]:
    if classes - FAILURE_CLASSES:
        raise ValueError(f"unknown class(es): {sorted(classes - FAILURE_CLASSES)}")
    ids = {i.injection_id for i in PILOT_INJECTIONS}
    if only - ids:
        raise ValueError(f"unknown injection id(s): {sorted(only - ids)}")
    return [i for i in PILOT_INJECTIONS
            if (not only or i.injection_id in only)
            and (not classes or i.failure_class in classes)]


# Test-domain injections: the fault lets the BUILD succeed but makes import/collection
# FAIL, so parse() sees its native domain. correct_anchor = the symptom node.
TEST_DOMAIN_INJECTIONS: tuple[Injection, ...] = (
    Injection("td_mnf_dropdep", "requests", "python:3.11-slim", "MODULE_NOT_FOUND",
              {"op": "strip_line", "match": "urllib3"},
              {"kind": "install", "target": "urllib3"},
              "strip urllib3 install -> `import requests` fails ModuleNotFoundError: urllib3",
              correct_anchor=package_id("urllib3", None)),
)
