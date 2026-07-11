from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.manifest_builder import certificate as C
from src.manifest_builder import workspace as W
from src.manifest_builder.collect import Docker, build_and_collect, BuildError
from src.manifest_builder.gate import accept, pick_best
from src.manifest_builder.protected import restore_pristine, source_tree_sha256
from src.manifest_builder.runner import ClaudeRunner, TASK_PROMPT

_PLUGIN = str(_REPO_ROOT / "src" / "manifest_builder" / "collect_plugin.py")


def certify(docker, ws, plugin_path, tmp_dir):
    os.makedirs(tmp_dir, exist_ok=True)
    restore_pristine(ws.path)
    try:
        image_id, build_log, r1, r2, in_img, injected = build_and_collect(
            docker, ws, plugin_path, tmp_dir, ws.protected)
    except BuildError as e:
        from src.manifest_builder.types import CollectionResult, Verdict
        empty = CollectionResult(exit_code=1)
        v = Verdict(False, ("docker build failed",), None, 0)
        # Record the agent's actual (failing) Dockerfile — restore_pristine preserves it, so
        # it's on disk — not the seed, and keep provenance symmetric with the success path.
        _df = Path(ws.path) / "Dockerfile"
        dockerfile_text = _df.read_text() if _df.exists() else ws.dockerfile_text
        cert = C.build_certificate(v, empty, empty, repo_url=ws.repo_url, commit_sha=ws.commit_sha,
            base_image=ws.base_image, base_image_digest="", collect_command="",
            source_tree_sha256=source_tree_sha256(ws.pristine_hashes),
            protected_file_hashes=ws.pristine_hashes, dockerfile_text=dockerfile_text,
            image_id="", agent_meta={"runner": "claude code", "model": "opus"})
        return v, cert, str(e), empty, empty
    protected_ok = (in_img == ws.pristine_hashes) and not injected
    verdict = accept(r1, r2, protected_ok)
    from src.manifest_builder.collect import COLLECT_CMD
    dockerfile_text = (Path(ws.path) / "Dockerfile").read_text()
    cert = C.build_certificate(
        verdict, r1, r2, repo_url=ws.repo_url, commit_sha=ws.commit_sha, base_image=ws.base_image,
        base_image_digest=image_id, collect_command=COLLECT_CMD,
        source_tree_sha256=source_tree_sha256(ws.pristine_hashes),
        protected_file_hashes=in_img, dockerfile_text=dockerfile_text, image_id=image_id,
        agent_meta={"runner": "claude code", "model": "opus"},
        injected_collection_files=injected)
    return verdict, cert, build_log, r1, r2


