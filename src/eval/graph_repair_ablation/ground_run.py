"""Container orchestration for the grounding arm: build the graph, inject a fault that
survives construction but fails at import/collection, capture that failure, and run the
deterministic grounding (arms G + B). Integration-only; unit correctness is test_ground.py."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"
for _p in (_REPO_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from graph.compile.build_script import render_build_script  # noqa: E402
from graph.python.enrich.diagnose import RepoContext  # noqa: E402
from graph.python.read import repo_modules  # noqa: E402
from src.eval.graph_repair_ablation.ground import cause_line, grade_grounding, run_grounding  # noqa: E402
from src.eval.graph_repair_ablation.inject import apply_injection  # noqa: E402
from src.eval.graph_repair_ablation.oracle import Injection  # noqa: E402
from src.eval.language_package_eval.coverage import (  # noqa: E402
    _MountedContainer, _write_file, base_image_for_repo, build_graph_construction_only,
    first_failure_evidence,
)


def _capture_runtime_failure(box, container_dir: str) -> str | None:
    """Run pytest --collect-only in the built container; return the failure output or None."""
    boot = box.run("pip install --no-input --quiet pytest", timeout=300)
    if not boot.ok:
        return None
    collected = box.run(f"cd {container_dir} && python3 -m pytest --collect-only -q", timeout=600)
    if collected.ok:
        return None
    return (collected.stdout or "") + (collected.stderr or "")


def ground_one(inj: Injection, *, smoke_root, install_timeout: int = 1800) -> list[dict]:
    repo_dir = Path(smoke_root) / inj.repo
    image, minor, _ = base_image_for_repo(str(repo_dir))
    graph = build_graph_construction_only(str(repo_dir), image, minor)
    script = render_build_script(graph, ())
    mutated = apply_injection(script, inj)
    ctx = RepoContext(local_names=repo_modules.top_level_names(str(repo_dir)),
                      collisions=repo_modules.stem_collisions(str(repo_dir)))

    with _MountedContainer(image, str(repo_dir)) as box:
        _write_file(box, "/setup.sh", mutated)
        install = box.run(f"cd {box.container_dir} && bash -x /setup.sh", timeout=install_timeout)
        if not install.ok:
            # test-domain injection must survive construction; a build failure is a corpus bug.
            print(f"WARNING: {inj.injection_id!r} failed at BUILD, not import — skipping", file=sys.stderr)
            return []
        failure_output = _capture_runtime_failure(box, box.container_dir)

    if not failure_output:
        print(f"WARNING: {inj.injection_id!r} did not produce an import/collection failure — skipping",
              file=sys.stderr)
        return []

    ev = first_failure_evidence(failure_output)
    cause_text = cause_line(failure_output)
    res = run_grounding(graph, cause_text, ev["command"] or "python3 -m pytest --collect-only -q",
                        failure_output, ctx)
    g = grade_grounding(res["grounded_anchor"], res["grounded_added_node"], inj.correct_anchor)
    b = grade_grounding(res["baseline_anchor"], res["baseline_anchor"] is not None, inj.correct_anchor)
    return [
        {"injection_id": inj.injection_id, "failure_class": inj.failure_class, "arm": "G", "score": g.__dict__},
        {"injection_id": inj.injection_id, "failure_class": inj.failure_class, "arm": "B", "score": b.__dict__},
    ]
