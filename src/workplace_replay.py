import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

from agent import DockerAgent
from src.constants import DEFAULT_LLM_MODEL
from src.observation_compressor import RunTokenLedger
from src.synthesizer import Synthesizer
from src.verification_bundle import derive_supported_verification_bundle


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_selected_base_image_from_workplace(workplace: Path) -> Optional[str]:
    summary_path = workplace / "logs" / "image_selector_logs" / "summary.json"
    summary = load_json(summary_path) or {}
    selected_image = str(summary.get("selected_image") or "").strip()
    return selected_image or None


def load_platform_override_from_workplace(workplace: Path) -> Optional[str]:
    summary_path = workplace / "logs" / "image_selector_logs" / "summary.json"
    summary = load_json(summary_path) or {}
    platform_override = str(summary.get("platform_override") or "").strip()
    if platform_override:
        return platform_override

    log_dir = workplace / "logs" / "image_selector_logs"
    if not log_dir.is_dir():
        return None

    for log_path in sorted(log_dir.glob("*.md")):
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for arch_note in re.findall(r"<arch_note>(.*?)</arch_note>", content, re.IGNORECASE | re.DOTALL):
            match = re.search(r"(linux/(?:amd64|arm64))", arch_note, re.IGNORECASE)
            if match:
                return match.group(1).lower()
    return None


def infer_base_image_from_dockerfile_text(dockerfile_text: str) -> Optional[str]:
    for line in (dockerfile_text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"FROM\s+([^\s]+)", stripped, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def infer_workdir_from_dockerfile_text(dockerfile_text: str) -> str:
    for line in (dockerfile_text or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("WORKDIR "):
            return stripped.split(None, 1)[1].strip()
    return "/app"


def create_openai_client_from_env() -> OpenAI:
    api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("MINIMAX_API_BASE") or os.getenv("OPENAI_API_BASE")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY or OPENAI_API_KEY not found in environment variables.")
    return OpenAI(api_key=api_key, base_url=base_url if base_url else None)


def build_minimal_recipe_run_summary(run_summary: dict[str, Any]) -> dict[str, Any]:
    run_summary = run_summary or {}
    supported_bundle = derive_supported_verification_bundle(run_summary)
    return {
        "repo_url": run_summary.get("repo_url"),
        "verification_bundle": supported_bundle,
        "verified_runtime_preparation_commands": supported_bundle.get("runtime_preparation_commands") or [],
        "verified_test_commands": supported_bundle.get("test_commands") or [],
        "verified_test_command": (
            supported_bundle.get("test_commands") or [None]
        )[-1],
        "successful_actions": run_summary.get("successful_actions") or [],
        "failed_actions": run_summary.get("failed_actions") or [],
        "required_local_services": sorted(run_summary.get("required_local_services") or []),
    }


def resynthesize_dockerfile_from_existing_workplace(
    workplace: str | Path,
    model: str = DEFAULT_LLM_MODEL,
    base_image: Optional[str] = None,
    workdir: Optional[str] = None,
    client: Optional[Any] = None,
    synthesizer: Optional[Synthesizer] = None,
) -> dict[str, Any]:
    workplace_path = Path(workplace).resolve()
    run_summary_path = workplace_path / "agent_run_summary.json"
    run_summary = load_json(run_summary_path)
    if not run_summary:
        raise FileNotFoundError(f"Run summary not found: {run_summary_path}")

    setup_log_dir = workplace_path / "logs" / "setup_logs"
    if not setup_log_dir.is_dir():
        raise FileNotFoundError(f"Setup log directory not found: {setup_log_dir}")

    dockerfile_path = workplace_path / "Dockerfile"
    existing_dockerfile_text = (
        dockerfile_path.read_text(encoding="utf-8")
        if dockerfile_path.exists()
        else ""
    )

    resolved_base_image = (
        base_image
        or load_selected_base_image_from_workplace(workplace_path)
        or infer_base_image_from_dockerfile_text(existing_dockerfile_text)
        or "python:3.10"
    )
    resolved_workdir = workdir or infer_workdir_from_dockerfile_text(existing_dockerfile_text)
    resolved_client = client or create_openai_client_from_env()
    resolved_synthesizer = synthesizer or Synthesizer(
        base_image=resolved_base_image,
        workdir=resolved_workdir,
    )

    replay_agent = DockerAgent.__new__(DockerAgent)
    replay_agent.client = resolved_client
    replay_agent.model = model
    replay_agent.synthesizer = resolved_synthesizer
    replay_agent.setup_log_dir = str(setup_log_dir)
    replay_agent.run_token_ledger = RunTokenLedger()
    replay_agent.memory_stats = {"errors": []}
    replay_agent.agent_steps = []

    setup_log_summary_text = replay_agent._build_setup_log_summary_text()
    minimal_run_summary = build_minimal_recipe_run_summary(run_summary)
    verification_bundle = minimal_run_summary.get("verification_bundle") or {
        "runtime_preparation_commands": [],
        "test_commands": [],
    }
    recipe_input = {
        "task": {
            "repo_url": minimal_run_summary.get("repo_url"),
            "base_commit": run_summary.get("base_commit"),
            "base_image": resolved_base_image,
            "workdir": resolved_workdir,
            "language": run_summary.get("language", ""),
            "required_local_services": minimal_run_summary.get("required_local_services") or [],
        },
        "final_verification_bundle": verification_bundle,
        "setup_log_summary_text": setup_log_summary_text,
        "agent_run_summary": minimal_run_summary,
    }

    result = resolved_synthesizer.synthesize_build_recipe(
        resolved_client,
        model,
        recipe_input,
        log_dir=str(setup_log_dir),
    )
    dockerfile_text = None
    if not result.error:
        dockerfile_text = resolved_synthesizer.generate_dockerfile(file_path=str(dockerfile_path))

    resynthesis_metadata = {
        "reused_existing_workplace": True,
        "model": model,
        "base_image": resolved_base_image,
        "workdir": resolved_workdir,
        "setup_log_summary_path": str(setup_log_dir / "setup_log_summary.md"),
        "recipe_synthesis_log_path": str(setup_log_dir / "recipe_synthesis.md"),
        "summary_token_usage": replay_agent.run_token_ledger.recipe.__dict__,
        "recipe_token_usage": result.usage,
        "dockerfile_generated": dockerfile_text is not None,
    }
    run_summary["build_recipe"] = result.recipe
    run_summary["build_recipe_source"] = result.source
    run_summary["build_recipe_error"] = result.error
    run_summary["resynthesis"] = resynthesis_metadata
    run_summary_path.write_text(
        json.dumps(run_summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "workplace": str(workplace_path),
        "run_summary_path": str(run_summary_path),
        "dockerfile_path": str(dockerfile_path),
        "build_recipe_source": result.source,
        "build_recipe_error": result.error,
        "base_image": resolved_base_image,
        "workdir": resolved_workdir,
        "setup_log_summary_path": str(setup_log_dir / "setup_log_summary.md"),
        "recipe_synthesis_log_path": str(setup_log_dir / "recipe_synthesis.md"),
        "dockerfile_generated": dockerfile_text is not None,
        "summary_token_usage": replay_agent.run_token_ledger.recipe.__dict__,
        "recipe_token_usage": result.usage,
    }
