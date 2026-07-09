#!/usr/bin/env python3
"""multi_docker_eval_adapter.py — bridge the run_v3 graph-scheduler agent to the
RAT eval harness (``eval/models/dockeragent_model.py``).

Contract (see ``DockerAgentModel.predict``): the harness calls
``MultiDockerEvalAdapter(output_dir).process_single_instance(instance, ...)`` and
consumes ``result["dockerfile"]`` — a self-contained Dockerfile STRING that clones
the repo into ``/testbed`` and installs the environment — plus, optionally,
``result["setup_scripts"]`` (``{name: content}`` files the Dockerfile ``COPY``s).
The harness then builds the image, mounts RAT's pytest tools, runs them at
``/testbed`` and scores. This adapter does NOT run pytest itself.

The v3 agent's entrypoint is ``scripts/run_v3_e2e.py`` (``run_v3`` — the certified
graph-scheduler loop). This adapter runs it on a fresh local checkout to obtain a
certified install-only ``setup.sh`` + the base image ``run_v3`` selected, then bakes
both into a Dockerfile::

    FROM <base>
    RUN git clone <repo_url> /testbed
    WORKDIR /testbed
    COPY setup.sh ...            # the certified install-only script
    RUN bash setup.sh           # CWD=/testbed so `pip install -e .` resolves

This replaces the legacy DockerAgent adapter (which drove ``agent.DockerAgent`` +
``src.planner`` — both removed on the v3-core branch); it keeps the SAME
``process_single_instance`` entrypoint and the SAME result-dict contract, so
``dockeragent_model.py`` needs no change.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

# run_v3_e2e prints:  "[v3] base-image: <image> (py X) — <reason>". Capture <image>.
_BASE_IMAGE_RE = re.compile(r"\[v3\]\s*base-image:\s*(\S+)")
# run_v3 builds setup.sh for the Sandbox workdir /app; the eval image uses
# /testbed. setup.sh is install-only and path-relative in practice, but normalize
# any stray absolute /app defensively (mirrors the legacy adapter's behavior).
_APP_WORKDIR_RE = re.compile(r"(?<![\w/])/app(?![\w])")

# run_v3's entrypoint lives in THIS checkout (the agent root == this file's dir).
_AGENT_ROOT = Path(__file__).resolve().parent
_RUN_V3_E2E = _AGENT_ROOT / "scripts" / "run_v3_e2e.py"

# Repair-only ablation (V3_REPAIR_ABLATION=1 + V3_SEED_DIR=<construction run>/output):
# seed the react repair loop from a PRE-GENERATED construction setup.sh and skip graph
# construction, so only the repair loop varies. The seed's base image (needed because seed
# mode rejects "auto") comes from the per-instance _meta.json, falling back to the
# construction eval_build/Dockerfile FROM. Extract the FROM tag for that fallback.
_DOCKERFILE_FROM_RE = re.compile(r"^\s*FROM\s+(\S+)", re.IGNORECASE | re.MULTILINE)
# repo_url is always https://github.com/<owner>/<repo>[.git] — recover <owner>/<repo>.
_GITHUB_FULLNAME_RE = re.compile(r"github\.com[/:]+([^/]+/[^/]+?)(?:\.git)?/?$")
# The react loop's per-call token line: "[Tokens] Input: I, Output: O, Total: T".
_TOKENS_TOTAL_RE = re.compile(r"\[Tokens\].*Total:\s*(\d+)")

# run_v3 may report giveup (exit 1) yet still have written a best-effort setup.sh;
# the benchmark scores whatever environment the agent produced, so a non-zero exit
# is NOT fatal here — only a missing artifact is.
_RUN_V3_TIMEOUT = int(os.environ.get("V3_ADAPTER_RUN_TIMEOUT", "5400"))   # 90 min
_CLONE_TIMEOUT = int(os.environ.get("V3_ADAPTER_CLONE_TIMEOUT", "1200"))


class MultiDockerEvalAdapter:
    """Bridge run_v3 to the RAT harness by emitting a Dockerfile from the certified
    setup.sh. Drop-in for the legacy DockerAgent adapter: same
    ``process_single_instance`` entrypoint, same ``dockerfile``/``setup_scripts``/
    ``base_image`` result contract."""

    def __init__(self, output_dir: str = "./multi_docker_eval_output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def process_single_instance(
        self,
        instance: Dict[str, Any],
        base_image: str = "auto",
        model: str | None = None,
        max_steps: int = 30,
        enable_artifact_preflight: bool = False,
        **_ignored: Any,          # tolerate legacy kwargs the harness may still pass
    ) -> Dict[str, Any]:
        """Run run_v3 on a fresh checkout and return ``{instance_id: result}``.

        ``result`` carries ``dockerfile`` (str | None), ``setup_scripts``
        (``{"setup.sh": <content>}``), ``base_image`` (str | None) and ``logs``.
        Never raises — on any failure ``dockerfile`` stays None and ``logs["error"]``
        explains why (``dockeragent_model.py`` treats a None dockerfile as a clean
        ``no_dockerfile`` failure rather than a crash)."""
        instance_id = instance.get("instance_id", "unknown")
        repo_url = instance.get("repo_url") or ""
        result: Dict[str, Any] = {
            "dockerfile": None,
            "setup_scripts": {},
            "base_image": None,
            "logs": {},
        }
        try:
            full_name = self._full_name(repo_url, instance_id)
            pin_sha = self._seed_head_sha(full_name)      # None off-ablation → shallow current-HEAD clone
            src_dir = self._clone(repo_url, pin_sha=pin_sha)
            head_sha = self._head_sha(src_dir)
            setup_sh, resolved_base = self._run_v3(
                src_dir, base_image, model, full_name=full_name, max_steps=max_steps)
            setup_sh = _APP_WORKDIR_RE.sub("/testbed", setup_sh)
            result["base_image"] = resolved_base
            result["setup_scripts"] = {"setup.sh": setup_sh}
            # V3_INCLUDE_SERVICES=1: _run_v3 asked run_v3_e2e for a second
            # artifact (the service-start ENTRYPOINT wrapper); pick it up if it
            # wrote one. "" (the default/no-service-nodes case) adds nothing —
            # setup_scripts and the Dockerfile stay exactly as before.
            services_sh = self._read_services_script()
            if services_sh:
                result["setup_scripts"]["services_start.sh"] = services_sh
            result["dockerfile"] = self._render_dockerfile(
                resolved_base, repo_url, include_services=bool(services_sh), pin_sha=pin_sha)
            result["logs"] = {"head_sha": head_sha, "resolved_base": resolved_base}
        except subprocess.CalledProcessError as e:
            tail = (getattr(e, "stderr", "") or "")[-2000:]
            result["logs"] = {"error": f"{e} :: {tail}"}
        except Exception as e:  # never crash the harness — it reads result["dockerfile"]
            result["logs"] = {"error": repr(e)}
        return {instance_id: result}

    # ── steps (individually overridable/mockable) ─────────────────────────────
    def _clone(self, repo_url: str, pin_sha: str | None = None) -> Path:
        """Fresh checkout for run_v3 to analyze. Idempotent per output_dir. Default: shallow clone
        of current HEAD. ``pin_sha`` (repair-ablation): FULL clone (history + tags, so VCS-versioned
        backends resolve) checked out at construction's commit for a faithful baseline."""
        if not repo_url:
            raise ValueError("instance has no repo_url")
        dst = self.output_dir / "v3_src"
        if dst.exists():
            subprocess.run(["rm", "-rf", str(dst)], check=True, timeout=300)
        if pin_sha:
            subprocess.run(["git", "clone", repo_url, str(dst)],
                           check=True, capture_output=True, text=True, timeout=_CLONE_TIMEOUT)
            subprocess.run(["git", "-C", str(dst), "checkout", "--detach", pin_sha],
                           check=True, capture_output=True, text=True, timeout=300)
        else:
            subprocess.run(["git", "clone", "--depth=1", repo_url, str(dst)],
                           check=True, capture_output=True, text=True, timeout=_CLONE_TIMEOUT)
        return dst

    def _head_sha(self, src_dir: Path) -> str:
        out = subprocess.run(
            ["git", "-C", str(src_dir), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return out.stdout.strip()

    @staticmethod
    def _full_name(repo_url: str, instance_id: str) -> str:
        """``<owner>/<repo>`` — the key into the seed corpus. Prefer the repo_url
        (unambiguous); fall back to un-mangling the instance_id (``owner__repo``)."""
        m = _GITHUB_FULLNAME_RE.search(repo_url or "")
        if m:
            return m.group(1)
        return (instance_id or "").replace("__", "/")

    def _resolve_seed(self, full_name: str | None) -> Tuple[str | None, str | None]:
        """Repair-only ablation (``V3_REPAIR_ABLATION=1``): map ``full_name`` to the
        construction run's seed ``setup.sh`` and its base image, so the react arm repairs
        a PRE-GENERATED script instead of constructing one. Returns ``(None, None)`` when
        the flag is off. Raises (→ clean ``no_dockerfile`` skip upstream, never a crash)
        when enabled but the seed is genuinely unusable — a missing/empty seed (e.g. the
        one repo whose construction produced nothing) must be recorded, not silently
        reconstructed, which would defeat the ablation."""
        if os.getenv("V3_REPAIR_ABLATION") != "1":
            return None, None
        root = os.getenv("V3_SEED_DIR")
        if not root:
            raise RuntimeError(
                "V3_REPAIR_ABLATION=1 requires V3_SEED_DIR (the construction run's output/ dir)")
        if not full_name:
            raise RuntimeError("repair-ablation: no full_name to map to a seed setup.sh")
        inst = Path(root) / full_name
        seed = inst / "setup.sh"
        if not seed.is_file() or seed.stat().st_size == 0:
            raise RuntimeError(
                f"repair-ablation: missing/empty seed setup.sh for {full_name} at {seed}")
        base = self._seed_base_image(inst)
        if not base:
            raise RuntimeError(
                f"repair-ablation: no base image for {full_name} "
                "(_meta.json['base_image'] and eval_build/Dockerfile FROM both unusable)")
        return str(seed), base

    @staticmethod
    def _seed_base_image(inst: Path) -> str:
        """Base image the construction run committed to, for this instance. Prefer
        ``_meta.json['base_image']`` (what run_v3 resolved); fall back to the
        ``eval_build/Dockerfile`` FROM tag the construction eval baked."""
        meta = inst / "_meta.json"
        if meta.is_file():
            try:
                base = (json.loads(meta.read_text()) or {}).get("base_image")
                if base:
                    return str(base)
            except (ValueError, OSError):
                pass
        dockerfile = inst / "eval_build" / "Dockerfile"
        if dockerfile.is_file():
            m = _DOCKERFILE_FROM_RE.search(dockerfile.read_text())
            if m:
                return m.group(1)
        return ""

    def _seed_head_sha(self, full_name: str | None) -> str | None:
        """The commit construction analyzed for this instance (repair-ablation only), from its
        ``_meta.json``. Pinning the react Sandbox AND the eval image to it reproduces the SAME tree
        + git tags construction measured — not a drifted current HEAD, which both makes the A/B
        unfaithful and breaks VCS-versioned installs (hatch-vcs/setuptools_scm need reachable tags,
        absent from a shallow clone). ``None`` off-ablation or when unrecorded."""
        if os.getenv("V3_REPAIR_ABLATION") != "1" or not full_name:
            return None
        root = os.getenv("V3_SEED_DIR")
        if not root:
            return None
        meta = Path(root) / full_name / "_meta.json"
        if meta.is_file():
            try:
                return (json.loads(meta.read_text()) or {}).get("head_sha") or None
            except (ValueError, OSError):
                return None
        return None

    def _run_v3(self, src_dir: Path, base_image: str, model: str | None,
                *, full_name: str | None = None, max_steps: int = 30) -> Tuple[str, str]:
        """Run the run_v3 graph-scheduler loop on ``src_dir``; return
        ``(setup_sh_text, resolved_base_image)``.

        ``check=False``: run_v3 exits 1 on giveup/unresolved but still writes a
        best-effort setup.sh (run_v3_e2e.py:203) — the missing-artifact case is the
        only true failure, so we validate the file rather than the exit code."""
        setup_path = self.output_dir / "setup.sh"
        if setup_path.exists():
            setup_path.unlink()
        services_path = self.output_dir / "services_start.sh"
        if services_path.exists():
            services_path.unlink()   # drop any stale artifact from a prior instance
        # Repair-only ablation: repair a PRE-GENERATED construction setup.sh; the base
        # image comes with the seed (seed mode rejects "auto"), so it overrides the caller's.
        seed_script, seed_base = self._resolve_seed(full_name)
        if seed_base:
            base_image = seed_base
        cmd = [sys.executable, str(_RUN_V3_E2E), str(src_dir),
               "--base-image", base_image or "auto",
               "--out", str(setup_path)]
        if model:
            cmd += ["--model", model]
        if seed_script:
            # Seed the react arm from the construction setup.sh and SKIP graph construction
            # (run_v3_e2e --seed-script). Mutually exclusive with --construction-only:
            # construction is exactly what this ablation replays instead of rebuilding.
            # --trace-out PERSISTS the react loop's Thought/Action/Observation JSONL next to run.log
            # (run_v3_e2e threads it to the react ReactLog); without it the loop's stdout is swallowed
            # on success and a regressed repo can't be diagnosed. react/seed-only — the construction
            # path's --trace-out builds a different (Task-8d) tracer, so it stays off there.
            trace_path = self.output_dir / "react_trace.jsonl"
            cmd += ["--arm", "react", "--seed-script", seed_script,
                    "--max-steps", str(max_steps), "--trace-out", str(trace_path)]
        elif os.getenv("V3_CONSTRUCTION_ONLY") == "1":
            # First-pass-construction benchmark mode: render the initial setup.sh from
            # LLM-driven construction and skip the repair loop, so the harness scores how
            # well construction ALONE provisions the repo. The LLM (base-image + service/
            # config classify) stays on — only repair is skipped.
            cmd.append("--construction-only")
        # V3_INCLUDE_SERVICES=1: also request the runtime service-start script.
        # run_v3_e2e only WRITES this file when its own env-var check passes AND
        # the graph has a reciped SERVICE node — passing the flag through env
        # (inherited below) plus this --services-out path is enough; nothing
        # downstream needs to know the flag's value directly.
        if os.getenv("V3_INCLUDE_SERVICES") == "1":
            cmd += ["--services-out", str(services_path)]
        # Ensure `from src...` resolves when run_v3_e2e runs as a subprocess.
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_AGENT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            cmd, cwd=str(_AGENT_ROOT), check=False, env=env,
            capture_output=True, text=True, timeout=_RUN_V3_TIMEOUT,
        )
        if not setup_path.exists():
            raise RuntimeError(
                "run_v3_e2e produced no setup.sh "
                f"(exit={proc.returncode}); stdout tail:\n{proc.stdout[-2000:]}\n"
                f"stderr tail:\n{proc.stderr[-2000:]}")
        setup_sh = setup_path.read_text()
        self._relay_token_telemetry(proc.stdout)
        m = _BASE_IMAGE_RE.search(proc.stdout)
        resolved = m.group(1) if m else (
            base_image if base_image and base_image != "auto" else "python:3.11-slim")
        return setup_sh, resolved

    def _relay_token_telemetry(self, run_v3_stdout: str) -> None:
        """The react loop prints one ``[Tokens] …`` line per LLM call, but run_v3_e2e's stdout is
        captured here (not the worker's). Re-emit those lines to THIS process's stdout — which the
        ratbench scheduler captures into the per-repo ``run.log`` where unified_metrics' arm0 economy
        reads them — and persist a ``usage.json`` sidecar the case-study consolidator picks up."""
        total = 0
        for line in (run_v3_stdout or "").splitlines():
            if line.startswith("[Tokens]"):
                print(line)                       # → worker stdout → output/<repo>/run.log
                mt = _TOKENS_TOTAL_RE.search(line)
                if mt:
                    total += int(mt.group(1))
        if total:
            try:
                (self.output_dir / "usage.json").write_text(
                    json.dumps({"total_tokens": total}))
            except OSError:
                pass

    def _read_services_script(self) -> str:
        """The service-start artifact ``_run_v3`` asked run_v3_e2e to write
        (V3_INCLUDE_SERVICES=1 only). "" when absent — the flag is off, or the
        graph had no reciped SERVICE node, or ``_run_v3`` was overridden by a
        test double that never invoked the real subprocess (so no file exists
        under ``self.output_dir``); every one of those cases must leave the
        Dockerfile untouched, which returning "" here guarantees."""
        path = self.output_dir / "services_start.sh"
        if not path.exists():
            return ""
        return path.read_text()

    def _render_dockerfile(
        self, base_image: str, repo_url: str, include_services: bool = False,
        pin_sha: str | None = None,
    ) -> str:
        """Self-contained Dockerfile: clone the repo into /testbed (same default
        HEAD run_v3 analyzed) and run the certified install-only setup.sh with
        CWD=/testbed so the trailing ``pip install -e .`` resolves.

        ``pin_sha`` (repair-ablation): FULL clone (history + tags) checked out at
        construction's commit, so the tested tree matches the one the react arm just
        repaired AND matches what construction measured (default: shallow current HEAD).

        ``include_services`` (default False — byte-identical to before) adds an
        ENTRYPOINT that starts every SERVICE node's daemon before handing off to
        the harness's ``tail -f /dev/null`` (see build_script.render_service_
        start_script's module docstring for why this can't just be another RUN
        line: setup.sh runs at BUILD time, but the daemon needs to be alive in
        the LATER container the harness `docker run -d`s and `docker exec`s
        pytest into). ``docker run -d ... tail -f /dev/null`` does not pass
        ``--entrypoint``, so this ENTRYPOINT survives and runs first, blocking
        until every probe passes, before ``exec "$@"`` hands off to ``tail``."""
        if pin_sha:
            clone_step = (f"# Full clone (history + tags) pinned to construction's commit.\n"
                          f"RUN git clone {repo_url} /testbed \\\n"
                          f"        && git -C /testbed checkout --detach {pin_sha}")
        else:
            clone_step = (f"# Fresh clone of the repo run_v3 analyzed.\n"
                          f"RUN git clone --depth=1 {repo_url} /testbed")
        df = f"""FROM {base_image}
WORKDIR /testbed

# git for cloning (slim bases omit it); keep the layer lean.
RUN command -v git >/dev/null 2>&1 || (apt-get update \\
        && apt-get install -y --no-install-recommends git \\
        && rm -rf /var/lib/apt/lists/*)

{clone_step}

# The certified install-only script (system tier -> pinned pip closure ->
# editable install of the repo). CWD is /testbed so `pip install -e .` resolves.
COPY setup.sh /tmp/v3_setup.sh
RUN bash /tmp/v3_setup.sh
"""
        if include_services:
            df += """
# V3_INCLUDE_SERVICES=1: start each SERVICE node's daemon, wait for its probe,
# then exec the container's original command — the ENTRYPOINT seam so
# `docker run -d <image> tail -f /dev/null` brings every service up before any
# later `docker exec ... pytest` call reaches the container.
COPY services_start.sh /v3_start_services.sh
RUN chmod +x /v3_start_services.sh
ENTRYPOINT ["/bin/bash", "/v3_start_services.sh"]
"""
        return df
