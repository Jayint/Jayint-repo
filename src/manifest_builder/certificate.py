from __future__ import annotations

import hashlib
import json
import os

TOOL_VERSION = "manifest_builder/0.1"


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode()).hexdigest()


def _sha256_json(obj) -> str:
    return _sha256_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


def build_certificate(verdict, r1, r2, *, repo_url, commit_sha, base_image, base_image_digest,
                      collect_command, source_tree_sha256, protected_file_hashes, dockerfile_text,
                      image_id, agent_meta) -> dict:
    manifest = list(verdict.manifest or ())
    return {
        "status": "CERTIFIED" if verdict.accepted else "REJECTED",
        "repo_url": repo_url,
        "commit_sha": commit_sha,
        "base_image": base_image,
        "base_image_digest": base_image_digest,
        "collect_command": collect_command,
        "accepted": verdict.accepted,
        "reject_reasons": list(verdict.reasons),
        "runs": [{"exit_code": r1.exit_code, "collected_count": r1.collected_count},
                 {"exit_code": r2.exit_code, "collected_count": r2.collected_count}],
        "manifest_size": len(manifest),
        "completeness": {
            "collected_count": r1.collected_count,
            "skipped_modules": list(r1.skipped_modules),
            "n_skipped_modules": len(r1.skipped_modules),
            "deselected": list(r1.deselected),
            "n_deselected": len(r1.deselected),
        },
        "hashes": {
            "source_tree_sha256": source_tree_sha256,
            "protected_files": dict(protected_file_hashes),
            "dockerfile_sha256": _sha256_text(dockerfile_text),
            "image_id": image_id,
            "collect_command_sha256": _sha256_text(collect_command),
            "manifest_sha256": _sha256_json(manifest),
        },
        "agent": dict(agent_meta),
        "tool_version": TOOL_VERSION,
    }


def _dump_run(r) -> dict:
    return {"exit_code": r.exit_code, "collected": list(r.collected),
            "collect_errors": list(r.collect_errors), "skipped_modules": list(r.skipped_modules),
            "deselected": list(r.deselected)}


def write_artifacts(out_dir, verdict, certificate, r1, r2, build_log, transcript_src=None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "collected-nodeids.json"), "w") as f:
        json.dump(list(verdict.manifest or ()), f, indent=1)
    with open(os.path.join(out_dir, "collection-certificate.json"), "w") as f:
        json.dump(certificate, f, indent=1)
    with open(os.path.join(out_dir, "build.log"), "w") as f:
        f.write(build_log or "")
    with open(os.path.join(out_dir, "collect-run1.json"), "w") as f:
        json.dump(_dump_run(r1), f, indent=1)
    with open(os.path.join(out_dir, "collect-run2.json"), "w") as f:
        json.dump(_dump_run(r2), f, indent=1)
    if transcript_src and os.path.exists(transcript_src):
        import shutil
        shutil.copyfile(transcript_src, os.path.join(out_dir, "agent-transcript.jsonl"))
