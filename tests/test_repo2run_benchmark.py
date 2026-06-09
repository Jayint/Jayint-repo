import json
from pathlib import Path
from types import SimpleNamespace

from run_repo2run_benchmark import (
    REPO2RUN_PDM_COLLECT_COMMAND,
    REPO2RUN_POETRY_COLLECT_COMMAND,
    REPO2RUN_PYTEST_COLLECT_COMMAND,
    REPO2RUN_UV_COLLECT_COMMAND,
    build_agent_command,
    build_dockerfile_repair_input,
    classify_test_execution,
    derive_repo2run_collect_commands,
    docker_build_failed_due_to_unavailable_daemon,
    ensure_eval_dockerignore_includes_test_artifacts,
    evaluate_built_image,
    extract_observed_pip_install_constraints_from_text,
    extract_dockerfile_repair_json,
    infer_workdir_from_dockerfile,
    normalize_eval_dockerfile_for_replay,
    prepare_eval_build_context,
    render_eval_dockerfile,
    repair_dockerfile_for_missing_python_modules,
    repair_dockerfile_with_llm,
    resolve_benchmark_platform,
    run_command,
    select_repo2run_collect_command_from_run_summary,
    should_add_postgres_host_alias,
    should_use_agent_dockerfile,
    split_heavy_pip_install_replay_commands,
    write_instance_debug_artifacts,
    write_json,
)
from src.workplace_replay import (
    build_minimal_recipe_run_summary,
    infer_base_image_from_dockerfile_text,
    load_platform_override_from_workplace,
    load_selected_base_image_from_workplace,
    resynthesize_dockerfile_from_existing_workplace,
)


def test_render_eval_dockerfile_injects_repo_copy_after_workdir():
    agent_dockerfile = """FROM python:3.11
WORKDIR /app

RUN pip install -e .
"""

    rendered = render_eval_dockerfile(agent_dockerfile)

    assert "RUN (python -m pip install pytest pytest-xdist poetry" in rendered
    assert "WORKDIR /app\nCOPY . /app\n\nRUN pip install -e ." in rendered


def test_infer_workdir_expands_legacy_env_variable():
    dockerfile = """FROM python:3.11
ENV APP_HOME /app
WORKDIR ${APP_HOME}
"""

    assert infer_workdir_from_dockerfile(dockerfile) == "/app"


def test_normalize_eval_dockerfile_converts_export_path_to_env():
    normalized = normalize_eval_dockerfile_for_replay(
        'FROM python:3.12\nRUN export PATH="$HOME/.local/bin:$PATH"\nRUN uv --version\n'
    )

    assert 'ENV PATH="/root/.local/bin:${PATH}"' in normalized
    assert 'RUN export PATH=' not in normalized
    assert "RUN uv --version" in normalized


def test_normalize_eval_dockerfile_wraps_bare_uv_pip_install_with_curl_fallback():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\nRUN pip install uv\n"
    )

    assert "RUN JAYINT_PIP_ATTEMPT=1;" in normalized
    assert "/bin/sh -lc 'pip install uv'" in normalized
    assert "https://astral.sh/uv/install.sh" in normalized
    assert "RUN pip install uv\n" not in normalized


def test_normalize_eval_dockerfile_replaces_uv_shell_installer_with_pip_and_curl_fallback():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\nRUN curl -LsSf https://astral.sh/uv/install.sh | sh\n"
    )

    assert "RUN JAYINT_PIP_ATTEMPT=1;" in normalized
    assert "/bin/sh -lc 'pip install uv'" in normalized
    assert "https://astral.sh/uv/install.sh" in normalized


def test_extract_observed_pip_install_constraints_uses_later_successful_versions():
    constraints = extract_observed_pip_install_constraints_from_text(
        "Successfully installed gradio-5.23.1 gradio-client-1.8.0 jaxtyping-0.3.7\n"
        "WARNING: ignored\n"
        "Successfully installed jaxtyping-0.2.38\n"
    )

    assert constraints["gradio"] == "5.23.1"
    assert constraints["gradio-client"] == "1.8.0"
    assert constraints["jaxtyping"] == "0.2.38"


def test_extract_observed_pip_install_constraints_does_not_cross_into_apt_output():
    constraints = extract_observed_pip_install_constraints_from_text(
        "Successfully installed torch-2.12.0.\n"
        "Observation: Reading package lists...\n"
        "The following additional packages will be installed:\n"
        "  gir1.2-glib-2.0-dev libgio-2.0-dev-bin libxcb-dri3-0\n"
        "Setting up libglib2.0-dev-bin (2.84.4-3~deb13u3) ...\n"
        "Successfully installed mujoco-3.2.6 robosuite-1.5.0\n"
    )

    assert "torch" not in constraints
    assert "gir1-2-glib" not in constraints
    assert "libgio" not in constraints
    assert "libxcb-dri3" not in constraints
    assert constraints == {"mujoco": "3.2.6", "robosuite": "1.5.0"}


def test_normalize_eval_dockerfile_adds_observed_constraints_to_editable_install_only():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN python -m pip install -e \".[test]\" pytest-shard\n"
        "RUN python -m pip install \"jaxtyping<0.3.0\"\n",
        pip_constraints={
            "gradio": "5.23.1",
            "gradio-client": "1.8.0",
            "jaxtyping": "0.2.38",
        },
    )

    assert "RUN printf '%s\\n' gradio==5.23.1 gradio-client==1.8.0 jaxtyping==0.2.38" in normalized
    assert "--constraint /tmp/jayint-pip-constraints.txt" in normalized
    assert normalized.count("--constraint /tmp/jayint-pip-constraints.txt") == 1
    assert "'jaxtyping<0.3.0' --constraint" not in normalized


def test_normalize_eval_dockerfile_splits_broad_torch_to_cpu_index():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.9\n"
        "RUN pip install einops torch x-transformers vector-quantize-pytorch pytest -q\n"
    )

    assert "--index-url https://download.pytorch.org/whl/cpu torch" in normalized
    assert "pip install -q einops x-transformers vector-quantize-pytorch pytest" in normalized


def test_normalize_eval_dockerfile_adds_no_deps_to_known_force_reinstall():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.9\n"
        "RUN pip install einops torch x-transformers vector-quantize-pytorch pytest -q\n"
        "RUN pip install 'x-transformers>=1.30.20' -q --force-reinstall\n"
    )

    assert "--force-reinstall --no-deps" in normalized
    assert "x-transformers>=1.30.20" in normalized


def test_normalize_eval_dockerfile_upgrades_generated_uv_pip_retry_with_curl_fallback():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\n"
        "RUN JAYINT_PIP_ATTEMPT=1; while false; do :; done; /bin/sh -lc 'pip install uv'\n"
    )

    assert "RUN JAYINT_PIP_ATTEMPT=1;" in normalized
    assert "/bin/sh -lc 'pip install uv'" in normalized
    assert "https://astral.sh/uv/install.sh" in normalized


def test_normalize_eval_dockerfile_wraps_non_uv_pip_install_without_curl_fallback():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\nRUN pip install pybullet\n"
    )

    assert "RUN JAYINT_PIP_ATTEMPT=1;" in normalized
    assert "/bin/sh -lc 'pip install pybullet'" in normalized
    assert "https://astral.sh/uv/install.sh" not in normalized


def test_split_heavy_pip_install_replay_commands_splits_sentence_transformers():
    commands = split_heavy_pip_install_replay_commands(
        "pip install langchain-chroma sentence-transformers"
    )

    assert commands == [
        "pip install langchain-chroma",
        "pip install sentence-transformers --no-deps",
    ]


