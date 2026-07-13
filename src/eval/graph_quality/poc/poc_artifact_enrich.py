"""POC — the central claim of the unified-enrichment design, against REAL Docker.

    "A successful build can still be a broken environment."

`pip install opencv-python` returns rc=0 (a manylinux wheel — nothing to compile).
Then `import cv2` dies on `libGL.so.1: cannot open shared object file`.

So the build is GREEN and the environment is BROKEN, and there is NO ERROR TEXT for the
current arm to parse. This is a real case from the corpus (Azure__co-op-translator:
libGL.so.1 -> libgl1).

ARM A (today)  : enrich() on the build output — a SUCCESS log. Expect: discovers NOTHING.
ARM B (design) : the REAL ldd_probe() on the installed artifact.
                 Expect: syslib:libGL.so.1, with a requires edge from pkg:opencv-python.

Then we install the fix the graph proposes and re-import, to prove the fix is CORRECT and
not merely plausible. Nothing here is mocked: real container, real wheel, real ELF headers.
"""
from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[4]   # repo root
assert (_ROOT / "src").is_dir(), _ROOT
for p in (str(_ROOT), str(_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from python_deps.depgraph.diagnose import RepoContext
from python_deps.depgraph.executor import DockerExecutor
from python_deps.depgraph.graph_enrich import enrich
from python_deps.depgraph.ids import TEST_NODE_ID, package_id
from python_deps.depgraph.ldd_probe import ldd_probe
from python_deps.depgraph.schema import (
    DepGraph, DiscoveredBy, Layer, Node, NodeType, State,
)
from src.react_repair.loop import RunResult

IMAGE = "python:3.11-slim"
PKG = "opencv-python"


def banner(s):
    print(f"\n{'=' * 78}\n{s}\n{'=' * 78}")


def seed_graph(version: str) -> DepGraph:
    """What construction leaves: the declared package, and NOTHING about libGL."""
    return (DepGraph()
            .with_node(Node(id=TEST_NODE_ID, type=NodeType.TEST, name="repo_tests_pass",
                            layer=Layer.TESTS, discovered_by=DiscoveredBy.GOAL))
            .with_node(Node(id=package_id(PKG, version), type=NodeType.PACKAGE, name=PKG,
                            layer=Layer.PIP, discovered_by=DiscoveredBy.STATIC_SCAN,
                            version=version, state=State.SATISFIED)))


def main() -> int:
    with DockerExecutor(IMAGE) as ex:
        banner("STEP 1 — install the package. Is the build GREEN?")
        install = ex.run(f"python -m pip install --no-cache-dir {PKG} 2>&1", timeout=600)
        print(f"  $ pip install {PKG}")
        print(f"  rc = {install.returncode}   <-- 0 means the BUILD SUCCEEDED")
        if install.returncode != 0:
            print("  POC INVALID: the build failed, so this is not the case we are testing.")
            return 1
        ver = ex.run(f"python -c \"import importlib.metadata as m; "
                     f"print(m.version('{PKG}'))\"").stdout.strip()
        print(f"  installed {PKG}=={ver}")
        build_log = install.stdout          # a SUCCESS log. This is all the arm gets today.

        banner("STEP 2 — but is the ENVIRONMENT actually working?")
        imp = ex.run('python -c "import cv2; print(cv2.__version__)" 2>&1')
        print(f"  $ python -c 'import cv2'")
        print(f"  rc = {imp.returncode}")
        for line in (imp.stdout or "").strip().splitlines()[-2:]:
            print(f"     {line.strip()[:100]}")
        broken = imp.returncode != 0
        print(f"\n  => build GREEN (rc 0) but environment BROKEN: {broken}")

        graph = seed_graph(ver)
        print(f"\n  seed graph: {[n.id for n in graph.nodes]}   (nothing about libGL)")

        banner("ARM A — TODAY: enrich() from the build output (which is a SUCCESS log)")
        result_a = RunResult(ok=True, failing_command=None, output=build_log)
        g_a, new_a = enrich(graph, result_a, [], RepoContext())
        print(f"  enrich() discovered: {new_a or '[]  <-- NOTHING'}")
        print("  Why: the build SUCCEEDED. There is no failing command and no error text.")
        print("       The classifier has nothing to classify. The graph learns NOTHING,")
        print("       and the agent gets no help — even though the env is demonstrably broken.")

        banner("ARM B — DESIGN: the REAL ldd_probe() against the INSTALLED ARTIFACT")
        before = {n.id for n in graph.nodes}
        g_b = ldd_probe(graph, ex)
        new_b = [n for n in g_b.nodes if n.id not in before]
        print(f"  ldd_probe() discovered: {[n.id for n in new_b] or '[] '}")
        for n in new_b:
            print(f"\n     node   {n.id}")
            print(f"     type   {n.type.value}   state={n.state.value}")
            print(f"     found  discovered_by={n.discovered_by.value}")
            print(f"     fix    {n.chosen_fix}   candidates={list(n.fix_candidates)}")
        edges = [e for e in g_b.edges if e.dst in {n.id for n in new_b}]
        print("\n     edges (the OWNER, attached with no parsing and no guessing):")
        for e in edges:
            print(f"     {e.src}  --{e.relation.value}-->  {e.dst}")

        banner("STEP 3 — is the fix the graph proposes actually CORRECT?")
        if not new_b:
            print("  no discovery -> nothing to verify. POC FAILED.")
            return 1
        # FIX-KEYED COLLAPSE: several syslib nodes can share ONE apt action.
        # (libgthread-2.0.so.0 and libglib-2.0.so.0 both ship in libglib2.0-0.)
        # The agent gets ONE rebuild per turn, so it must get ONE action list -- not
        # one node at a time, which is exactly the serial chain the graph exists to kill.
        by_fix: dict[str, list[str]] = {}
        for n in new_b:
            if n.chosen_fix:
                by_fix.setdefault(n.chosen_fix.split(":", 1)[-1], []).append(n.id)
        print(f"  {len(new_b)} missing syslib nodes  ->  {len(by_fix)} distinct apt actions")
        for apt_name, ids in by_fix.items():
            print(f"     apt:{apt_name:<16} satisfies {', '.join(ids)}")

        apt_all = " ".join(sorted(by_fix))
        print(f"\n  proposed fix (ALL of them, one shot): apt-get install -y {apt_all}")
        fix = ex.run(f"apt-get update -qq && apt-get install -y -qq {apt_all} 2>&1", timeout=900)
        print(f"  rc = {fix.returncode}")
        after = ex.run('python -c "import cv2; print(cv2.__version__)" 2>&1')
        print(f"\n  $ python -c 'import cv2'   (after applying the graph's fix)")
        print(f"  rc = {after.returncode}   output: {(after.stdout or '').strip()[:60]}")

        banner("VERDICT")
        a_blind = not new_a
        b_found = bool(new_b)
        b_owner = any(e.src.startswith("pkg:") for e in edges)
        fixed = after.returncode == 0
        print(f"  build was GREEN, env was BROKEN ............. {broken}")
        print(f"  ARM A (error text) discovered nothing ....... {a_blind}")
        print(f"  ARM B (artifact)   discovered the syslib .... {b_found}")
        print(f"  ARM B attached the OWNER package ............ {b_owner}")
        print(f"  the fix ARM B proposed actually WORKS ....... {fixed}")
        ok = broken and a_blind and b_found and b_owner and fixed
        print(f"\n  DESIGN CLAIM {'CONFIRMED' if ok else 'NOT CONFIRMED'}")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