def build_one(repo_url, sha, out_dir, runner, docker=None, *, docker_factory=None,
              attempts=3, base_image="python:3.11-slim", workdir=None):
    workdir = workdir or tempfile.mkdtemp(prefix="manifest-wt-")
    ws = W.prepare_workspace(repo_url, sha, workdir, base_image=base_image)
    dk = docker or (docker_factory(ws) if docker_factory else Docker())
    df_path = os.path.join(ws.path, "Dockerfile")
    # Run EVERY attempt as an independent maximization sample: reset the Dockerfile to the
    # seed each time (fresh start), let the agent maximize, then certify. After all attempts
    # we keep the highest-collected_count ACCEPTED result (pick_best) — running N and picking
    # the best guards against a single agent run under-collecting. Each attempt's Dockerfile is
    # captured so the artifact + certificate reflect the WINNING attempt, not the last one run.
    records = []   # (verdict, cert, build_log, r1, r2, transcript_path, dockerfile_text)
    for _ in range(max(1, attempts)):
        with open(df_path, "w") as f:
            f.write(ws.dockerfile_text)
        agent_res = runner.run(cwd=ws.path, prompt=TASK_PROMPT, autonomous=True)
        with open(df_path) as f:
            dockerfile_text = f.read()
        with tempfile.TemporaryDirectory() as td:
            verdict, cert, build_log, r1, r2 = certify(dk, ws, _PLUGIN, td)
        records.append((verdict, cert, build_log, r1, r2, agent_res.transcript_path,
                        dockerfile_text))
    best_verdict = pick_best([rec[0] for rec in records])
    if best_verdict is not None:
        chosen = next(rec for rec in records if rec[0] is best_verdict)
    else:
        chosen = max(records, key=lambda rec: rec[0].collected_count)   # best-effort reject
    verdict, cert, build_log, r1, r2, transcript, dockerfile_text = chosen
    # Observability: record every attempt's outcome and which one keep-best selected, so a
    # multi-attempt run shows that the highest-collected accepted attempt is the one certified.
    selected_index = next(i for i, rec in enumerate(records) if rec is chosen)
    attempts_detail = [{"accepted": rec[0].accepted, "collected": rec[0].collected_count,
                        "status": rec[1]["status"]} for rec in records]
    for i, det in enumerate(attempts_detail):
        mark = "  <-- selected (highest accepted)" if i == selected_index else ""
        print(f"[build_one] attempt {i + 1}/{len(records)}: accepted={det['accepted']} "
              f"collected={det['collected']}{mark}", file=sys.stderr, flush=True)
    art_dir = os.path.join(out_dir, ws.slug, sha)
    os.makedirs(art_dir, exist_ok=True)
    with open(os.path.join(art_dir, "Dockerfile"), "w") as f:
        f.write(dockerfile_text)   # the WINNING attempt's Dockerfile, not the last executed
    C.write_artifacts(art_dir, verdict, cert, r1, r2, build_log, transcript_src=transcript)
    return {"repo_url": repo_url, "sha": sha, "status": cert["status"],
            "manifest_size": cert["manifest_size"], "artifacts_dir": art_dir,
            "attempts": len(records), "selected_index": selected_index,
            "attempts_detail": attempts_detail}


def _cmd_verify(args):
    ws = W.load_state(args.workspace)
    with tempfile.TemporaryDirectory() as td:
        verdict, cert, _, _, _ = certify(Docker(), ws, _PLUGIN, td)
    # Surface the maximization signal to the agent: not just accepted/reasons, but the collected
    # count and which modules were skipped at import (the list to keep driving down).
    print(json.dumps({"accepted": verdict.accepted, "reasons": list(verdict.reasons),
                      "collected": verdict.collected_count,
                      "import_skipped": cert["completeness"]["skipped_modules"]}))
    return 0 if verdict.accepted else 1


def _cmd_build(args):
    summary = build_one(args.repo_url, args.sha, args.out, ClaudeRunner(), attempts=args.attempts)
    print(json.dumps(summary, indent=1))
    return 0 if summary["status"] == "CERTIFIED" else 1


def _cmd_corpus(args):
    with open(args.corpus) as f:
        data = json.load(f)
    repos = data.get("repos", data)
    rc = 0
    for r in repos:
        full = r.get("full_name")
        url = r.get("clone_url") or (f"https://github.com/{full}" if full else None)
        sha = r.get("commit")
        if not url or not sha:
            print(f"SKIP {url or r}: missing clone_url/full_name or commit", file=sys.stderr)
            rc = 1
            continue
        try:
            summary = build_one(url, sha, args.out, ClaudeRunner(), attempts=args.attempts)
        except Exception as e:   # one repo's failure must not abort the whole batch
            print(f"FAIL {url}@{sha}: {type(e).__name__}: {e}", file=sys.stderr)
            rc = 1
            continue
        print(json.dumps(summary))
        rc = rc or (0 if summary["status"] == "CERTIFIED" else 1)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.manifest_builder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify"); v.add_argument("--workspace", required=True)
    v.set_defaults(fn=_cmd_verify)
    b = sub.add_parser("build")
    b.add_argument("--repo-url", required=True); b.add_argument("--sha", required=True)
    b.add_argument("--out", default="artifacts"); b.add_argument("--attempts", type=int, default=3)
    b.set_defaults(fn=_cmd_build)
    c = sub.add_parser("corpus")
    c.add_argument("--corpus", required=True); c.add_argument("--out", default="artifacts")
    c.add_argument("--attempts", type=int, default=3); c.set_defaults(fn=_cmd_corpus)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