def test_normalize_eval_dockerfile_installs_sentence_transformers_without_deps():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\nRUN pip install langchain-chroma sentence-transformers\n"
    )

    assert "/bin/sh -lc 'pip install langchain-chroma'" in normalized
    assert "/bin/sh -lc 'pip install sentence-transformers --no-deps'" in normalized
    assert "/bin/sh -lc 'pip install langchain-chroma sentence-transformers'" not in normalized


def test_normalize_eval_dockerfile_rewrites_existing_pip_retry_for_sentence_transformers():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\n"
        "RUN JAYINT_PIP_ATTEMPT=1; while false; do :; done; "
        "/bin/sh -lc 'pip install langchain-chroma sentence-transformers'\n"
    )

    assert "/bin/sh -lc 'pip install langchain-chroma'" in normalized
    assert "/bin/sh -lc 'pip install sentence-transformers --no-deps'" in normalized
    assert "/bin/sh -lc 'pip install langchain-chroma sentence-transformers'" not in normalized


def test_normalize_eval_dockerfile_drops_pypi_reinstall_after_local_source_install():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN git clone --depth 1 https://example.test/bddl.git /tmp/bddl "
        "&& pip install /tmp/bddl\n"
        "RUN pip install bddl pytest\n"
    )

    assert "pip install /tmp/bddl" in normalized
    assert "/bin/sh -lc 'pip install pytest'" in normalized
    assert "/bin/sh -lc 'pip install bddl pytest'" not in normalized
    assert "/bin/sh -lc 'pip install bddl'" not in normalized


def test_normalize_eval_dockerfile_drops_generated_pypi_retry_after_local_source_install():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN git clone --depth 1 https://example.test/bddl.git /tmp/bddl "
        "&& pip install /tmp/bddl\n"
        "RUN JAYINT_PIP_ATTEMPT=1; while false; do :; done; "
        "/bin/sh -lc 'pip install bddl>=3.6.0'\n"
    )

    assert "pip install /tmp/bddl" in normalized
    assert "pip install bddl>=3.6.0" not in normalized


def test_normalize_eval_dockerfile_adds_no_deps_to_cuda_skipped_source_install():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN cd /tmp/mamba && MAMBA_SKIP_CUDA_BUILD=TRUE "
        "pip install . --no-build-isolation\n"
        "RUN cd /tmp/causal-conv1d && CAUSAL_CONV1D_SKIP_CUDA_BUILD=TRUE "
        "python -m pip install . --no-build-isolation\n"
    )

    assert (
        "RUN cd /tmp/mamba && MAMBA_SKIP_CUDA_BUILD=TRUE "
        "pip install . --no-build-isolation --no-deps"
    ) in normalized
    assert (
        "RUN cd /tmp/causal-conv1d && CAUSAL_CONV1D_SKIP_CUDA_BUILD=TRUE "
        "python -m pip install . --no-build-isolation --no-deps"
    ) in normalized


def test_normalize_eval_dockerfile_does_not_duplicate_cuda_skipped_no_deps():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN cd /tmp/mamba && MAMBA_SKIP_CUDA_BUILD=TRUE "
        "pip install . --no-build-isolation --no-deps\n"
    )

    assert normalized.count("--no-deps") == 1


def test_normalize_eval_dockerfile_drops_broad_torch_when_later_mosaicml_installs_torch():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN pip install torch>=2.2.0 einops omegaconf transformers\n"
        "RUN pip install mosaicml\n"
    )

    assert "/bin/sh -lc 'pip install einops omegaconf transformers'" in normalized
    assert "torch>=2.2.0" not in normalized
    assert "/bin/sh -lc 'pip install mosaicml'" in normalized


def test_normalize_eval_dockerfile_keeps_broad_torch_without_later_replacement():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\nRUN pip install torch>=2.2.0 einops\n"
    )

    assert "torch>=2.2.0" in normalized


def test_normalize_eval_dockerfile_drops_generated_broad_torch_retry_when_replaced():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN JAYINT_PIP_ATTEMPT=1; while false; do :; done; "
        "/bin/sh -lc 'pip install torch>=2.2.0 einops'\n"
        "RUN pip install torch==2.7.0 --upgrade --force-reinstall\n"
    )

    assert "/bin/sh -lc 'pip install torch==2.7.0 einops'" in normalized
    assert "torch>=2.2.0" not in normalized
    assert "/bin/sh -lc 'pip install torch==2.7.0 --upgrade --force-reinstall'" not in normalized


def test_normalize_eval_dockerfile_replaces_broad_torch_with_later_exact_pin():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN pip install torch>=2.2.0 einops omegaconf transformers\n"
        "RUN pip install mosaicml-streaming einops omegaconf transformers\n"
        "RUN pip install torch==2.7.0 --upgrade --force-reinstall\n"
    )

    assert (
        "/bin/sh -lc 'pip install torch==2.7.0 torchvision==0.22.0 "
        "einops omegaconf transformers'"
    ) in normalized
    assert "torch>=2.2.0" not in normalized
    assert normalized.count("torch==2.7.0") == 1


def test_normalize_eval_dockerfile_adds_torchvision_pin_for_mosaicml_stack():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN pip install torch==2.7.0 einops omegaconf transformers\n"
        "RUN pip install mosaicml-streaming einops omegaconf transformers\n"
    )

    assert "/bin/sh -lc 'pip install torch==2.7.0 torchvision==0.22.0 einops" in normalized


def test_normalize_eval_dockerfile_keeps_existing_torchvision_pin():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN pip install torch==2.7.0 torchvision==0.22.0 einops\n"
        "RUN pip install mosaicml-streaming\n"
    )

    assert normalized.count("torchvision==0.22.0") == 1


def test_normalize_eval_dockerfile_fixes_generated_cuda_skipped_retry_no_deps():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN JAYINT_PIP_ATTEMPT=1; while false; do :; done; "
        "/bin/sh -lc 'cd /tmp/mamba && MAMBA_SKIP_CUDA_BUILD=TRUE "
        "pip install . --no-build-isolation' --no-deps\n"
    )

    assert (
        "/bin/sh -lc 'cd /tmp/mamba && MAMBA_SKIP_CUDA_BUILD=TRUE "
        "pip install . --no-build-isolation --no-deps'"
    ) in normalized
    assert "--no-build-isolation' --no-deps" not in normalized


def test_normalize_eval_dockerfile_drops_unused_cuda_installer_when_cuda_builds_are_skipped():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "RUN mkdir -p /tmp/cuda\n"
        "RUN cd /tmp/cuda && curl -O "
        "https://developer.download.nvidia.com/compute/cuda/12.5.0/local.run\n"
        "RUN cd /tmp/mamba && MAMBA_SKIP_CUDA_BUILD=TRUE pip install . --no-build-isolation\n"
    )

    assert "mkdir -p /tmp/cuda" not in normalized
    assert "developer.download.nvidia.com/compute/cuda" not in normalized
    assert "MAMBA_SKIP_CUDA_BUILD=TRUE pip install . --no-build-isolation --no-deps" in normalized


def test_normalize_eval_dockerfile_rewrites_absolute_tests_redirect_to_workdir():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.10\n"
        "WORKDIR /app\n"
        "RUN printf 'patch' > /tests/conftest.py\n"
    )

    assert "> /app/tests/conftest.py" in normalized
    assert "> /tests/conftest.py" not in normalized


def test_normalize_eval_dockerfile_encodes_raw_multiline_run_blocks():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.9\n"
        "WORKDIR /app\n"
        'RUN python -c "\n'
        "import sys\n"
        "print('ok')\n"
        '"\n'
        "RUN pip install pytest\n"
    )

    assert "RUN printf '%s'" in normalized
    assert "base64 -d > /tmp/jayint_eval_run_1.sh" in normalized
    assert 'RUN python -c "' not in normalized
    assert "\nimport sys\n" not in normalized
    assert "/bin/sh -lc 'pip install pytest'" in normalized


