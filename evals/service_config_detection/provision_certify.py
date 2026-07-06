"""Container-certify harness: the objective oracle for service-provisioning setups.

Given a service `setup` (`install`/`start`/`probe`), assembles a bounded bash script and
runs it in a fresh Debian container: install steps first (each bounded by an in-container
`timeout`), then the (possibly backgrounded) start command, then a bounded readiness poll
of the probe. SATISFIED iff the poll succeeds before the bound, else MISSING. This is the
same thing `certify.py` will do at build time, isolated so the eval can score generated
setups. Standalone: takes a plain `setup` dict, imports nothing from the pipeline, and
nothing imports it yet.

Usage:
    python3 evals/service_config_detection/provision_certify.py   # run + record PoC baseline
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
_READY_RE = re.compile(r"READY\(iter=(\d+)\)")
_DOCKER_INFO_TIMEOUT_S = 5
# Slack added on top of boot_timeout_s for the *host-side* subprocess.run bound: covers
# image-pull latency plus the in-container poll loop's own bound (which is itself derived
# from boot_timeout_s), so the host timeout always fires strictly after the in-container
# poll would have given up on its own.
_HOST_TIMEOUT_SLACK_S = 15


@dataclass(frozen=True)
class CertifyResult:
    """Outcome of certifying one service `setup` against a fresh container."""

    state: str            # "SATISFIED" | "MISSING"
    iters: int | None      # poll iteration that succeeded, parsed from "READY(iter=N)"
    log: str               # last ~15 lines of combined stdout+stderr


def _docker_available() -> bool:
    """True iff the `docker` binary is on PATH and the daemon answers `docker info`.

    Bounded with a short *Python-level* subprocess timeout — never a host-side `timeout`
    command, since macOS has none.
    """
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_DOCKER_INFO_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0


def _assemble_script(setup: dict, boot_timeout_s: int) -> str:
    """Build the in-container bash script per the certify bash contract:
    install (each line bounded + rc-checked) -> start -> bounded readiness poll.
    """
    lines = ["set +e"]
    for install_line in setup["install"]:
        lines.append(
            f"timeout 120 {install_line} || {{ echo INSTALL_FAIL; exit 3; }}"
        )
    lines.append(setup["start"])
    n = max(1, boot_timeout_s // 2)
    lines.append(
        f'for i in $(seq 1 {n}); do {setup["probe"]} >/dev/null 2>&1 && '
        f'{{ echo "READY(iter=$i)"; exit 0; }}; sleep 2; done; echo TIMEOUT; exit 1'
    )
    return "\n".join(lines) + "\n"


def _last_lines(text: str, n: int = 15) -> str:
    return "\n".join(text.splitlines()[-n:])


def certify_setup(
    setup: dict, base_image: str = "debian:bookworm", boot_timeout_s: int = 30
) -> CertifyResult:
    """Run `setup` (`{"install": [...], "start": str, "probe": str}`) in a fresh
    `base_image` container and score the result of a bounded readiness poll.

    The in-container `timeout` on install lines plus the poll's own bound are the primary
    bounding mechanism. But `setup["start"]` is emitted raw and untrusted (it may be
    LLM-generated): if it runs in the foreground (no `&`/daemonize) bash blocks on it
    forever and the poll loop is never reached. To keep this harness self-bounding against
    that case, `subprocess.run` also carries a *host-side* Python-level `timeout` (never a
    shell `timeout` command — macOS has none) of `boot_timeout_s + _HOST_TIMEOUT_SLACK_S`.
    """
    script = _assemble_script(setup, boot_timeout_s)
    host_timeout_s = boot_timeout_s + _HOST_TIMEOUT_SLACK_S
    try:
        completed = subprocess.run(
            ["docker", "run", "--rm", "-i", base_image, "bash", "-s"],
            input=script,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=host_timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        # `--rm` reclaims the container once the killed `docker run` client process tears
        # down its end of the attached session; there is no separately-identifiable
        # container name/id to `docker kill` here (we never passed `--name`), so there is
        # nothing more to clean up host-side.
        captured = exc.output or exc.stdout or ""
        if isinstance(captured, bytes):
            captured = captured.decode("utf-8", errors="replace")
        note = (
            f"TIMEOUT: docker run exceeded host-side bound of {host_timeout_s}s "
            "(likely a foreground `start` command blocking the poll loop)"
        )
        combined = f"{note}\n{captured}" if captured else note
        return CertifyResult(state="MISSING", iters=None, log=_last_lines(combined))
    combined = completed.stdout or ""
    state = "SATISFIED" if completed.returncode == 0 else "MISSING"
    match = _READY_RE.search(combined)
    iters = int(match.group(1)) if match else None
    return CertifyResult(state=state, iters=iters, log=_last_lines(combined))


# ------------------------------- PoC baseline ------------------------------------------
# Ported bodies of the 4 validated scratchpad PoC setups (poc_certify.sh: redis known-kind,
# qdrant binary release, memcached apt-root, milvus hallucinated standalone binary). Per
# spec Sec 8 this is the ONE place hand-ported bash is allowed: it exists to establish and
# record the reference baseline the harness was validated against, not as a pattern to
# replicate elsewhere.
_POC_SETUPS: dict[str, dict] = {
    "redis": {
        "install": ["apt-get update -qq", "apt-get install -y -qq redis-server"],
        "start": "redis-server --daemonize yes >/dev/null 2>&1",
        "probe": "redis-cli ping 2>/dev/null | grep -q PONG",
    },
    "qdrant": {
        "install": ["apt-get update -qq", "apt-get install -y -qq curl tar"],
        "start": (
            "curl -sL -o /tmp/q.tar.gz "
            "https://github.com/qdrant/qdrant/releases/download/v1.9.0/"
            "qdrant-x86_64-unknown-linux-gnu.tar.gz; "
            "tar -xzf /tmp/q.tar.gz -C /tmp 2>/dev/null; "
            "mv /tmp/qdrant /usr/local/bin/qdrant 2>/dev/null; "
            "chmod +x /usr/local/bin/qdrant 2>/dev/null; "
            "mkdir -p /var/lib/qdrant/storage; "
            "nohup /usr/local/bin/qdrant --storage-path /var/lib/qdrant/storage "
            "> /var/log/qdrant.log 2>&1 &"
        ),
        "probe": "curl -sf localhost:6333/healthz",
    },
    "memcached": {
        "install": ["apt-get update -qq", "apt-get install -y -qq memcached netcat-openbsd"],
        "start": "memcached -d -m 64 -p 11211 2>/tmp/mc.err",
        "probe": "nc -z localhost 11211",
    },
    "milvus": {
        "install": [
            "apt-get update -qq",
            "apt-get install -y -qq wget tar curl libopenblas-dev libomp-dev",
        ],
        "start": (
            "wget -q https://github.com/milvus-io/milvus/releases/download/v2.4.0/"
            "milvus-standalone-docker-2.4.0.tar.gz 2>/dev/null; "
            "tar -xzf milvus-standalone-docker-2.4.0.tar.gz 2>/dev/null; "
            "( cd milvus 2>/dev/null && chmod +x bin/milvus 2>/dev/null && "
            "nohup ./bin/milvus run standalone > /tmp/milvus.log 2>&1 & ) 2>/dev/null"
        ),
        "probe": (
            "curl -s -o /dev/null -w '%{http_code}' "
            "http://localhost:19530/api/v1/health 2>/dev/null | grep -q 200"
        ),
    },
}


def run_poc_baseline() -> dict:
    """Certify the 4 PoC-reference setups, write the baseline artifact, and return it.

    Validates the instrument itself (redis/qdrant SATISFIED, memcached/milvus expected
    to expose their known bugs) and records the reference result other work can diff
    against. Requires Docker + network (apt, GitHub release downloads).
    """
    results: dict[str, dict] = {}
    for name, setup in _POC_SETUPS.items():
        result = certify_setup(setup)
        results[name] = {"state": result.state, "iters": result.iters, "log": result.log}
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (_ARTIFACT_DIR / "poc_baseline.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return results


if __name__ == "__main__":
    baseline = run_poc_baseline()
    for name, row in baseline.items():
        print(f"{name}: {row['state']} (iters={row['iters']})")