def test_normalize_eval_dockerfile_does_not_fold_next_dockerfile_instruction_into_multiline_run():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.9\n"
        "WORKDIR /app\n"
        "RUN printf 'broken single quoted text with ['inner']\n"
        "still part of first run\n"
        "RUN pip install pytest\n"
    )

    assert normalized.count("base64 -d > /tmp/jayint_eval_run_1.sh") == 1
    assert "/bin/sh -lc 'pip install pytest'" in normalized


def test_normalize_eval_dockerfile_wraps_apt_install_with_command_retry():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\nRUN apt-get install -y libgl1 libglib2.0-0\n"
    )

    assert "RUN JAYINT_APT_ATTEMPT=1;" in normalized
    assert "JAYINT_APT_MAX_ATTEMPTS=3" in normalized
    assert 'exit "$JAYINT_APT_STATUS"' in normalized
    assert "JAYINT_PIP_STATUS" not in normalized
    assert "/bin/sh -lc 'apt-get update && apt-get install -y libgl1 libglib2.0-0'" in normalized
    assert "RUN apt-get install -y libgl1" not in normalized


def test_normalize_eval_dockerfile_repairs_malformed_generated_apt_retry_status():
    malformed = (
        "FROM python:3.12\n"
        "RUN JAYINT_APT_ATTEMPT=1; JAYINT_APT_MAX_ATTEMPTS=3; JAYINT_APT_STATUS=1; "
        "while [ \"$JAYINT_APT_ATTEMPT\" -le \"$JAYINT_APT_MAX_ATTEMPTS\" ]; do "
        "DEBIAN_FRONTEND=noninteractive /bin/sh -lc 'apt-get update && apt-get install -y postgresql' "
        "&& JAYINT_APT_STATUS=0 && break; JAYINT_APT_STATUS=$?; "
        "done; exit \"$JAYINT_PIP_STATUS\"\n"
    )

    normalized = normalize_eval_dockerfile_for_replay(malformed)

    assert 'exit "$JAYINT_APT_STATUS"' in normalized
    assert "JAYINT_PIP_STATUS" not in normalized


def test_normalize_eval_dockerfile_wraps_multiline_apt_install_without_orphan_packages():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\n"
        "RUN apt-get update && apt-get install -y \\\n"
        "    build-essential \\\n"
        "    libmagic-dev \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
    )

    assert "RUN JAYINT_APT_ATTEMPT=1;" in normalized
    assert (
        "/bin/sh -lc 'apt-get update && apt-get install -y build-essential "
        "libmagic-dev && rm -rf /var/lib/apt/lists/*'"
    ) in normalized
    assert "\n    build-essential \\" not in normalized
    assert "\n    libmagic-dev \\" not in normalized


def test_normalize_eval_dockerfile_repairs_generated_apt_retry_with_orphan_packages():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\n"
        "RUN JAYINT_APT_ATTEMPT=1; JAYINT_APT_MAX_ATTEMPTS=3; JAYINT_APT_STATUS=1; "
        "while [ \"$JAYINT_APT_ATTEMPT\" -le \"$JAYINT_APT_MAX_ATTEMPTS\" ]; do "
        "rm -rf /var/lib/apt/lists/*; "
        "DEBIAN_FRONTEND=noninteractive /bin/sh -lc "
        "'apt-get update && apt-get install -y --no-install-recommends \\' "
        "&& JAYINT_APT_STATUS=0 && break; JAYINT_APT_STATUS=$?; "
        "done; exit \"$JAYINT_APT_STATUS\"\n"
        "    gcc \\\n"
        "    && rm -rf /var/lib/apt/lists/*\n"
    )

    assert "RUN JAYINT_APT_ATTEMPT=1;" in normalized
    assert (
        "/bin/sh -lc 'apt-get update && apt-get install -y --no-install-recommends "
        "gcc && rm -rf /var/lib/apt/lists/*'"
    ) in normalized
    assert "\n    gcc \\" not in normalized
    assert "\n    && rm -rf /var/lib/apt/lists/*" not in normalized


def test_normalize_eval_dockerfile_drops_poetry_lock_when_lockfile_is_copied():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.11\n"
        "WORKDIR /app\n"
        "COPY pyproject.toml poetry.lock /app/\n"
        "RUN poetry lock && \\\n"
        "    poetry config virtualenvs.create false\n"
    )

    assert "RUN poetry config virtualenvs.create false" in normalized
    assert "poetry lock" not in normalized


def test_normalize_eval_dockerfile_drops_poetry_lock_when_context_is_copied():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.11\n"
        "WORKDIR /app\n"
        "COPY . /app\n"
        "RUN poetry lock && \\\n"
        "    poetry config virtualenvs.create false\n"
    )

    assert "RUN poetry config virtualenvs.create false" in normalized
    assert "poetry lock" not in normalized


def test_normalize_eval_dockerfile_wraps_multiline_pip_install_without_orphan_packages():
    normalized = normalize_eval_dockerfile_for_replay(
        "FROM python:3.12\n"
        "RUN pip install \\\n"
        "    pytest \\\n"
        "    numpy \\\n"
        "    --no-deps\n"
    )

    assert "RUN JAYINT_PIP_ATTEMPT=1;" in normalized
    assert "/bin/sh -lc 'pip install pytest numpy --no-deps'" in normalized
    assert "\n    pytest \\" not in normalized
    assert "\n    numpy \\" not in normalized


def test_write_json_decodes_bytes(tmp_path):
    output_path = tmp_path / "result.json"

    write_json(output_path, {"docker_build": {"stdout": b"ok\n", "stderr": b"\xffbad"}})

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["docker_build"]["stdout"] == "ok\n"
    assert payload["docker_build"]["stderr"] == "\ufffdbad"


def test_run_command_decodes_timeout_streams(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(
            cmd=args[0],
            timeout=1,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr("run_repo2run_benchmark.subprocess.run", fake_run)

    result = run_command(["docker", "build"], cwd=tmp_path, timeout_seconds=1)

    assert result["timed_out"] is True
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"


def test_docker_daemon_unavailable_does_not_look_like_repairable_dockerfile_failure():
    assert docker_build_failed_due_to_unavailable_daemon(
        {
            "returncode": 1,
            "stdout": "",
            "stderr": (
                "ERROR: Cannot connect to the Docker daemon at "
                "unix:///Users/test/.docker/run/docker.sock. Is the docker daemon running?"
            ),
        }
    )
    assert not docker_build_failed_due_to_unavailable_daemon(
        {"returncode": 1, "stdout": "", "stderr": "Dockerfile.eval:8 unknown instruction: gcc"}
    )


def test_repair_dockerfile_for_missing_module_prefers_declared_requirement(tmp_path):
    (tmp_path / "requirements.txt").write_text("paddleocr==2.7.3\n", encoding="utf-8")
    dockerfile = (
        "FROM ubuntu:22.04\n"
        "WORKDIR /app\n"
        "RUN pip3 install paddleocr\n"
        "CMD pytest --collect-only -q --disable-warnings\n"
    )
    repaired, requirements = repair_dockerfile_for_missing_python_modules(
        dockerfile,
        {
            "results": [
                {
                    "execution": {
                        "stdout": "ModuleNotFoundError: No module named 'ppocr'",
                        "stderr": "",
                    }
                }
            ]
        },
        tmp_path,
    )

    assert requirements == ["paddleocr==2.7.3"]
    assert "/bin/sh -lc 'pip3 install paddleocr==2.7.3'" in repaired
    assert repaired.index("paddleocr==2.7.3") < repaired.index("CMD pytest")


def test_repair_dockerfile_for_missing_module_uses_poetry_lock_version(tmp_path):
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "transitions"\nversion = "0.9.2"\n',
        encoding="utf-8",
    )
    dockerfile = (
        "FROM python:3.10\n"
        "WORKDIR /workspaces/dspygen\n"
        "RUN pip install --no-deps -e .\n"
        "CMD poetry run pytest --collect-only -q --disable-warnings\n"
    )
    repaired, requirements = repair_dockerfile_for_missing_python_modules(
        dockerfile,
        {
            "results": [
                {
                    "execution": {
                        "stdout": "ModuleNotFoundError: No module named 'transitions'",
                        "stderr": "",
                    }
                }
            ]
        },
        tmp_path,
    )

    assert requirements == ["transitions==0.9.2"]
    assert "/bin/sh -lc 'pip install transitions==0.9.2'" in repaired


def test_extract_dockerfile_repair_json_parses_full_dockerfile():
    parsed = extract_dockerfile_repair_json(
        "```json\n"
        "{\"dockerfile\":\"FROM python:3.11\\nWORKDIR /app\\nRUN pip install robomimic\\n\","
        "\"rationale\":\"restore missing sandbox install\",\"confidence\":\"high\"}"
        "\n```"
    )

    assert parsed["dockerfile"].startswith("FROM python:3.11")
    assert "RUN pip install robomimic" in parsed["dockerfile"]
    assert parsed["confidence"] == "high"


def test_repair_dockerfile_with_llm_writes_log_and_returns_replacement(tmp_path):
    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(
                                {
                                    "dockerfile": "FROM python:3.11\nWORKDIR /app\nRUN pip install pybullet\n",
                                    "rationale": "restore omitted successful install",
                                    "confidence": "medium",
                                }
                            )
                        )
                    )
                ],
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    result = repair_dockerfile_with_llm(
        client=fake_client,
        model="fake-model",
        repair_input={
            "dockerfile": "FROM python:3.11\nWORKDIR /app\n",
            "docker_build": {"returncode": 0},
            "test_execution": [],
        },
        artifact_dir=tmp_path,
        round_index=1,
    )

    assert result["error"] is None
    assert result["usage"]["total_tokens"] == 5
    assert "RUN pip install pybullet" in result["dockerfile_text"]
    assert Path(result["log_path"]).read_text(encoding="utf-8").count("Dockerfile repair") >= 1


def test_build_dockerfile_repair_input_includes_failure_logs_and_trajectory():
    repair_input = build_dockerfile_repair_input(
        instance={
            "instance_id": "owner__repo",
            "full_name": "owner/repo",
            "sha": "abc123",
            "repo_url": "https://github.com/owner/repo.git",
        },
        workdir="/app",
        dockerfile_text="FROM python:3.11\nWORKDIR /app\n",
        run_summary={
            "successful_actions": [{"step_index": 4, "command": "pip install robomimic"}],
            "failed_actions": [{"step_index": 5, "command": "pytest --collect-only"}],
            "build_recipe": {
                "source": "agent_run_summary",
                "build_commands": ["pip install robomimic"],
                "runtime_commands": [],
            },
            "verification_bundle": {
                "runtime_preparation_commands": [],
                "test_commands": ["pytest --collect-only -q --disable-warnings"],
            },
        },
        runtime_commands=[],
        test_commands=["pytest --collect-only -q --disable-warnings"],
        docker_build={"returncode": 0, "timed_out": False, "stdout": "", "stderr": ""},
        test_execution={
            "results": [
                {
                    "test_command": "pytest --collect-only -q --disable-warnings",
                    "classification": {"effective": False, "reason": "collection_or_env_error"},
                    "execution": {
                        "returncode": 2,
                        "timed_out": False,
                        "stdout": "",
                        "stderr": "ModuleNotFoundError: No module named 'robomimic'",
                    },
                }
            ]
        },
    )

    assert repair_input["agent_run_summary"]["successful_actions"][0]["command"] == "pip install robomimic"
    assert repair_input["agent_run_summary"]["build_recipe"]["build_commands"] == ["pip install robomimic"]
    assert repair_input["test_execution"][0]["classification"]["reason"] == "collection_or_env_error"
    assert "robomimic" in repair_input["test_execution"][0]["stderr"]


def test_classify_test_execution_accepts_repo2run_collect_success():
    result = classify_test_execution(
        {
            "returncode": 0,
            "timed_out": False,
            "stdout": "tests/test_app.py::test_ok\n10 tests collected in 0.42s\n",
            "stderr": "",
        }
    )

    assert result["effective"] is True
    assert result["reason"] == "tests_collected_successfully"


def test_classify_test_execution_accepts_repo2run_no_tests_collected():
    result = classify_test_execution(
        {
            "returncode": 5,
            "timed_out": False,
            "stdout": "no tests collected in 0.02s\n",
            "stderr": "",
        }
    )

    assert result["effective"] is True
    assert result["reason"] == "no_tests_collected"


def test_classify_test_execution_rejects_internal_repo_collection_failures():
    result = classify_test_execution(
        {
            "returncode": 2,
            "timed_out": False,
            "stdout": "============================= test session starts =============================\n"
            "collected 23 items / 1 error\n",
            "stderr": "ERROR collecting tests/test_app.py\n"
            "ImportError while importing test module\n"
            "ModuleNotFoundError: No module named 'src.common.missing_module'\n",
        },
        internal_import_prefixes={"src", "tests"},
    )

    assert result["effective"] is False
    assert result["reason"] == "collection_or_env_error"


def test_classify_test_execution_rejects_external_dependency_collection_errors():
    result = classify_test_execution(
        {
            "returncode": 2,
            "timed_out": False,
            "stdout": "============================= test session starts =============================\n"
            "collected 23 items / 1 error\n",
            "stderr": "ERROR collecting tests/test_app.py\n"
            "ImportError while importing test module\n"
            "ModuleNotFoundError: No module named 'pandas'\n",
        },
        internal_import_prefixes={"src", "tests"},
    )

    assert result["effective"] is False
    assert result["reason"] == "collection_or_env_error"


def test_derive_repo2run_collect_commands_uses_plain_pytest_by_default(tmp_path):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(tmp_path)

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PYTEST_COLLECT_COMMAND]
    assert source == "repo2run_pytest_collect_only"


def test_derive_repo2run_collect_commands_uses_poetry_when_detected(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = \"demo\"\n",
        encoding="utf-8",
    )

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(tmp_path)

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_POETRY_COLLECT_COMMAND]
    assert source == "repo2run_poetry_collect_only"


def test_derive_repo2run_collect_commands_preserves_verified_runtime_preparation(tmp_path):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verification_bundle": {
                "runtime_preparation_commands": ["pg_ctlcluster 17 main start"],
                "test_commands": [REPO2RUN_POETRY_COLLECT_COMMAND],
            },
            "successful_actions": [
                {
                    "command": "pg_ctlcluster 17 main start",
                    "observation_summary": "PostgreSQL cluster started\n",
                },
                {
                    "command": REPO2RUN_POETRY_COLLECT_COMMAND,
                    "observation_summary": "12 tests collected in 0.33s\n",
                }
            ],
        },
    )

    assert runtime_commands == ["pg_ctlcluster 17 main start"]
    assert test_commands == [REPO2RUN_POETRY_COLLECT_COMMAND]
    assert source == "repo2run_poetry_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_prefers_standard_verified_command():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verification_bundle": {
                "test_commands": [REPO2RUN_PYTEST_COLLECT_COMMAND],
            },
            "successful_actions": [
                {
                    "command": REPO2RUN_PYTEST_COLLECT_COMMAND,
                    "observation_summary": "22 tests collected in 1.39s\n",
                }
            ],
        }
    )

    assert command == REPO2RUN_PYTEST_COLLECT_COMMAND
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_normalizes_redundant_cd_app_prefix():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": f"cd /app && {REPO2RUN_PYTEST_COLLECT_COMMAND}",
            "successful_actions": [
                {
                    "command": f"cd /app && {REPO2RUN_PYTEST_COLLECT_COMMAND}",
                    "observation_summary": "22 tests collected in 1.39s\n",
                }
            ],
        }
    )

    assert command == REPO2RUN_PYTEST_COLLECT_COMMAND
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_preserves_relative_cd_subdir():
    verified_command = f"cd analyzer && {REPO2RUN_PYTEST_COLLECT_COMMAND}"

    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": verified_command,
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "112 tests collected in 8.26s\n",
                }
            ],
        }
    )

    assert command == verified_command
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_normalizes_absolute_app_subdir():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": f"cd /app/analyzer && python3 -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
            "successful_actions": [
                {
                    "command": f"cd /app/analyzer && python3 -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
                    "observation_summary": "112 tests collected in 8.26s\n",
                }
            ],
        }
    )

    assert command == f"cd analyzer && {REPO2RUN_PYTEST_COLLECT_COMMAND}"
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_normalizes_python_module_pytest():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": f"python3 -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
            "successful_actions": [
                {
                    "command": f"python3 -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
                    "observation_summary": "47 tests collected in 1.84s\n",
                }
            ],
        }
    )

    assert command == REPO2RUN_PYTEST_COLLECT_COMMAND
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_preserves_uv_wrapper():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": REPO2RUN_UV_COLLECT_COMMAND,
            "successful_actions": [
                {
                    "command": REPO2RUN_UV_COLLECT_COMMAND,
                    "observation_summary": "47 tests collected in 1.84s\n",
                }
            ],
        }
    )

    assert command == REPO2RUN_UV_COLLECT_COMMAND
    assert source == "repo2run_uv_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_preserves_uv_wrapper_with_targets():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": f"{REPO2RUN_UV_COLLECT_COMMAND} .github/workflows/ tests/",
            "successful_actions": [
                {
                    "command": f"{REPO2RUN_UV_COLLECT_COMMAND} .github/workflows/ tests/",
                    "observation_summary": "221 tests collected in 1.16s\n",
                }
            ],
        }
    )

    assert command == f"{REPO2RUN_UV_COLLECT_COMMAND} .github/workflows/ tests/"
    assert source == "repo2run_uv_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_preserves_pdm_wrapper():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": REPO2RUN_PDM_COLLECT_COMMAND,
            "successful_actions": [
                {
                    "command": REPO2RUN_PDM_COLLECT_COMMAND,
                    "observation_summary": "47 tests collected in 1.84s\n",
                }
            ],
        }
    )

    assert command == REPO2RUN_PDM_COLLECT_COMMAND
    assert source == "repo2run_pdm_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_preserves_xvfb_wrapper():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": f"xvfb-run -a {REPO2RUN_PYTEST_COLLECT_COMMAND}",
            "successful_actions": [
                {
                    "command": f"xvfb-run -a {REPO2RUN_PYTEST_COLLECT_COMMAND}",
                    "observation_summary": "48 tests collected in 2.84s\n",
                }
            ],
        }
    )

    assert command == f"xvfb-run -a {REPO2RUN_PYTEST_COLLECT_COMMAND}"
    assert source == "repo2run_xvfb_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_preserves_xvfb_env_wrapper():
    verified_command = f"xvfb-run -a env PYTHONPATH=. {REPO2RUN_PYTEST_COLLECT_COMMAND}"

    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verification_bundle": {
                "test_commands": [verified_command],
            },
            "verified_test_commands": [verified_command],
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "1 test collected in 1.02s\n",
                }
            ],
        }
    )

    assert command == verified_command
    assert source == "repo2run_xvfb_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_prefers_xvfb_env_verified_command(tmp_path):
    verified_command = f"xvfb-run -a env PYTHONPATH=. {REPO2RUN_PYTEST_COLLECT_COMMAND}"

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verification_bundle": {
                "runtime_preparation_commands": [verified_command],
                "test_commands": [verified_command],
            },
            "verified_test_commands": [verified_command],
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "1 test collected in 1.02s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_xvfb_collect_only_agent_verified"


def test_select_repo2run_collect_command_from_run_summary_normalizes_xvfb_python_module_pytest():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verified_test_command": f"cd /app && xvfb-run -a python -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
            "successful_actions": [
                {
                    "command": f"cd /app && xvfb-run -a python -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
                    "observation_summary": "48 tests collected in 2.84s\n",
                }
            ],
        }
    )

    assert command == f"xvfb-run -a {REPO2RUN_PYTEST_COLLECT_COMMAND}"
    assert source == "repo2run_xvfb_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_prefers_agent_verified_standard_command_over_workspace_hint(
    tmp_path,
):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = \"demo\"\n",
        encoding="utf-8",
    )

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": REPO2RUN_PYTEST_COLLECT_COMMAND,
            "successful_actions": [
                {
                    "command": REPO2RUN_PYTEST_COLLECT_COMMAND,
                    "observation_summary": "22 tests collected in 1.39s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PYTEST_COLLECT_COMMAND]
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_prefers_python_module_pytest_over_poetry_hint(
    tmp_path,
):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = \"demo\"\n",
        encoding="utf-8",
    )

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": f"python -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
            "successful_actions": [
                {
                    "command": f"python -m {REPO2RUN_PYTEST_COLLECT_COMMAND}",
                    "observation_summary": "47 tests collected in 1.84s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PYTEST_COLLECT_COMMAND]
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_keeps_env_prefixed_verified_pytest_targets(tmp_path):
    verified_command = (
        "PYTHONPATH=/app pytest "
        "tests/test_app.py tests/unit/test_two_factor_auth.py "
        "security/test/test_authentication.py security/test/test_encryption.py "
        "ai/test/test_machine_learning.py --collect-only -q --disable-warnings"
    )

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verification_bundle": {
                "test_commands": [verified_command],
            },
            "verified_test_commands": [verified_command],
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "26 tests collected in 1.91s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_keeps_env_prefixed_python_module_pytest(tmp_path):
    verified_command = "PYTHONPATH=/app python -m pytest tests --collect-only -q --disable-warnings"

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": verified_command,
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "18 tests collected in 0.71s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_prefers_uv_wrapper_over_plain_pytest_fallback(tmp_path):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": REPO2RUN_UV_COLLECT_COMMAND,
            "successful_actions": [
                {
                    "command": REPO2RUN_UV_COLLECT_COMMAND,
                    "observation_summary": "47 tests collected in 1.84s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_UV_COLLECT_COMMAND]
    assert source == "repo2run_uv_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_prefers_uv_wrapper_with_targets_over_plain_pytest_fallback(
    tmp_path,
):
    verified_command = f"{REPO2RUN_UV_COLLECT_COMMAND} .github/workflows/ tests/"

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": verified_command,
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "221 tests collected in 1.16s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_uv_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_prefers_pdm_wrapper_over_plain_pytest_fallback(tmp_path):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": REPO2RUN_PDM_COLLECT_COMMAND,
            "successful_actions": [
                {
                    "command": REPO2RUN_PDM_COLLECT_COMMAND,
                    "observation_summary": "47 tests collected in 1.84s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PDM_COLLECT_COMMAND]
    assert source == "repo2run_pdm_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_ignores_nonstandard_agent_verified_command(tmp_path):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": "python -m pytest tests -q",
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PYTEST_COLLECT_COMMAND]
    assert source == "repo2run_pytest_collect_only"


def test_derive_repo2run_collect_commands_keeps_agent_verified_collect_with_extra_options(
    tmp_path,
):
    verified_command = f"{REPO2RUN_UV_COLLECT_COMMAND} --ignore=tests/flaky"

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": verified_command,
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "220 tests collected in 1.16s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_uv_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_keeps_agent_verified_import_mode_option(tmp_path):
    verified_command = f"{REPO2RUN_PYTEST_COLLECT_COMMAND} --import-mode=importlib"

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": verified_command,
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "135 tests collected in 2.28s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_keeps_agent_verified_venv_pytest(tmp_path):
    verified_command = ".venv/bin/pytest --collect-only -q --disable-warnings"

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": verified_command,
            "successful_actions": [
                {
                    "command": verified_command,
                    "observation_summary": "90 tests collected in 0.57s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [verified_command]
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_keeps_multiple_agent_verified_collect_commands(
    tmp_path,
):
    verified_commands = [
        "poetry run pytest tests/tests_async --collect-only -q --disable-warnings",
        "poetry run pytest tests/tests_sync --collect-only -q --disable-warnings",
    ]

    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_commands": verified_commands,
            "successful_actions": [
                {"command": verified_commands[0], "observation_summary": "1 test collected in 0.02s\n"},
                {"command": verified_commands[1], "observation_summary": "2 tests collected in 0.01s\n"},
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == verified_commands
    assert source == "repo2run_poetry_collect_only_agent_verified"


def test_derive_repo2run_collect_commands_rejects_agent_verified_collect_with_pipe(
    tmp_path,
):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": f"{REPO2RUN_PYTEST_COLLECT_COMMAND} | tail -20",
            "successful_actions": [
                {
                    "command": f"{REPO2RUN_PYTEST_COLLECT_COMMAND} | tail -20",
                    "observation_summary": "22 tests collected in 1.39s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PYTEST_COLLECT_COMMAND]
    assert source == "repo2run_pytest_collect_only"


def test_derive_repo2run_collect_commands_rejects_non_cd_shell_chain(tmp_path):
    runtime_commands, test_commands, source = derive_repo2run_collect_commands(
        tmp_path,
        {
            "verified_test_command": f"{REPO2RUN_PYTEST_COLLECT_COMMAND} && echo done",
            "successful_actions": [
                {
                    "command": f"{REPO2RUN_PYTEST_COLLECT_COMMAND} && echo done",
                    "observation_summary": "22 tests collected in 1.39s\n",
                }
            ],
        },
    )

    assert runtime_commands == []
    assert test_commands == [REPO2RUN_PYTEST_COLLECT_COMMAND]
    assert source == "repo2run_pytest_collect_only"


def test_should_use_agent_dockerfile_rejects_failed_fresh_agent_run():
    usable, reason = should_use_agent_dockerfile(
        {"returncode": 1, "timed_out": False},
        reused_existing_workplace=False,
    )

    assert usable is False
    assert reason == "agent_run_failed_or_timed_out"


def test_should_use_agent_dockerfile_rejects_unverified_unsuccessful_agent_summary():
    usable, reason = should_use_agent_dockerfile(
        {"returncode": 0, "timed_out": False},
        reused_existing_workplace=False,
        run_summary={
            "configuration_success": False,
            "verified_test_commands": [],
            "successful_test_commands": [],
            "verification_bundle": None,
        },
    )

    assert usable is False
    assert reason == "agent_configuration_unsuccessful"


def test_should_use_agent_dockerfile_allows_unsuccessful_summary_with_verified_tests():
    usable, reason = should_use_agent_dockerfile(
        {"returncode": 0, "timed_out": False},
        reused_existing_workplace=False,
        run_summary={
            "configuration_success": False,
            "verified_test_commands": [REPO2RUN_PYTEST_COLLECT_COMMAND],
        },
    )

    assert usable is True
    assert reason is None


def test_should_use_agent_dockerfile_allows_reused_existing_workplace_after_failed_prior_run():
    usable, reason = should_use_agent_dockerfile(
        {"returncode": 1, "timed_out": False},
        reused_existing_workplace=True,
        run_summary={"configuration_success": False},
    )

    assert usable is True
    assert reason is None


def test_prepare_eval_build_context_uses_clean_git_checkout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    run_command(["git", "init"], cwd=source)
    run_command(["git", "config", "user.email", "test@example.com"], cwd=source)
    run_command(["git", "config", "user.name", "Test User"], cwd=source)
    (source / "pyproject.toml").write_text("[tool.pytest]\ntestpaths = ['tests']\n", encoding="utf-8")
    (source / "tests").mkdir()
    (source / "tests" / "test_demo.py").write_text("def test_demo(): pass\n", encoding="utf-8")
    run_command(["git", "add", "."], cwd=source)
    run_command(["git", "commit", "-m", "initial"], cwd=source)

    commit = run_command(["git", "rev-parse", "--short", "HEAD"], cwd=source)["stdout"].strip()
    (source / "pyproject.toml").write_text("broken mutated state\n", encoding="utf-8")
    (source / "agent_run_summary.json").write_text("{}", encoding="utf-8")

    destination = tmp_path / "context"
    result = prepare_eval_build_context(source, destination, base_commit=commit, cwd=tmp_path)

    assert result["success"] is True
    assert result["method"] == "local_git_clone"
    assert (destination / "pyproject.toml").read_text(encoding="utf-8") == (
        "[tool.pytest]\ntestpaths = ['tests']\n"
    )
    assert not (destination / "agent_run_summary.json").exists()


def test_ensure_eval_dockerignore_includes_tests_excluded_by_target_repo(tmp_path):
    build_context = tmp_path / "context"
    (build_context / "tests").mkdir(parents=True)
    (build_context / "tests" / "test_demo.py").write_text("def test_demo(): pass\n", encoding="utf-8")
    dockerignore_path = build_context / ".dockerignore"
    dockerignore_path.write_text("docs/\ntests/\n.pytest_cache/\n", encoding="utf-8")

    result = ensure_eval_dockerignore_includes_test_artifacts(
        build_context,
        test_commands=[REPO2RUN_PYTEST_COLLECT_COMMAND],
        run_summary={
            "successful_actions": [
                {
                    "command": REPO2RUN_PYTEST_COLLECT_COMMAND,
                    "observation_summary": (
                        "tests/test_demo.py::test_demo\n1 test collected in 0.01s\n"
                    ),
                }
            ],
        },
    )

    updated = dockerignore_path.read_text(encoding="utf-8")
    assert result["changed"] is True
    assert result["test_artifact_paths"] == ["tests"]
    assert result["removed_patterns"] == ["tests/"]
    assert "docs/" in updated
    assert "tests/" not in [line.strip() for line in updated.splitlines()]
    assert "!tests/" in updated
    assert "!tests/**" in updated


def test_ensure_eval_dockerignore_keeps_non_test_exclusions(tmp_path):
    build_context = tmp_path / "context"
    (build_context / "src").mkdir(parents=True)
    (build_context / "src" / "app.py").write_text("", encoding="utf-8")
    dockerignore_path = build_context / ".dockerignore"
    dockerignore_path.write_text("docs/\nci/\n", encoding="utf-8")

    result = ensure_eval_dockerignore_includes_test_artifacts(
        build_context,
        test_commands=[REPO2RUN_PYTEST_COLLECT_COMMAND],
        run_summary=None,
    )

    assert result["changed"] is False
    assert result["reason"] == "no_existing_test_artifacts_detected"
    assert dockerignore_path.read_text(encoding="utf-8") == "docs/\nci/\n"


def test_select_repo2run_collect_command_from_run_summary_ignores_unobserved_reported_collect_command():
    command, source = select_repo2run_collect_command_from_run_summary(
        {
            "verification_bundle": {
                "test_commands": [REPO2RUN_PYTEST_COLLECT_COMMAND],
            },
            "successful_actions": [
                {
                    "command": (
                        "pytest --collect-only -q --disable-warnings "
                        "--ignore=tests/test_robomimic_image_runner.py "
                        "--ignore=tests/test_robomimic_lowdim_runner.py"
                    ),
                    "observation_summary": "22 tests collected in 1.39s\n",
                }
            ],
        }
    )

    assert command == (
        "pytest --collect-only -q --disable-warnings "
        "--ignore=tests/test_robomimic_image_runner.py "
        "--ignore=tests/test_robomimic_lowdim_runner.py"
    )
    assert source == "repo2run_pytest_collect_only_agent_verified"


def test_load_selected_base_image_from_workplace_reads_image_selector_summary(tmp_path):
    workplace = tmp_path / "case"
    summary_path = workplace / "logs" / "image_selector_logs" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps({"selected_image": "python:3.10"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_selected_base_image_from_workplace(workplace) == "python:3.10"


def test_load_platform_override_from_workplace_reads_image_selector_summary(tmp_path):
    workplace = tmp_path / "case"
    summary_path = workplace / "logs" / "image_selector_logs" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps({"platform_override": "linux/amd64"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_platform_override_from_workplace(workplace) == "linux/amd64"


def test_load_platform_override_from_workplace_recovers_from_arch_note_log(tmp_path):
    workplace = tmp_path / "case"
    log_dir = workplace / "logs" / "image_selector_logs"
    log_dir.mkdir(parents=True)
    (log_dir / "16.md").write_text(
        "<arch_note>This repo needs linux/amd64 for ARM64 compatibility.</arch_note>\n",
        encoding="utf-8",
    )

    assert load_platform_override_from_workplace(workplace) == "linux/amd64"


def test_resolve_benchmark_platform_prefers_run_summary(tmp_path):
    workplace = tmp_path / "case"
    assert resolve_benchmark_platform(
        workplace,
        {"platform_override": "linux/amd64"},
    ) == "linux/amd64"


def test_infer_base_image_from_dockerfile_text_reads_from_line():
    dockerfile_text = "FROM python:3.11\nWORKDIR /app\nRUN pip install -e .\n"

    assert infer_base_image_from_dockerfile_text(dockerfile_text) == "python:3.11"


def test_build_minimal_recipe_run_summary_drops_large_unused_fields():
    summary = build_minimal_recipe_run_summary(
        {
            "repo_url": "https://github.com/example/repo.git",
            "verification_bundle": {"test_commands": ["pytest tests"]},
            "verified_runtime_preparation_commands": ["export APP_ENV=test"],
            "verified_test_commands": ["pytest tests"],
            "verified_test_command": "pytest tests",
            "successful_actions": [
                {"command": "export APP_ENV=test", "observation_summary": ""},
                {"command": "pytest tests", "observation_summary": "collected 2 items\n2 passed\n"},
            ],
            "failed_actions": [{"command": "pytest tests"}],
            "required_local_services": ["redis"],
            "steps": [{"step_id": 1}],
            "build_recipe": {"build_commands": ["pip install pytest"]},
            "resynthesis": {"reused_existing_workplace": True},
        }
    )

    assert summary == {
        "repo_url": "https://github.com/example/repo.git",
        "verification_bundle": {
            "runtime_preparation_commands": ["export APP_ENV=test"],
            "test_commands": ["pytest tests"],
        },
        "verified_runtime_preparation_commands": ["export APP_ENV=test"],
        "verified_test_commands": ["pytest tests"],
        "verified_test_command": "pytest tests",
        "successful_actions": [
            {"command": "export APP_ENV=test", "observation_summary": ""},
            {"command": "pytest tests", "observation_summary": "collected 2 items\n2 passed\n"},
        ],
        "failed_actions": [{"command": "pytest tests"}],
        "required_local_services": ["redis"],
    }


def test_write_instance_debug_artifacts_emits_markdown_and_terminal_logs(tmp_path):
    artifact_dir = tmp_path / "artifacts"
    payload = {
        "execution_status": "environment_built",
        "dockerfile_generation_success": True,
        "environment_build_success": True,
        "paper_build_success": True,
        "paper_alignment": "matched_success",
        "docker_platform": "linux/amd64",
        "verification_command_source": "verification_bundle",
        "run_summary_path": str(tmp_path / "agent_run_summary.json"),
        "agent_dockerfile_path": str(tmp_path / "Dockerfile"),
        "eval_dockerfile_path": str(tmp_path / "Dockerfile.eval"),
        "result_json_path": str(tmp_path / "result.json"),
        "runtime_preparation_commands": ["export APP_ENV=test"],
        "test_commands": ["python -m pytest tests -q"],
        "agent_run": {
            "command_shell": "python agent.py repo",
            "returncode": 0,
            "timed_out": False,
            "duration_seconds": 1.2,
            "started_at": "2026-01-01T00:00:00+08:00",
            "finished_at": "2026-01-01T00:00:01+08:00",
            "cwd": "/tmp/work",
            "stdout": "agent stdout",
            "stderr": "agent stderr",
        },
        "docker_build": {
            "command_shell": "docker build -t demo .",
            "returncode": 0,
            "timed_out": False,
            "duration_seconds": 10.0,
            "started_at": "2026-01-01T00:00:02+08:00",
            "finished_at": "2026-01-01T00:00:12+08:00",
            "cwd": "/tmp/work",
            "stdout": "build stdout",
            "stderr": "build stderr",
        },
        "test_execution": {
            "workdir": "/app",
            "effective_test_command_count": 1,
            "all_test_commands_effective": True,
            "results": [
                {
                    "test_command": "python -m pytest tests -q",
                    "script": "cd /app\npython -m pytest tests -q\n",
                    "execution": {
                        "command_shell": "docker run demo",
                        "returncode": 1,
                        "timed_out": False,
                        "duration_seconds": 2.5,
                        "started_at": "2026-01-01T00:00:13+08:00",
                        "finished_at": "2026-01-01T00:00:15+08:00",
                        "cwd": "/tmp/work",
                        "stdout": "collected 10 items\n9 passed, 1 failed\n",
                        "stderr": "",
                    },
                    "classification": {
                        "effective": True,
                        "reason": "tests_executed_with_failures",
                    },
                }
            ],
        },
        "docker_cleanup": {
            "command_shell": "docker image rm -f demo",
            "returncode": 0,
            "timed_out": False,
            "duration_seconds": 0.1,
            "started_at": "2026-01-01T00:00:16+08:00",
            "finished_at": "2026-01-01T00:00:16+08:00",
            "cwd": "/tmp/work",
            "stdout": "deleted",
            "stderr": "",
        },
    }

    debug_artifacts = write_instance_debug_artifacts(
        artifact_dir=artifact_dir,
        instance={
            "instance_id": "case-1",
            "full_name": "owner/repo",
            "sha": "abc123",
            "repo_url": "https://github.com/example/repo.git",
        },
        payload=payload,
    )

    benchmark_log = Path(debug_artifacts["benchmark_log_path"]).read_text(encoding="utf-8")
    assert "Repo2Run Benchmark Run Log" in benchmark_log
    assert "Execution Status: `environment_built`" in benchmark_log
    assert "Docker Platform: `linux/amd64`" in benchmark_log
    assert "tests_executed_with_failures" in benchmark_log
    assert "python -m pytest tests -q" in benchmark_log

    assert Path(debug_artifacts["agent_run"]["stdout_log"]).read_text(encoding="utf-8") == "agent stdout"
    assert Path(debug_artifacts["docker_build"]["stderr_log"]).read_text(encoding="utf-8") == "build stderr"
    assert Path(debug_artifacts["test_execution"][0]["stdout_log"]).read_text(encoding="utf-8").startswith(
        "collected 10 items"
    )


def test_evaluate_built_image_forwards_platform_override(monkeypatch, tmp_path):
    captured = {}

    def fake_run_command(command, cwd, env=None, input_text=None, timeout_seconds=None):
        captured["command"] = command
        return {
            "command": command,
            "command_shell": "docker run --platform linux/amd64 demo",
            "cwd": str(cwd),
            "returncode": 0,
            "started_at": "2026-01-01T00:00:00+08:00",
            "finished_at": "2026-01-01T00:00:01+08:00",
            "duration_seconds": 1.0,
            "stdout": "3 tests collected in 0.10s\n",
            "stderr": "",
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
        }

    monkeypatch.setattr("run_repo2run_benchmark.run_command", fake_run_command)

    result = evaluate_built_image(
        image_tag="demo",
        workdir="/app",
        runtime_commands=[],
        test_commands=[REPO2RUN_PYTEST_COLLECT_COMMAND],
        cwd=tmp_path,
        timeout_seconds=30,
        workspace_root=tmp_path,
        docker_platform="linux/amd64",
    )

    assert captured["command"][:6] == [
        "docker",
        "run",
        "--rm",
        "-i",
        "--platform",
        "linux/amd64",
    ]
    assert result["all_test_commands_effective"] is True


def test_should_add_postgres_host_alias_for_verified_pg_runtime_command(tmp_path):
    assert should_add_postgres_host_alias(
        tmp_path,
        ["pg_ctlcluster 17 main start"],
        [REPO2RUN_POETRY_COLLECT_COMMAND],
    )


def test_evaluate_built_image_adds_postgres_host_alias(monkeypatch, tmp_path):
    captured = {}

    def fake_run_command(command, cwd, env=None, input_text=None, timeout_seconds=None):
        captured["command"] = command
        return {
            "command": command,
            "command_shell": "docker run demo",
            "cwd": str(cwd),
            "returncode": 0,
            "started_at": "2026-01-01T00:00:00+08:00",
            "finished_at": "2026-01-01T00:00:01+08:00",
            "duration_seconds": 1.0,
            "stdout": "3 tests collected in 0.10s\n",
            "stderr": "",
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
        }

    monkeypatch.setattr("run_repo2run_benchmark.run_command", fake_run_command)

    evaluate_built_image(
        image_tag="demo",
        workdir="/app",
        runtime_commands=["pg_ctlcluster 17 main start"],
        test_commands=[REPO2RUN_POETRY_COLLECT_COMMAND],
        cwd=tmp_path,
        timeout_seconds=30,
        workspace_root=tmp_path,
    )

    assert "--add-host" in captured["command"]
    assert "postgres:127.0.0.1" in captured["command"]
    assert captured["command"].index("postgres:127.0.0.1") < captured["command"].index("demo")


def test_build_agent_command_forwards_agent_command_timeout(tmp_path):
    repo_root = tmp_path / "repo"
    workplace = tmp_path / "workplace"
    args = SimpleNamespace(
        base_image="auto",
        model="demo-model",
        max_steps=300,
        agent_command_timeout=2400,
        enable_observation_compression=True,
        enable_long_term_memory=False,
        memory_embedding_model="text-embedding-3-large",
        memory_path=None,
        keep_container=False,
    )
    instance = {
        "repo_url": "https://github.com/example/repo.git",
        "base_commit": "abc123",
    }

    command = build_agent_command(
        python_executable="/usr/bin/python3",
        repo_root=repo_root,
        instance=instance,
        workplace=workplace,
        args=args,
    )

    assert command[:3] == [
        "/usr/bin/python3",
        str(repo_root / "agent.py"),
        "https://github.com/example/repo.git",
    ]
    assert "--command-timeout" in command
    assert command[command.index("--command-timeout") + 1] == "2400"
    assert "--enable-observation-compression" in command


class FakeReplaySynth:
    def __init__(self, base_image="python:3.10", workdir="/app"):
        self.base_image = base_image
        self.workdir = workdir
        self.recipe_input = None
        self.summary_input = None

    def summarize_setup_log_for_recipe(self, client, model, setup_log_trajectory_text, log_dir=None):
        self.summary_input = setup_log_trajectory_text
        if log_dir:
            Path(log_dir, "setup_log_summary.md").write_text(
                "fake setup log summary",
                encoding="utf-8",
            )
        return SimpleNamespace(
            summary_text=(
                "Step 1-2\n"
                "Type: failed_attempts\n"
                "Goal: run pytest before it is installed\n"
                "Attempts:\n"
                "- Step 1: python -m pytest tests -> pytest not found\n"
                "- Step 2: pip show pytest -> package missing\n"
                "Outcome: pytest must be installed\n\n"
                "Step 3\n"
                "Type: successful_state_change\n"
                "Thought: install pytest\n"
                "Action: pip install pytest\n"
                "Observation: Successfully installed pytest"
            ),
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            error=None,
        )

    def synthesize_build_recipe(self, client, model, recipe_input, log_dir=None):
        self.recipe_input = recipe_input
        if log_dir:
            Path(log_dir, "recipe_synthesis.md").write_text(
                "fake recipe synthesis",
                encoding="utf-8",
            )
        return SimpleNamespace(
            recipe={
                "build_commands": ["pip install pytest"],
                "post_test_patch_commands": [],
                "runtime_preparation_commands": [],
                "test_commands": ["python -m pytest tests -v"],
                "excluded_commands": [],
                "rationale": "pytest must be installed",
                "confidence": "high",
            },
            usage={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            error=None,
            source="llm",
        )

    def generate_dockerfile(self, file_path="Dockerfile"):
        dockerfile = (
            f"FROM {self.base_image}\n"
            f"WORKDIR {self.workdir}\n"
            "RUN pip install pytest\n"
        )
        Path(file_path).write_text(dockerfile, encoding="utf-8")
        return dockerfile


def test_resynthesize_dockerfile_from_existing_workplace_reuses_prior_run(tmp_path):
    workplace = tmp_path / "case"
    setup_log_dir = workplace / "logs" / "setup_logs"
    image_selector_log_dir = workplace / "logs" / "image_selector_logs"
    setup_log_dir.mkdir(parents=True)
    image_selector_log_dir.mkdir(parents=True)

    (image_selector_log_dir / "summary.json").write_text(
        json.dumps({"selected_image": "python:3.10"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (setup_log_dir / "1.md").write_text(
        "\n".join(
            [
                "##### LLM INPUT (setup call #1) #####",
                "================================ Human Message =================================",
                "",
                "[SYSTEM]",
                "planner prompt",
                "",
                "Repository Structure:",
                "repo/",
                "",
                "[ASSISTANT]",
                "Thought: Install pytest.",
                "Action: pip install pytest",
                "",
                "Observation: Successfully installed pytest",
                "",
                "================================ Raw AI Message =================================",
            ]
        ),
        encoding="utf-8",
    )
    (workplace / "agent_run_summary.json").write_text(
        json.dumps(
            {
                "repo_url": "https://github.com/example/repo.git",
                "verified_runtime_preparation_commands": [],
                "verified_test_commands": ["python -m pytest tests -v"],
                "verification_bundle": {
                    "runtime_preparation_commands": [],
                    "test_commands": ["python -m pytest tests -v"],
                },
                "required_local_services": ["redis"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    fake_synth = FakeReplaySynth()
    result = resynthesize_dockerfile_from_existing_workplace(
        workplace=workplace,
        model="fake-model",
        client=object(),
        synthesizer=fake_synth,
    )

    updated_summary = json.loads((workplace / "agent_run_summary.json").read_text(encoding="utf-8"))
    dockerfile_text = (workplace / "Dockerfile").read_text(encoding="utf-8")

    assert result["dockerfile_generated"] is True
    assert result["build_recipe_source"] == "llm"
    assert fake_synth.summary_input.startswith("Step 1")
    assert fake_synth.recipe_input["setup_log_summary_text"].startswith("Step 1")
    assert fake_synth.recipe_input["task"]["base_image"] == "python:3.10"
    assert "steps" not in fake_synth.recipe_input["agent_run_summary"]
    assert "RUN pip install pytest" in dockerfile_text
    assert updated_summary["build_recipe_source"] == "llm"
    assert updated_summary["resynthesis"]["reused_existing_workplace"] is True
