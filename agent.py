import os
import re
import copy
import json
import argparse
import subprocess
import shutil
import time
from pathlib import Path
from openai import OpenAI
from src.sandbox import Sandbox
from src.planner import Planner
from src.synthesizer import Synthesizer
from src.image_selector import ImageSelector
from src.verification_bundle import derive_supported_verification_bundle
from src.artifact_verify import verify_and_repair_recipe
from src.constants import DEFAULT_LLM_MODEL, DEFAULT_MEMORY_EMBEDDING_MODEL
from src.memory_manager import LongTermMemoryManager
from src.observation_compressor import (
    AgentStep,
    ObservationCompressor,
    RunTokenLedger,
    build_observation_metadata,
    safety_compress_observation,
    should_apply_compression,
)
from dotenv import load_dotenv

# Load environment variables (OPENAI_API_KEY, etc.)
# override=True ensures .env values take precedence over system env vars
load_dotenv(override=True)

LOCAL_SERVICE_CONFIG_EXTENSIONS = {
    ".properties",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".env",
}

LOCAL_SERVICE_EXCLUDED_FILENAMES = {
    "pom.xml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.mod",
    "cargo.toml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "composer.json",
    "composer.lock",
    "gemfile",
    "gemfile.lock",
}

LOCAL_SERVICE_MARKER_PATTERNS = {
    "postgresql": (
        r"jdbc:postgresql://",
        r"\bpostgresql://",
        r"spring\.datasource\.url\s*[=:]\s*jdbc:postgresql://",
        r"\blocalhost:543[23]\b",
    ),
    "mysql": (
        r"jdbc:mysql://",
        r"\bmysql://",
        r"\bmariadb://",
        r"spring\.datasource\.url\s*[=:]\s*jdbc:(?:mysql|mariadb)://",
        r"\blocalhost:3306\b",
    ),
    "redis": (
        r"\bredis://",
        r"spring\.data\.redis",
        r"\bredis\.host\b",
        r"\blocalhost:6379\b",
    ),
    "rabbitmq": (
        r"\bamqp://",
        r"spring\.rabbitmq",
        r"\brabbitmq\b",
        r"\blocalhost:5672\b",
    ),
    "minio": (
        r"\bminio\b",
        r"\bs3\.endpoint\b",
        r"\blocalhost:(?:9000|10000)\b",
    ),
    "elasticsearch": (
        r"\belasticsearch\b",
        r"\bopensearch\b",
        r"spring\.elasticsearch",
        r"\blocalhost:9200\b",
    ),
    "kafka": (
        r"\bkafka\b",
        r"\bbootstrap\.servers\b",
        r"\blocalhost:9092\b",
    ),
}

SETUP_LOG_HUMAN_MESSAGE_HEADER = "================================ Human Message ================================="
SETUP_LOG_RAW_AI_MESSAGE_HEADER = "================================ Raw AI Message ================================="
SETUP_LOG_SUMMARY_THOUGHT_MAX_CHARS = 1200
SETUP_LOG_SUMMARY_OBSERVATION_MAX_CHARS = 1600
MAX_INVALID_FINAL_BUNDLE_REPORTS = 3

class DockerAgent:
    def __init__(
        self,
        repo_url,
        base_image="auto",
        model=DEFAULT_LLM_MODEL,
        workplace="workplace",
        base_commit=None,
        problem_statement="",
        test_patch="",
        benchmark_evaluation_target=None,
        language="",
        enable_observation_compression=False,
        enable_long_term_memory=False,
        enable_envstate=False,
        enable_supervisor=False,
        enable_fullstate_worker=False,
        fullstate_worker_prompt=False,
        enable_v1=False,
        enable_cleanroom=False,
        memory_path=None,
        memory_embedding_model=DEFAULT_MEMORY_EMBEDDING_MODEL,
        command_timeout_seconds=1800,
        enable_post_synthesis_repair=True,
        self_verify_max_rounds=2,
    ):
        self.repo_url = repo_url
        self.model = model
        self.enable_post_synthesis_repair = enable_post_synthesis_repair
        self.self_verify_max_rounds = self_verify_max_rounds
        self.self_verify_result = None
        self.base_commit = base_commit
        self.problem_statement = problem_statement or ""
        self.test_patch = test_patch or ""
        self.benchmark_evaluation_target = benchmark_evaluation_target or {}
        self.language = language or ""
        self.workplace = os.path.abspath(workplace)
        self.successful_test_commands = []
        self.verified_test_command = None
        self.verified_test_commands = []
        self.verified_runtime_preparation_commands = []
        self.test_run_attempts = []
        self.successful_actions = []
        self.failed_actions = []
        self.verification_source = None
        self.verification_bundle = None
        self.build_recipe = None
        self.build_recipe_source = None
        self.build_recipe_error = None
        self.run_summary_path = os.path.join(self.workplace, "agent_run_summary.json")
        self._environment_revision = 0
        self._current_verification_group = []
        self.enable_observation_compression = enable_observation_compression
        self.enable_long_term_memory = enable_long_term_memory
        self.enable_supervisor = enable_supervisor
        self.enable_fullstate_worker = enable_fullstate_worker
        self.fullstate_worker_prompt = fullstate_worker_prompt
        self.enable_v1 = enable_v1
        self.enable_envstate = (
            enable_envstate or enable_supervisor or enable_fullstate_worker or enable_v1
        )
        self.enable_cleanroom = enable_cleanroom
        self.action_ledger = None
        self.current_task_id = None
        self.env_container_id = ""
        if self.enable_envstate:
            from src.envstate.ledger import ActionLedger
            self.action_ledger = ActionLedger()
        self.memory_path = memory_path
        self.memory_embedding_model = memory_embedding_model
        self.command_timeout_seconds = command_timeout_seconds
        self.memory_manager = None
        self.last_failed_memory_context = None
        self.memory_stats = {
            "enabled": enable_long_term_memory,
            "retrievals": 0,
            "retrieval_hits": 0,
            "generation_attempted": False,
            "candidate_memories": 0,
            "written_memories": 0,
            "skipped_duplicates": 0,
            "skipped_invalid": 0,
            "relation_judged_pairs": 0,
            "relation_links_accepted": 0,
            "relation_links_rejected": 0,
            "relation_duplicates_rejected": 0,
            "errors": [],
        }
        self.safety_compression_threshold_chars = 200_000
        self.safety_compression_target_chars = 20_000
        self.compression_delay = 2
        self.compression_context_before = 1
        self.compression_threshold_chars = 1500
        self.compression_benefit_tokens = 300
        self.agent_steps = []
        self.run_token_ledger = RunTokenLedger()
        self.compression_stats = {
            "candidate_steps": 0,
            "compressed_steps": 0,
            "saved_tokens_est": 0,
        }
        
        # 1. Prepare local workplace and clone repo
        self._prepare_workplace()
        
        # 2. If base_commit is specified, checkout before image selection
        # so that LLM analyzes the actual files at base_commit, not the latest HEAD
        if base_commit:
            self._checkout_commit(base_commit)
            print(f"Checked out commit: {base_commit}")

        self.required_local_services = self._collect_local_service_hints()
        if self.required_local_services:
            print(
                "[DockerAgent] Detected local service dependencies: "
                + ", ".join(sorted(self.required_local_services))
            )
        
        # 3. Initialize LLM client first (needed for image selection)
        #    Provider precedence: OpenRouter -> MiniMax -> OpenAI (all OpenAI-compatible).
        api_key = (os.getenv("OPENROUTER_API_KEY")
                   or os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY"))
        base_url = (os.getenv("OPENROUTER_API_BASE")
                    or os.getenv("MINIMAX_API_BASE") or os.getenv("OPENAI_API_BASE"))
        if not api_key:
            raise ValueError("No LLM API key found. Set OPENROUTER_API_KEY, MINIMAX_API_KEY, "
                             "or OPENAI_API_KEY in environment variables (.env).")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None
        )

        # OpenRouter provider pinning. When talking to OpenRouter (detected via
        # base_url / LLM_API_PROVIDER, or when OPENROUTER_PROVIDER is set), route
        # every chat completion through the listed upstream provider(s) in order,
        # no fallbacks. OPENROUTER_PROVIDER is comma-separated (default "Alibaba",
        # e.g. for deepseek-v4-flash). Applies to every component sharing
        # self.client — image selection, the Arm-0 ReAct loop, and the v1
        # Planner/BuildAgent/Maintainer.
        _or_provider_env = os.getenv("OPENROUTER_PROVIDER")
        _is_openrouter = (
            bool(_or_provider_env)
            or (base_url and "openrouter" in base_url)
            or os.getenv("LLM_API_PROVIDER") == "openrouter"
        )
        if _is_openrouter:
            _providers = [p.strip() for p in (_or_provider_env or "Alibaba").split(",") if p.strip()]
            _orig_create = self.client.chat.completions.create

            def _routed_create(*args, _orig=_orig_create, _prov=_providers, **kwargs):
                extra_body = dict(kwargs.get("extra_body") or {})
                extra_body.setdefault(
                    "provider", {"order": _prov, "allow_fallbacks": False}
                )
                kwargs["extra_body"] = extra_body
                return _orig(*args, **kwargs)

            self.client.chat.completions.create = _routed_create

        if self.enable_long_term_memory:
            self.memory_manager = LongTermMemoryManager(
                client=self.client,
                llm_model=model,
                memory_path=self.memory_path,
                embedding_model=self.memory_embedding_model,
            )
            print(
                "[DockerAgent] Long-term memory enabled: "
                f"{self.memory_manager.memory_path} "
                f"(embedding: {self.memory_embedding_model})"
            )
        
        # 4. Auto-detect base image if set to "auto" or not specified
        platform_override = None
        self.logs_dir = os.path.join(self.workplace, "logs")
        image_selector_log_dir = os.path.join(self.logs_dir, "image_selector_logs")
        if base_image == "auto":
            print("[DockerAgent] Analyzing repository to select optimal base image...")
            selector = ImageSelector(self.client, model)
            selected_image, language_handler, docs, platform_override = selector.select_base_image(
                repo_path=self.workplace,
                platform="linux",
                log_dir=image_selector_log_dir
            )
            usage = selector.get_token_usage()
            self.run_token_ledger.add(
                "image_selector",
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
            base_image = selected_image
            self.language_handler = language_handler
            self.repo_docs = docs
            print(f"[DockerAgent] Selected base image: {base_image}")
            if platform_override:
                print(f"[DockerAgent] Platform override: {platform_override} (for ARM64 compatibility)")
            print(f"[DockerAgent] Image selection logs saved to: {image_selector_log_dir}")
        else:
            # Use specified base image with legacy detection for Python
            if base_image.startswith("python:"):
                detected = self._detect_python_image()
                if detected:
                    print(f"[Auto-detect] Using base image: {detected} (from project files)")
                    base_image = detected
            self.language_handler = None
            self.repo_docs = ""
        
        # 5. Setup Sandbox with a copied workspace so rollback restores repo state too.
        self.sandbox = self._create_sandbox(
            base_image=base_image,
            platform_override=platform_override,
        )
        self.platform_override = platform_override  # Expose for adapter to read
        if self.enable_envstate and getattr(self.sandbox, "container", None) is not None:
            self.env_container_id = self.sandbox.container.short_id

        # 6. Initialize Planner and Synthesizer
        # Load only repository structure from image_selector_logs. Configuration
        # files should be inspected explicitly by the agent when needed.
        repo_structure = ""
        
        # Load structure.txt
        structure_file = os.path.join(image_selector_log_dir, "structure.txt")
        if os.path.exists(structure_file):
            try:
                with open(structure_file, 'r') as f:
                    repo_structure = f.read()
                print(f"[DockerAgent] Loaded repository structure from: {structure_file}")
            except Exception as e:
                print(f"[DockerAgent] Warning: Could not read structure.txt: {e}")

        maven_repository_hints = self._collect_maven_repository_hints()
        
        # Setup log directory for LLM calls (similar to image_selector_logs)
        setup_log_dir = os.path.join(self.logs_dir, "setup_logs")
        os.makedirs(setup_log_dir, exist_ok=True)
        self.setup_log_dir = setup_log_dir
        compression_log_dir = os.path.join(self.logs_dir, "compression_logs")
        self.compression_log_dir = compression_log_dir
        memory_relation_log_dir = os.path.join(self.logs_dir, "memory_relation_logs")
        self.memory_relation_log_dir = memory_relation_log_dir
        if self.memory_manager:
            self.memory_manager.log_dir = Path(setup_log_dir)
            self.memory_manager.relation_log_dir = Path(memory_relation_log_dir)
        
        self.planner = Planner(
            self.client,
            model=model,
            language_handler=self.language_handler,
            repo_structure=repo_structure,
            maven_repository_hints=maven_repository_hints,
            benchmark_evaluation_target=self.benchmark_evaluation_target,
            log_dir=setup_log_dir,
            enable_long_term_memory=self.enable_long_term_memory,
        )
        self.synthesizer = Synthesizer(base_image=base_image)
        self.observation_compressor = None
        if self.enable_observation_compression:
            os.makedirs(compression_log_dir, exist_ok=True)
            self.observation_compressor = ObservationCompressor(
                self.client,
                model=model,
                log_dir=compression_log_dir,
            )
            self.planner.init_managed_history(self.repo_url)
        print(f"[DockerAgent] Setup logs will be saved to: {setup_log_dir}")
        if self.enable_observation_compression:
            print(f"[DockerAgent] Compression logs will be saved to: {compression_log_dir}")
        if self.enable_long_term_memory:
            print(f"[DockerAgent] Memory relation logs will be saved to: {memory_relation_log_dir}")

    def _detect_python_image(self):
        """
        Scan project files to determine the required Python version.
        Returns a docker image tag like 'python:3.9', or None if undetermined.
        Priority: .python-version > pyproject.toml > setup.cfg > setup.py > CI configs > tox.ini
        """
        wp = self.workplace

        def _usable(ver_str):
            """Only accept Python 3.6+; discard Python 2.x or very old 3.x"""
            try:
                parts = ver_str.split('.')
                major, minor = int(parts[0]), int(parts[1])
                return major == 3 and minor >= 6
            except Exception:
                return False

        def _parse_version_spec(spec):
            """Extract a concrete version from a specifier like '>=3.8,<3.11' or '==3.9.*'"""
            spec = spec.strip().replace(' ', '')
            # exact: ==3.9 or ==3.9.*
            m = re.search(r'==\s*(\d+\.\d+)', spec)
            if m and _usable(m.group(1)):
                return m.group(1)
            # lower-bound: >=3.x
            m = re.search(r'>=\s*(\d+\.\d+)', spec)
            if m and _usable(m.group(1)):
                return m.group(1)
            # ~=3.x
            m = re.search(r'~=\s*(\d+\.\d+)', spec)
            if m and _usable(m.group(1)):
                return m.group(1)
            return None

        # 1. .python-version (e.g. "3.9.7" or "3.9")
        pv_file = os.path.join(wp, ".python-version")
        if os.path.exists(pv_file):
            with open(pv_file) as f:
                ver = f.read().strip().split('\n')[0]
            m = re.match(r'(\d+\.\d+)', ver)
            if m and _usable(m.group(1)):
                return f"python:{m.group(1)}"

        # 2. pyproject.toml  requires-python
        pp = os.path.join(wp, "pyproject.toml")
        if os.path.exists(pp):
            with open(pp) as f:
                content = f.read()
            m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                ver = _parse_version_spec(m.group(1))
                if ver:
                    return f"python:{ver}"

        # 3. setup.cfg  python_requires
        sc = os.path.join(wp, "setup.cfg")
        if os.path.exists(sc):
            with open(sc) as f:
                content = f.read()
            m = re.search(r'python_requires\s*=\s*(.+)', content)
            if m:
                ver = _parse_version_spec(m.group(1))
                if ver:
                    return f"python:{ver}"

        # 4. setup.py  python_requires
        sp = os.path.join(wp, "setup.py")
        if os.path.exists(sp):
            with open(sp) as f:
                content = f.read()
            m = re.search(r'python_requires\s*=\s*["\']([^"\']+)["\']', content)
            if m:
                ver = _parse_version_spec(m.group(1))
                if ver:
                    return f"python:{ver}"

        # 5. GitHub Actions workflow files
        actions_dir = os.path.join(wp, ".github", "workflows")
        if os.path.isdir(actions_dir):
            for fname in os.listdir(actions_dir):
                if not fname.endswith(('.yml', '.yaml')):
                    continue
                with open(os.path.join(actions_dir, fname)) as f:
                    content = f.read()
                # python-version: "3.x" or ["3.x", ...]
                versions = re.findall(r'python-version["\s:]+["\[]*(3\.\d+)', content)
                usable = [v for v in versions if _usable(v)]
                if usable:
                    return f"python:{sorted(usable)[0]}"  # lowest usable

        # 6. .travis.yml
        travis = os.path.join(wp, ".travis.yml")
        if os.path.exists(travis):
            with open(travis) as f:
                content = f.read()
            versions = re.findall(r'["\s-]+(3\.\d+)["\s]', content)
            usable = [v for v in versions if _usable(v)]
            if usable:
                return f"python:{sorted(usable)[0]}"

        # 7. tox.ini  envlist (pyXY style, Python 3.6+)
        tox = os.path.join(wp, "tox.ini")
        if os.path.exists(tox):
            with open(tox) as f:
                content = f.read()
            versions = [(int(a), int(b)) for a, b in re.findall(r'py(\d)(\d+)', content)
                        if int(a) == 3 and int(b) >= 6]
            if versions:
                major, minor = sorted(versions)[0]
                return f"python:{major}.{minor}"

        return None

    def _collect_maven_repository_hints(self):
        """Extract custom Maven repository ids declared by the cloned project."""
        hint_lines = []
        seen_ids = set()

        for root, dirs, files in os.walk(self.workplace):
            rel_root = os.path.relpath(root, self.workplace)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            if depth > 2:
                dirs[:] = []
                continue

            if "pom.xml" not in files:
                continue

            pom_path = os.path.join(root, "pom.xml")
            try:
                with open(pom_path, "r", encoding="utf-8") as file_obj:
                    content = file_obj.read()
            except OSError:
                continue

            repositories_sections = re.findall(
                r"<repositories>(.*?)</repositories>",
                content,
                flags=re.DOTALL | re.IGNORECASE,
            )
            for section in repositories_sections:
                repository_blocks = re.findall(
                    r"<repository>(.*?)</repository>",
                    section,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                for block in repository_blocks:
                    repo_id_match = re.search(
                        r"<id>\s*([^<]+?)\s*</id>",
                        block,
                        flags=re.IGNORECASE,
                    )
                    if not repo_id_match:
                        continue

                    repo_id = repo_id_match.group(1).strip()
                    repo_key = repo_id.lower()
                    if repo_key in seen_ids:
                        continue

                    seen_ids.add(repo_key)
                    repo_url_match = re.search(
                        r"<url>\s*([^<]+?)\s*</url>",
                        block,
                        flags=re.IGNORECASE,
                    )
                    repo_url = repo_url_match.group(1).strip() if repo_url_match else ""
                    rel_path = os.path.relpath(pom_path, self.workplace)
                    if repo_url:
                        hint_lines.append(f"- {repo_id}: {repo_url} (declared in {rel_path})")
                    else:
                        hint_lines.append(f"- {repo_id} (declared in {rel_path})")

        if not hint_lines:
            return ""

        return "\n".join(hint_lines[:12])

    def _collect_local_service_hints(self):
        """Detect explicit local service dependencies from config-like files in the cloned repo."""
        workplace = getattr(self, "workplace", None)
        if not workplace or not os.path.isdir(workplace):
            return set()

        detected_services = set()
        scanned_files = 0
        max_files = 200
        max_file_bytes = 200_000

        for root, dirs, files in os.walk(workplace):
            rel_root = os.path.relpath(root, workplace)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            if depth > 5:
                dirs[:] = []
                continue

            dirs[:] = [
                directory
                for directory in dirs
                if directory not in {".git", "node_modules", ".venv", "venv", "vendor", "dist", "build"}
            ]

            for filename in files:
                if scanned_files >= max_files:
                    return detected_services

                if not self._looks_like_local_service_config_file(root, filename):
                    continue

                file_path = os.path.join(root, filename)
                try:
                    if os.path.getsize(file_path) > max_file_bytes:
                        continue
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                        content = file_obj.read().lower()
                except OSError:
                    continue

                scanned_files += 1
                for service_name, patterns in LOCAL_SERVICE_MARKER_PATTERNS.items():
                    if service_name in detected_services:
                        continue
                    if any(re.search(pattern, content) for pattern in patterns):
                        detected_services.add(service_name)

        return detected_services

    def _looks_like_local_service_config_file(self, root, filename):
        lowered_name = filename.lower()
        if lowered_name in LOCAL_SERVICE_EXCLUDED_FILENAMES:
            return False

        _, extension = os.path.splitext(lowered_name)
        if extension not in LOCAL_SERVICE_CONFIG_EXTENSIONS and lowered_name not in {
            ".env",
            ".env.example",
        }:
            return False

        normalized_root = root.replace(os.sep, "/").lower()
        if any(
            marker in normalized_root
            for marker in (
                "/src/test/",
                "/src/test/resources",
                "/src/main/resources",
                "/config",
                "/configs",
            )
        ):
            return True

        return True

    def _prepare_workplace(self):
        """Clones the repository to the local workplace directory."""
        if os.path.exists(self.workplace):
            print(f"Cleaning up existing workplace at {self.workplace}...")
        self._reset_workplace_directory()
        print(f"Cloning {self.repo_url} into {self.workplace}...")
        last_error = None
        last_detail = ""
        for attempt in range(1, 4):
            try:
                self._run_git_clone_with_optional_lfs_fallback()
                print("Clone successful.")
                return
            except subprocess.CalledProcessError as e:
                last_error = e
                detail = self._format_clone_failure_detail(e)
                last_detail = detail
                print(f"Clone failed on attempt {attempt}/3: {detail}")
                if attempt < 3:
                    self._reset_workplace_directory()
                    time.sleep(attempt * 2)

        raise RuntimeError(
            "git clone failed after 3 attempts: "
            f"{last_detail or ((last_error.stderr or b'').decode(errors='replace') if last_error else '')}"
        ) from last_error

    def _reset_workplace_directory(self):
        if os.path.exists(self.workplace):
            shutil.rmtree(self.workplace)
        os.makedirs(self.workplace, exist_ok=True)

    def _run_git_clone_with_optional_lfs_fallback(self):
        last_error = None
        for index, strategy in enumerate(self._git_clone_strategies(), start=1):
            if index > 1:
                self._reset_workplace_directory()
            label = strategy["label"]
            if strategy.get("lfs_disabled"):
                print(f"[Clone Strategy] {label}; LFS filters disabled.")
            else:
                print(f"[Clone Strategy] {label}.")
            try:
                subprocess.run(
                    strategy["command"],
                    cwd=self.workplace,
                    check=True,
                    capture_output=True,
                )
                self._ensure_base_commit_available()
                if strategy.get("lfs_disabled"):
                    print(
                        "Clone successful with Git LFS filters disabled; "
                        "LFS-managed files may remain as pointer files."
                    )
                return
            except subprocess.CalledProcessError as error:
                last_error = error
                detail = self._format_clone_failure_detail(error)
                print(f"[Clone Strategy Failed] {label}: {detail}")

        if last_error is not None:
            raise last_error
        raise RuntimeError("git clone failed: no clone strategies were attempted")

    def _git_clone_strategies(self):
        base_git = [
            "git",
            "-c",
            "http.lowSpeedLimit=1000",
            "-c",
            "http.lowSpeedTime=60",
            "-c",
            "http.postBuffer=524288000",
        ]
        lfs_disabled_config = [
            "-c",
            "filter.lfs.process=",
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.required=false",
        ]
        clone_variants = [
            ("plain clone", ["clone", self.repo_url, "."]),
            (
                "partial clone without blobs",
                ["clone", "--filter=blob:none", self.repo_url, "."],
            ),
            (
                "shallow partial clone",
                ["clone", "--depth", "1", "--filter=blob:none", self.repo_url, "."],
            ),
        ]

        strategies = []
        for label, clone_args in clone_variants:
            strategies.append(
                {
                    "label": label,
                    "command": [*base_git, *clone_args],
                    "lfs_disabled": False,
                }
            )
            strategies.append(
                {
                    "label": f"{label} with LFS filters disabled",
                    "command": [*base_git, *lfs_disabled_config, *clone_args],
                    "lfs_disabled": True,
                }
            )
        return strategies

    def _ensure_base_commit_available(self):
        commit = getattr(self, "base_commit", None)
        if not commit:
            return

        if self._git_commit_exists(commit):
            return

        print(f"[Clone Strategy] Fetching target commit {commit}.")
        fetch_strategies = [
            [
                "git",
                "-c",
                "http.lowSpeedLimit=1000",
                "-c",
                "http.lowSpeedTime=60",
                "fetch",
                "--depth",
                "1",
                "origin",
                commit,
            ],
            [
                "git",
                "-c",
                "http.lowSpeedLimit=1000",
                "-c",
                "http.lowSpeedTime=60",
                "fetch",
                "--filter=blob:none",
                "origin",
                commit,
            ],
        ]
        last_error = None
        for command in fetch_strategies:
            try:
                subprocess.run(
                    command,
                    cwd=self.workplace,
                    check=True,
                    capture_output=True,
                )
                if self._git_commit_exists(commit):
                    return
            except subprocess.CalledProcessError as error:
                last_error = error

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"target commit is unavailable after clone: {commit}")

    def _git_commit_exists(self, commit):
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=self.workplace,
            capture_output=True,
        )
        return result.returncode == 0

    @staticmethod
    def _format_clone_failure_detail(error: subprocess.CalledProcessError) -> str:
        stderr = (error.stderr or b"").decode(errors="replace").strip()
        stdout = (error.stdout or b"").decode(errors="replace").strip()
        if stdout and stderr:
            return f"{stdout}\n{stderr}"
        return stderr or stdout or str(error)

    @staticmethod
    def _clone_failed_due_to_missing_git_lfs(detail: str) -> bool:
        normalized = detail.lower()
        return "git-lfs" in normalized and "command not found" in normalized

    def _checkout_commit(self, commit: str):
        """Checkout a specific git commit in the workplace directory."""
        try:
            subprocess.run(
                ["git", "checkout", commit],
                cwd=self.workplace,
                check=True,
                capture_output=True
            )
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to checkout commit {commit}: {e.stderr.decode()}")


    def _run_v1(self, max_cycles=12, keep_container=False):
        """Arm v1: three-role (Planner / BuildAgent / Maintainer) loop (spec §4).

        Structure mirrors _run_supervisor / _run_fullstate_worker:
          1. Set up LLM-exchange log scope.
          2. Build initial WorldModelMap.
          3. Instantiate Planner, BuildAgent, Maintainer with canonical signatures.
          4. Call run_v1() from src.envstate.orchestrator.
          5. Scan ActionLedger for the collect-only command; populate
             verified_test_commands using COLLECT_ONLY_CMD as fallback.
          6. On done_flag: call _auto_finalize_from_verified_tests then finalize.

        CLEANROOM NOTE: _verify_cleanroom_or_fail is NOT called directly from this
        method. The v1 path skips cleanroom (enable_cleanroom defaults to False).
        EBSR is the trusted success metric. When _verify_cleanroom_or_fail is
        rewritten to the decoupled signature
            _verify_cleanroom_or_fail(self, dockerfile_path, build_context) -> bool
        (with NO reference to self.env_snapshot / snapshot.requirements / req.source),
        cleanroom can be opt-in enabled for v1 runs via enable_cleanroom=True.
        Until then, _finalize_supervisor_artifacts respects enable_cleanroom=False
        and skips the gate automatically.
        """
        import re as _re
        from src.envstate.orchestrator import run_v1 as _run_v1_loop, COLLECT_ONLY_CMD
        from src.envstate.planner import Planner as _Planner
        from src.envstate.build_agent import BuildAgent as _BuildAgent
        from src.envstate.maintainer import Maintainer as _Maintainer
        from src.envstate.world_model import initial_map, Fact, map_to_dict, apply_deterministic
        from src.envstate.manifest import parse_manifests
        from src.envstate.snapshot import probe_env

        # ── 1. LLM log scope (same pattern as _run_supervisor) ───────────────
        _llm_log_dir = os.path.join(self.logs_dir, "setup_logs")
        os.makedirs(_llm_log_dir, exist_ok=True)
        _llm_log_path = os.path.join(_llm_log_dir, "envstate_llm.jsonl")
        _prev_llm_log = os.environ.get("ENVSTATE_LLM_LOG")
        os.environ["ENVSTATE_LLM_LOG"] = _llm_log_path

        # ── 2. Build initial WorldModelMap ────────────────────────────────────
        _base_image = (
            getattr(self, "base_image", None)
            or getattr(self.synthesizer, "base_image", None)
            or ""
        )
        _workdir = getattr(self.synthesizer, "workdir", "/app") or "/app"
        _repo_structure = ""
        _structure_file = os.path.join(
            self.logs_dir, "image_selector_logs", "structure.txt"
        )
        if os.path.exists(_structure_file):
            try:
                with open(_structure_file) as _f:
                    _repo_structure = _f.read()
            except Exception:
                pass

        # Derive repo_layout tuple from the first non-empty lines of structure.txt.
        _repo_layout: tuple = tuple(
            ln.strip() for ln in _repo_structure.splitlines()[:20] if ln.strip()
        )

        # Derive language/build_system from synthesizer attrs or fall back.
        _language = (
            getattr(self.synthesizer, "language", "")
            or getattr(self, "language", "")
            or "unknown"
        )
        _build_system = getattr(self.synthesizer, "build_system", "") or "unknown"

        world_map = initial_map(
            base_image=_base_image,
            workdir=_workdir,
            language=_language,
            build_system=_build_system,
            repo_layout=_repo_layout,
        )

        # ── 3. Instantiate collaborators with canonical signatures ─────────────
        # All three roles share the same constructor shape:
        #   Role(client, model, on_usage=<callable|None>, log_path=<str|None>)
        # BuildAgent additionally receives the synthesizer (for mutation
        # classification); the sandbox executor and ActionLedger are passed to
        # the loop (_run_v1_loop) per-task, not to the constructor.
        planner = _Planner(
            self.client,
            self.model,
            on_usage=lambda usage: self._record_supervisor_path_usage("worker", usage),
            log_path=_llm_log_path,
        )
        maintainer = _Maintainer(
            self.client,
            self.model,
            on_usage=lambda usage: self._record_supervisor_path_usage("reflection", usage),
            log_path=_llm_log_path,
        )
        build_agent = _BuildAgent(
            self.client,
            self.model,
            self.synthesizer,
            container_id=getattr(self, "env_container_id", "unknown"),
            on_usage=lambda usage: self._record_supervisor_path_usage("worker", usage),
            log_path=_llm_log_path,
        )

        configuration_success = False
        run_error = None

        try:
            # ── 4. Run the v1 loop ────────────────────────────────────────────
            # Deterministic facts: manifest (host FS) + read-only env probe.
            _manifest = parse_manifests(self.workplace)
            _probe = lambda: probe_env(self.sandbox.exec_readonly)

            # Structured per-cycle component trace (NL prompts excluded): one JSONL
            # line per cycle with the planner decision, build report, and the
            # resulting state map (Maintainer output). cycle 0 = the grounded
            # initial map == the Planner's first input. Diagnostic only; never
            # raises into the loop.
            _cycle_log_path = os.path.join(_llm_log_dir, "envstate_cycles.jsonl")

            def _write_cycle_record(record):
                try:
                    with open(_cycle_log_path, "a", encoding="utf-8") as _fh:
                        _fh.write(json.dumps(record) + "\n")
                except Exception:
                    pass

            def _trunc(text, limit=400):
                text = text or ""
                return text if len(text) <= limit else text[:limit] + "…"

            def _decision_dict(decision):
                if decision is None:
                    return None
                task = getattr(decision, "task", None)
                return {
                    "action": decision.action,
                    "task": (
                        {"goal": task.goal, "done_when": task.done_when,
                         "layer": task.layer, "facts": list(task.facts)}
                        if task is not None else None
                    ),
                    "reason": getattr(decision, "reason", ""),
                }

            def _report_dict(report):
                if report is None:
                    return None
                return {
                    "task_goal": report.task_goal,
                    "status": report.status,
                    "commands": [
                        {"cmd": c.cmd, "rc": c.rc, "output": _trunc(c.output)}
                        for c in report.commands
                    ],
                    "learning": report.learning,
                }

            # cycle 0: the grounded initial map (the Planner's first input).
            try:
                _grounded0 = apply_deterministic(world_map, _probe(), _manifest)
                _write_cycle_record(
                    {"cycle": 0, "planner": None, "build_report": None,
                     "state_map": map_to_dict(_grounded0)}
                )
            except Exception:
                pass

            def _on_cycle(cycle, current_map, decision, report):
                _write_cycle_record({
                    "cycle": cycle,
                    "planner": _decision_dict(decision),
                    "build_report": _report_dict(report),
                    "state_map": map_to_dict(current_map),
                })

            final_map, stop_reason = _run_v1_loop(
                planner=planner,
                build_agent=build_agent,
                maintainer=maintainer,
                initial_world_map=world_map,
                ledger=self.action_ledger,
                sandbox_execute=self.sandbox.execute,
                max_cycles=max_cycles,
                probe=_probe,
                manifest=_manifest,
                on_cycle=_on_cycle,
            )

            print(f"[v1] Loop finished: stop_reason={stop_reason!r}")

            # ── 5. Scan ActionLedger for collect-only command ─────────────────
            # The ledger accumulates ActionEvent(cmd, rc, stdout, step) records
            # written by BuildAgent during execution.  We look for the most recent
            # successful pytest --collect-only invocation and use its exact command
            # string so the verification bundle matches what was verified in-loop.
            _COLLECT_ONLY_PAT = _re.compile(r"pytest\s+--collect-only", _re.IGNORECASE)
            _collect_cmd = None
            for ev in reversed(self.action_ledger.events()):
                if ev.rc == 0 and _COLLECT_ONLY_PAT.search(ev.cmd):
                    _collect_cmd = ev.cmd
                    break
            if _collect_cmd is None:
                # Fallback: use the canonical module constant.
                _collect_cmd = COLLECT_ONLY_CMD

            if not self.verified_test_commands:
                self.verified_test_commands = [_collect_cmd]

            # ── 6. Finalize ───────────────────────────────────────────────────
            configuration_success = (
                self._auto_finalize_from_verified_tests("v1_done_flag")
                or bool(self.verification_bundle)
            )
            if configuration_success:
                configuration_success = self._finalize_supervisor_artifacts(
                    configuration_success
                )

        except Exception as exc:
            run_error = str(exc)
            print(f"[v1] Error during v1 execution: {exc}")
            if self._is_transient_llm_error(exc) and self._auto_finalize_from_verified_tests(
                source="v1_auto_after_transient_error"
            ):
                configuration_success = True
                print(
                    "[v1 Auto Finalization] Transient LLM failure after a verified "
                    "test collection command. Generating Dockerfile from recorded evidence."
                )
                try:
                    configuration_success = self._finalize_supervisor_artifacts(
                        configuration_success
                    )
                except Exception as synth_exc:
                    configuration_success = False
                    run_error = f"{run_error}; auto-finalization synthesis failed: {synth_exc}"
                    print(f"[v1 Warning] Auto-finalization synthesis failed: {synth_exc}")

        finally:
            # Restore env var scope (same pattern as _run_supervisor).
            if _prev_llm_log is None:
                os.environ.pop("ENVSTATE_LLM_LOG", None)
            else:
                os.environ["ENVSTATE_LLM_LOG"] = _prev_llm_log
            self._write_run_summary(configuration_success, run_error)
            self.sandbox.close(keep_alive=keep_container)

        return configuration_success

    def _build_v1_ledger_appender(self, ledger):
        """Return a thin closure that records (cmd, rc, stdout) into the ActionLedger.

        This replaces the full _build_observer pipeline for v1 runs. In v1, the
        per-action observer's only job is to persist evidence in the ledger so that:
          - run_v1's done_flag scan can find the collect-only command.
          - Token-bucket accounting remains intact via on_usage callbacks on the roles.

        The Maintainer's interpretation work (formerly done per-action in _build_observer)
        is now performed once per cycle by Maintainer.update inside run_v1.

        Signature of the returned closure:
            appender(cmd: str, rc: int, stdout: str) -> None
        """
        from src.envstate.ledger import ActionEvent

        step_counter = [0]

        def _appender(cmd: str, rc: int, stdout: str) -> None:
            step_counter[0] += 1
            ledger.append(ActionEvent(
                cmd=cmd,
                rc=rc,
                stdout=stdout,
                step=step_counter[0],
            ))

        return _appender

    def _finalize_supervisor_artifacts(self, configuration_success):
        """Synthesize the recipe, write the Dockerfile, and generate memories — the
        same success-artifact path the legacy run() uses (agent.py:1043-1052)."""
        if not self._synthesize_final_build_recipe():
            print("[Warning] Build recipe synthesis failed. No Dockerfile will be generated.")
            return False
        dockerfile_path = os.path.join(self.workplace, "Dockerfile")
        self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
        _dockerfile_path = os.path.join(self.workplace, "Dockerfile")
        if not self._verify_cleanroom_or_fail(
            dockerfile_path=_dockerfile_path,
            build_context=self.workplace,
        ):
            return False
        self._maybe_generate_long_term_memories(configuration_success)
        return True

    def _verify_cleanroom_or_fail(self, dockerfile_path: str = "", build_context: str = "") -> bool:
        """Return True if clean-room verification passes (or is disabled).

        Rebuilds the synthesized Dockerfile from scratch and re-runs the
        host-certified test commands in a throwaway container.

        This method operates ONLY on the produced Dockerfile + build context.
        It does NOT reference self.env_snapshot / snapshot.requirements / req.source
        (those types are deleted in v1). Probe list is derived from
        self.verified_test_commands (set by _run_v1 or the supervisor paths).

        Args:
            dockerfile_path: Absolute path to the synthesized Dockerfile.
                             Defaults to <workplace>/Dockerfile when empty.
            build_context:   Directory used as Docker build context.
                             Defaults to self.workplace when empty.
        """
        if not getattr(self, "enable_cleanroom", False):
            return True

        from src.envstate.cleanroom import ensure_repo_in_dockerfile, verify_cleanroom

        _dockerfile_path = dockerfile_path or os.path.join(self.workplace, "Dockerfile")
        _build_context = build_context or self.workplace
        _workdir = getattr(self.synthesizer, "workdir", "/app")

        try:
            with open(_dockerfile_path) as _df:
                dockerfile_text = _df.read()
        except OSError as exc:
            print(f"[Clean-room] cannot read Dockerfile at {_dockerfile_path!r}: {exc}")
            return False

        dockerfile_text = ensure_repo_in_dockerfile(dockerfile_text, _workdir)

        def run_command(image_ref, command):
            try:
                result = self.sandbox.client.containers.run(
                    image_ref, command, remove=True, working_dir=_workdir
                )
                if isinstance(result, (bytes, bytearray)):
                    return 0, result.decode("utf-8", "replace")
                return 0, str(result)
            except Exception as exc:
                return getattr(exc, "exit_status", 1), str(exc)

        result = verify_cleanroom(
            self.sandbox.client,
            dockerfile_text,
            build_context_dir=_build_context,
            probe_commands=[],
            test_commands=list(self.verified_test_commands),
            run_command=run_command,
        )
        self.run_summary_cleanroom = {"passed": result.passed, "reason": result.reason}
        if not result.passed:
            print(f"[Clean-room] verification FAILED: {result.reason}")
        return result.passed

    def _record_supervisor_path_usage(self, bucket, usage):
        """Thread Supervisor/Worker/Maintainer LLM usage into the run token ledger.

        Bucket routing (§3.7):
          'supervisor' — Supervisor.next_task LLM calls (Arm B/C orchestrator on_usage)
          'worker'     — LlmWorkerPlanner / FullStateWorkerPlanner calls
          'reflection' — Maintainer (unchanged)
          'planner'    — legacy Arm-0 ReAct planner (back-compat)
        """
        if not usage:
            return
        self.run_token_ledger.add(
            bucket,
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
        )

    def run(self, max_steps=30, keep_container=False):
        """Runs the ReAct loop to configure the environment."""
        if getattr(self, "enable_v1", False):
            return self._run_v1(max_cycles=max_steps, keep_container=keep_container)
        if getattr(self, "enable_supervisor", False):
            # supervisor.py removed in v1 migration — treat as bare ReAct
            import warnings
            warnings.warn(
                "--enable-supervisor is deprecated and has no effect (supervisor.py removed). "
                "Use --enable-v1 for the v1 orchestrator.",
                DeprecationWarning,
                stacklevel=2,
            )
        if getattr(self, "enable_fullstate_worker", False):
            # fullstate_worker.py removed in v1 migration — treat as bare ReAct
            import warnings
            warnings.warn(
                "--enable-fullstate-worker is deprecated and has no effect "
                "(fullstate_worker.py removed). Use --enable-v1 for the v1 orchestrator.",
                DeprecationWarning,
                stacklevel=2,
            )
        print(f"Starting agent for repository: {self.repo_url}")
        observation = None
        configuration_success = False  # 成功标志位
        run_error = None
        invalid_final_bundle_reports = 0
        
        try:
            for step in range(max_steps):
                print(f"\n{'='*20} Step {step + 1} {'='*20}")
                
                # 1. Plan next step
                thought, action, raw_llm_output, is_finished, usage_info = (
                    self._plan_next_step_with_retry(observation)
                )
                self.run_token_ledger.add(
                    "planner",
                    input_tokens=usage_info["input_tokens"],
                    output_tokens=usage_info["output_tokens"],
                )
                
                print(
                    f"\n[Tokens] Input: {usage_info['input_tokens']}, "
                    f"Output: {usage_info['output_tokens']}, "
                    f"Total: {usage_info['total_tokens']}"
                )
                
                if is_finished:
                    print("\n[Finished] Agent has reached a conclusion.")
                    print(raw_llm_output)
                    final_answer = self.planner.extract_final_answer(raw_llm_output)
                    if final_answer == "Success":
                        if self._finalize_verification_from_agent_report(raw_llm_output):
                            configuration_success = True
                            break
                        else:
                            print("[Warning] Agent claimed success but did not provide a valid Verification Bundle.")
                            (
                                invalid_final_bundle_reports,
                                auto_finalized,
                                stop_reason,
                            ) = self._handle_invalid_final_success_report(
                                invalid_final_bundle_reports
                            )
                            if auto_finalized:
                                configuration_success = True
                                break
                            if stop_reason:
                                run_error = stop_reason
                                print(f"[Warning] {stop_reason}")
                                break
                            print("[Warning] Continuing setup instead of producing unverifiable artifacts.")
                            observation = (
                                "[SYSTEM] Final success requires a valid Verification Bundle JSON object "
                                "with a non-empty `test_commands` list immediately before "
                                "`Final Answer: Success`. Do not output an Action in the final response."
                            )
                            if self.enable_observation_compression:
                                self._record_agent_step(
                                    step_id=step + 1,
                                    thought=thought or "",
                                    action="",
                                    assistant_content=raw_llm_output,
                                    success=False,
                                    observation=observation,
                                    prompt_observation=observation,
                                    mutates_environment=False,
                                    env_revision_before=self._environment_revision,
                                    env_revision_after=self._environment_revision,
                                    planner_usage=usage_info,
                                )
                            continue
                    break

                if thought:
                    print(f"\n[Thought]\n{thought}")

                if not action:
                    print("\n[Warning] No Action detected. Asking Planner to clarify.")
                    observation = "Error: No command found. Please specify an action in 'Action: <command>' format."
                    if self.enable_observation_compression:
                        self._record_agent_step(
                            step_id=step + 1,
                            thought=thought or "",
                            action="",
                            assistant_content=raw_llm_output,
                            success=False,
                            observation=observation,
                            prompt_observation=observation,
                            mutates_environment=False,
                            env_revision_before=self._environment_revision,
                            env_revision_after=self._environment_revision,
                            planner_usage=usage_info,
                        )
                    continue

                invalid_final_bundle_reports = 0
                print(f"\n[Action]\n{action}")
                
                # 2. Execute Action in Sandbox
                env_revision_before = self._environment_revision
                is_rollback_action = self._is_explicit_rollback_action(action)
                is_memory_retrieval_action = self._is_memory_retrieval_action(action)
                if is_memory_retrieval_action:
                    print("\n[System] Agent requested long-term memory retrieval for the last failure.")
                    success, observation = True, self._retrieve_long_term_memory_observation()
                elif is_rollback_action:
                    print("\n[System] Agent requested an explicit rollback to the last successful snapshot.")
                    success, observation = self.sandbox.rollback(reason="agent_requested")
                else:
                    success, observation = self.sandbox.execute(action)
                prompt_observation = self._prepare_observation_for_prompt(observation)
                
                print(f"\n[Observation]\n{observation if observation.strip() else '(No output)'}")
                
                # 3. Synthesize if successful
                mutates_environment = False
                accepted_observation_final = False
                if success and not is_rollback_action and not is_memory_retrieval_action:
                    self.synthesizer.record_success(action)
                    mutates_environment = self.synthesizer.command_mutates_environment(action)
                    self._record_successful_action(step + 1, action, observation)
                    if self._observation_contains_final_success_bundle(observation):
                        accepted_observation_final = self._finalize_verification_from_agent_report(
                            observation,
                            source="agent_observation",
                        )
                        if accepted_observation_final:
                            configuration_success = True
                else:
                    if not success:
                        print(
                            "\n[System] Command failed. Current container state was preserved unless the sandbox "
                            "had to recover from an unhealthy container."
                        )
                        if not is_rollback_action and not is_memory_retrieval_action:
                            self._record_failed_action(step + 1, action, prompt_observation)
                            self._remember_failure_for_memory(action, prompt_observation)
                            prompt_observation = self._append_long_term_memory_hint(prompt_observation)

                if self.enable_observation_compression:
                    self._record_agent_step(
                        step_id=step + 1,
                        thought=thought or "",
                        action=action,
                        assistant_content=raw_llm_output,
                        success=success,
                        observation=observation,
                        prompt_observation=prompt_observation,
                        mutates_environment=mutates_environment,
                        env_revision_before=env_revision_before,
                        env_revision_after=self._environment_revision,
                        planner_usage=usage_info,
                    )
                else:
                    observation = prompt_observation
                if accepted_observation_final:
                    break

            # 4. Final Output - 只有配置成功才生成 Dockerfile
            if configuration_success:
                print(f"\n{'='*20} Environment Configuration Complete {'='*20}")
                if self._synthesize_final_build_recipe():
                    # 生成 Dockerfile 到 workplace 目录
                    dockerfile_path = os.path.join(self.workplace, "Dockerfile")
                    self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
                    self._self_verify_and_repair(dockerfile_path)
                    self._maybe_generate_long_term_memories(configuration_success)
                else:
                    configuration_success = False
                    print("[Warning] Build recipe synthesis failed. No Dockerfile will be generated.")
            else:
                print(f"\n{'='*20} Environment Configuration FAILED {'='*20}")
                print("[Warning] Configuration did not complete successfully. No Dockerfile will be generated.")
            
        except Exception as e:
            run_error = str(e)
            print(f"An error occurred during execution: {e}")
            if self._is_transient_llm_error(e) and self._auto_finalize_from_verified_tests(
                source="auto_after_transient_error"
            ):
                configuration_success = True
                print(
                    "[Auto Finalization] Transient LLM failure happened after a verified "
                    "test collection command. Generating Dockerfile from recorded evidence."
                )
                try:
                    if self._synthesize_final_build_recipe():
                        dockerfile_path = os.path.join(self.workplace, "Dockerfile")
                        self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
                        self._self_verify_and_repair(dockerfile_path)
                        self._maybe_generate_long_term_memories(configuration_success)
                    else:
                        configuration_success = False
                        print("[Warning] Build recipe synthesis failed after auto-finalization.")
                except Exception as synth_exc:
                    configuration_success = False
                    run_error = f"{run_error}; auto-finalization synthesis failed: {synth_exc}"
                    print(f"[Warning] Auto-finalization synthesis failed: {synth_exc}")
        finally:
            self._write_run_summary(configuration_success, run_error)
            self.sandbox.close(keep_alive=keep_container)

    def _plan_next_step_with_retry(self, observation, max_attempts=8):
        """Retry transient LLM transport failures without discarding the setup run."""
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                if self.enable_observation_compression:
                    return self.planner.plan(
                        repo_url=self.repo_url,
                        manage_history=False,
                    )
                return self.planner.plan(
                    self.repo_url,
                    observation,
                )
            except Exception as exc:
                last_error = exc
                if not self._is_transient_llm_error(exc) or attempt >= max_attempts:
                    raise
                wait_seconds = min(2 ** attempt, 30)
                print(
                    f"[Planner Retry] Transient LLM error on attempt {attempt}/{max_attempts}: "
                    f"{exc}. Retrying in {wait_seconds}s..."
                )
                time.sleep(wait_seconds)
        raise last_error

    def _is_transient_llm_error(self, exc):
        text = str(exc).lower()
        transient_markers = (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "remote protocol error",
            "rate limit",
            "server error",
            "502",
            "503",
            "504",
        )
        return any(marker in text for marker in transient_markers)

    def _create_sandbox(self, base_image, platform_override):
        return Sandbox(
            base_image=base_image,
            workdir="/app",
            platform=platform_override,  # Use linux/amd64 if ARM64 issues detected
            seed_dir=self.workplace,
            command_timeout_seconds=self.command_timeout_seconds,
        )

    def _is_explicit_rollback_action(self, action):
        normalized = (action or "").strip()
        return normalized in {"__ROLLBACK__", "__ROLLBACK_TO_LAST_SUCCESS__"}

    def _is_memory_retrieval_action(self, action):
        return (action or "").strip() == "__RETRIEVE_MEMORY__"

    def _remember_failure_for_memory(self, action, observation):
        if not self.enable_long_term_memory:
            return
        self.last_failed_memory_context = {
            "command": action or "",
            "observation": observation or "",
            "repo_url": self.repo_url,
            "services": sorted(getattr(self, "required_local_services", set())),
        }

    def _append_long_term_memory_hint(self, observation):
        if not self.enable_long_term_memory:
            return observation
        if "[Long-Term Memory Hint]" in (observation or ""):
            return observation

        failure_count = len(getattr(self, "failed_actions", []))
        if failure_count <= 1:
            lead = "This command failed."
        else:
            lead = f"{failure_count} commands have failed during this setup run."
        hint = (
            "[Long-Term Memory Hint]\n"
            f"{lead} If the fix is not obvious, or this looks like a repeated "
            "dependency, package-manager, service, network, compatibility, or "
            "verification-signal problem, strongly consider making the next "
            "Action exactly `__RETRIEVE_MEMORY__` before trying another "
            "speculative fix. Use normal diagnosis if the fix is already clear "
            "from the command output."
        )
        return f"{(observation or '').rstrip()}\n\n{hint}".strip()

    def _retrieve_long_term_memory_observation(self):
        if not self.enable_long_term_memory or not self.memory_manager:
            return (
                "Long-term memory retrieval is disabled. Continue diagnosing from "
                "the current command output."
            )

        if not self.last_failed_memory_context:
            return (
                "Long-term memory retrieval was requested, but there is no recent "
                "failed command context. Continue with a normal diagnostic command."
            )

        self.memory_stats["retrievals"] += 1
        try:
            results, _query = self.memory_manager.retrieve(
                failed_command=self.last_failed_memory_context.get("command", ""),
                failed_observation=self.last_failed_memory_context.get("observation", ""),
                repo_url=self.last_failed_memory_context.get("repo_url", self.repo_url),
                services=self.last_failed_memory_context.get("services", []),
            )
        except Exception as exc:
            message = f"Long-term memory retrieval failed: {exc}"
            self.memory_stats["errors"].append(message)
            print(f"[Long-Term Memory] {message}")
            return (
                "Long-term memory retrieval failed due to a system-side error. "
                f"Error: {exc}\n"
                "Continue diagnosing from the current command output."
            )

        self.memory_stats["retrieval_hits"] += len(results)
        return self.memory_manager.format_retrieval_results(results)

    def _synthesize_final_build_recipe(self):
        if getattr(self, "enable_envstate", False) and self.action_ledger is not None:
            from src.envstate.synthesis import build_commands_from_ledger
            ledger_commands = build_commands_from_ledger(
                self.action_ledger,
                distill=self.synthesizer._extract_recordable_setup_commands,
            )
            if ledger_commands:
                # Only build_commands become Dockerfile RUN steps. The verification
                # bundle (runtime_prep + test_commands) is already set on self by
                # _auto_finalize_from_verified_tests / the agent-report finalizer and
                # is serialized separately into the run summary — it is NOT part of the
                # Dockerfile body.
                self.synthesizer.apply_build_recipe({
                    "build_commands": ledger_commands,
                    "post_test_patch_commands": [],
                    "runtime_preparation_commands": [],
                    "test_commands": [],
                    "excluded_commands": [],
                    "rationale": "Assembled from ActionLedger (replayable actions only).",
                    "confidence": "high",
                })
                self.build_recipe = {
                    "build_commands": ledger_commands,
                    "source": "action_ledger",
                }
                self.build_recipe_source = "action_ledger"
                return True
        recipe_input = self._build_recipe_synthesis_input()
        result = self.synthesizer.synthesize_build_recipe(
            self.client,
            self.model,
            recipe_input,
            log_dir=getattr(self, "setup_log_dir", None),
        )
        self.build_recipe = result.recipe
        self.build_recipe_source = result.source
        self.build_recipe_error = result.error

        if result.usage:
            self.run_token_ledger.add(
                "recipe",
                input_tokens=result.usage.get("input_tokens", 0),
                output_tokens=result.usage.get("output_tokens", 0),
            )

        if result.error:
            print(
                "[Build Recipe] LLM synthesis failed; refusing to generate an unverifiable Dockerfile. "
                f"Error: {result.error}"
            )
            return False
        else:
            print(
                "[Build Recipe] Synthesized "
                f"{len(self.build_recipe.get('build_commands', []))} build command(s), "
                f"{len(self.build_recipe.get('post_test_patch_commands', []))} "
                "post-test-patch command(s)."
            )
            return True

    def _self_verify_and_repair(self, dockerfile_path):
        """DEPRECATED (2026-06-08): superseded by the runner-side repair loop
        (run_rat_benchmark.py:_repair_and_rescore via repo2run_repair_port.py).
        Retained and toggleable via enable_post_synthesis_repair / --repair-mode.
        Do not extend — port improvements to the runner loop instead.

        Build the synthesized recipe in a clean room, run the verified test command,
        and repair the recipe (deterministic + LLM) if the environment is incomplete.

        Recipe-level: on a resolved repair the repaired recipe is re-applied via
        ``apply_build_recipe`` and the Dockerfile is regenerated, so every downstream
        consumer inherits the fix. Fully guarded — never blocks finalization.
        """
        if not getattr(self, "enable_post_synthesis_repair", False):
            return
        if not getattr(self, "verified_test_command", None):
            return
        try:
            slug = re.sub(r"[^a-z0-9]+", "-", (self.repo_url or "repo").lower()).strip("-")[:60] or "repo"
            result = verify_and_repair_recipe(
                recipe=self.build_recipe,
                synthesizer=self.synthesizer,
                repo_url=self.repo_url,
                base_commit=self.base_commit,
                workdir=self.synthesizer.workdir,
                verified_test_command=self.verified_test_command,
                runtime_preparation_commands=self.verified_runtime_preparation_commands,
                workspace_root=self.workplace,
                client=self.client,
                model=self.model,
                image_tag=f"dockeragent-selfverify-{slug}",
                platform=getattr(self, "platform_override", None),
                max_rounds=getattr(self, "self_verify_max_rounds", 2),
            )
        except Exception as exc:
            print(f"[Self-Verify] Phase errored ({exc}); keeping original artifact.")
            self.self_verify_result = {"status": "phase_error", "error": str(exc)}
            return

        self.self_verify_result = {k: result.get(k) for k in ("status", "changed", "rounds")}
        if result.get("status") == "resolved" and result.get("changed"):
            self.synthesizer.apply_build_recipe(result["recipe"])
            # Own copy — apply_build_recipe aliases result["recipe"] onto the synthesizer.
            self.build_recipe = copy.deepcopy(result["recipe"])
            self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
            print(f"[Self-Verify] Adopted repaired recipe; regenerated {dockerfile_path}.")
        else:
            print(f"[Self-Verify] status={result.get('status')}; keeping original recipe.")

    def _build_recipe_synthesis_input(self):
        run_summary = self._build_run_summary(configuration_success=True, run_error=None)
        setup_log_summary_text = self._build_setup_log_summary_text()

        return {
            "task": {
                "repo_url": self.repo_url,
                "base_commit": self.base_commit,
                "base_image": self.synthesizer.base_image,
                "workdir": self.synthesizer.workdir,
                "language": self.language,
                "required_local_services": sorted(getattr(self, "required_local_services", set())),
            },
            "final_verification_bundle": self.verification_bundle or {
                "runtime_preparation_commands": self.verified_runtime_preparation_commands,
                "test_commands": self.verified_test_commands,
            },
            "setup_log_summary_text": setup_log_summary_text,
            "agent_run_summary": run_summary,
        }

    def _build_setup_log_summary_text(self):
        setup_log_text = self._read_latest_setup_log()
        extracted_trajectory = self._extract_setup_log_trajectory(setup_log_text)
        if not extracted_trajectory:
            return ""

        summarize_setup_log = getattr(self.synthesizer, "summarize_setup_log_for_recipe", None)
        if not callable(summarize_setup_log) or not getattr(self, "client", None):
            return extracted_trajectory

        result = summarize_setup_log(
            self.client,
            self.model,
            extracted_trajectory,
            log_dir=getattr(self, "setup_log_dir", None),
        )
        ledger_add = getattr(self.run_token_ledger, "add", None)
        if callable(ledger_add) and result.usage:
            ledger_add(
                "recipe",
                input_tokens=result.usage.get("input_tokens", 0),
                output_tokens=result.usage.get("output_tokens", 0),
            )

        if result.error:
            print(
                "[Setup Log Summary] LLM summarization failed; falling back to extracted trajectory. "
                f"Error: {result.error}"
            )
            return extracted_trajectory

        return result.summary_text or extracted_trajectory

    def _extract_setup_log_trajectory(self, setup_log_text):
        if not setup_log_text:
            return self._serialize_agent_steps_for_setup_log_summary()

        human_section = self._extract_log_section(
            setup_log_text,
            SETUP_LOG_HUMAN_MESSAGE_HEADER,
            SETUP_LOG_RAW_AI_MESSAGE_HEADER,
        )
        if not human_section:
            return self._serialize_agent_steps_for_setup_log_summary() or ""

        assistant_start = human_section.find("[ASSISTANT]")
        trajectory_text = human_section[assistant_start:] if assistant_start != -1 else human_section
        steps = self._parse_setup_log_steps(trajectory_text)
        if steps:
            return self._serialize_setup_log_steps_for_summary(steps)

        return self._serialize_agent_steps_for_setup_log_summary() or self._truncate_for_recipe(
            trajectory_text.strip(),
            4000,
        )

    def _extract_log_section(self, text, start_marker, end_marker):
        if not text:
            return ""
        start = text.find(start_marker)
        if start == -1:
            return ""
        start += len(start_marker)
        end = text.find(end_marker, start)
        if end == -1:
            end = len(text)
        return text[start:end].strip()

    def _parse_setup_log_steps(self, trajectory_text):
        if not trajectory_text:
            return []

        blocks = re.split(r"\n(?=\[ASSISTANT\]\n)", trajectory_text.strip())
        steps = []
        for block in blocks:
            block = block.strip()
            if not block.startswith("[ASSISTANT]"):
                continue
            body = block[len("[ASSISTANT]"):].strip()
            match = re.match(
                r"Thought:\s*(.*?)\nAction:\s*(.*?)\n\nObservation:\s*(.*)\Z",
                body,
                re.DOTALL,
            )
            if not match:
                continue
            steps.append(
                {
                    "thought": match.group(1).strip(),
                    "action": match.group(2).strip(),
                    "observation": match.group(3).strip(),
                }
            )
        return steps

    def _serialize_setup_log_steps_for_summary(self, steps):
        if not steps:
            return ""

        blocks = []
        for index, step in enumerate(steps, start=1):
            thought = self._truncate_for_recipe(
                step.get("thought", "").strip(),
                SETUP_LOG_SUMMARY_THOUGHT_MAX_CHARS,
            )
            observation = self._truncate_for_recipe(
                step.get("observation", "").strip(),
                SETUP_LOG_SUMMARY_OBSERVATION_MAX_CHARS,
            )
            blocks.append(
                "\n".join(
                    [
                        f"Step {index}",
                        f"Thought: {thought}",
                        f"Action: {step.get('action', '').strip()}",
                        "Observation:",
                        observation,
                    ]
                ).strip()
            )
        return "\n\n".join(blocks)

    def _serialize_agent_steps_for_setup_log_summary(self):
        if not getattr(self, "agent_steps", None):
            return ""

        steps = []
        for step in self.agent_steps:
            steps.append(
                {
                    "thought": step.thought or "",
                    "action": step.action or "",
                    "observation": step.observation_prompt or step.observation_raw or "",
                }
            )
        return self._serialize_setup_log_steps_for_summary(steps)

    def _build_recipe_trajectory(self):
        if self.agent_steps:
            trajectory = []
            for step in self.agent_steps:
                trajectory.append({
                    "step_id": step.step_id,
                    "command": step.action,
                    "success": step.success,
                    "mutates_environment": step.mutates_environment,
                    "env_revision_before": step.env_revision_before,
                    "env_revision_after": step.env_revision_after,
                    "compressed": step.compression.applied,
                    "observation_summary": self._truncate_for_recipe(
                        step.observation_prompt or step.observation_raw,
                        1600,
                    ),
                })
            return trajectory

        combined = []
        for record in self.successful_actions:
            combined.append({**record, "success": True})
        for record in self.failed_actions:
            combined.append({**record, "success": False})
        combined.sort(key=lambda item: item.get("step_index", 0))
        return self._compact_action_records(combined)

    def _compact_action_records(self, records):
        compact = []
        for record in records or []:
            if not isinstance(record, dict):
                continue
            compact.append({
                "step_index": record.get("step_index"),
                "command": record.get("command", ""),
                "success": record.get("success", True),
                "mutates_environment": record.get("mutates_environment"),
                "is_readonly": record.get("is_readonly"),
                "is_runtime_service": record.get("is_runtime_service"),
                "is_runtime_healthcheck": record.get("is_runtime_healthcheck"),
                "test_analysis": record.get("test_analysis"),
                "observation_summary": self._truncate_for_recipe(
                    record.get("observation", ""),
                    1200,
                ),
            })
        return compact

    def _truncate_for_recipe(self, text, max_chars):
        text = text or ""
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head
        return (
            text[:head]
            + f"\n... ({len(text) - max_chars} chars omitted for recipe synthesis) ...\n"
            + text[-tail:]
        )

    def _record_failed_action(self, step_index, action, observation):
        self._append_action_event(
            step_index, action, rc=1, mutation_class=None,
            env_revision_before=self._environment_revision,
            env_revision_after=self._environment_revision,
            summary=(observation or "")[:200],
        )
        self.failed_actions.append({
            "step_index": step_index,
            "command": action or "",
            "success": False,
            "observation": observation or "",
            "environment_revision": self._environment_revision,
            "mutates_environment": False,
            "is_readonly": self.synthesizer.is_readonly_command(action or ""),
            "is_runtime_service": self.synthesizer.is_runtime_service_command(action or ""),
            "is_runtime_healthcheck": self.synthesizer.is_runtime_healthcheck_command(action or ""),
            "test_analysis": self.synthesizer.analyze_test_run(action or "", observation or ""),
        })

    def _maybe_generate_long_term_memories(self, configuration_success):
        if not configuration_success or not self.enable_long_term_memory or not self.memory_manager:
            return

        self.memory_stats["generation_attempted"] = True
        setup_log_text = self._read_latest_setup_log()
        if not setup_log_text:
            self.memory_stats["errors"].append("No setup log found for memory generation.")
            print("[Long-Term Memory] No setup log found; skipping memory generation.")
            return

        try:
            result = self.memory_manager.generate_memories_from_run(
                setup_log_text=setup_log_text,
                run_summary=self._build_run_summary(configuration_success, run_error=None),
                repo_url=self.repo_url,
            )
        except Exception as exc:
            message = f"Long-term memory generation failed: {exc}"
            self.memory_stats["errors"].append(message)
            print(f"[Long-Term Memory] {message}")
            return

        self.run_token_ledger.add(
            "memory",
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )
        self.memory_stats["candidate_memories"] += result.candidate_count
        self.memory_stats["written_memories"] += result.written
        self.memory_stats["skipped_duplicates"] += result.skipped_duplicates
        self.memory_stats["skipped_invalid"] += result.skipped_invalid
        self.memory_stats["relation_judged_pairs"] += result.relation_judged_pairs
        self.memory_stats["relation_links_accepted"] += result.relation_links_accepted
        self.memory_stats["relation_links_rejected"] += result.relation_links_rejected
        self.memory_stats["relation_duplicates_rejected"] += result.relation_duplicates_rejected
        self.memory_stats["errors"].extend(result.relation_judge_errors)
        if result.error:
            self.memory_stats["errors"].append(result.error)
        print(
            "[Long-Term Memory] Generation complete: "
            f"{result.written} written, "
            f"{result.skipped_duplicates} duplicate(s), "
            f"{result.skipped_invalid} invalid candidate(s)."
        )
        if result.error:
            print(f"[Long-Term Memory] Generation warning: {result.error}")

    def _read_latest_setup_log(self):
        setup_log_dir = getattr(self, "setup_log_dir", None)
        if not setup_log_dir or not os.path.isdir(setup_log_dir):
            return ""

        candidates = [
            os.path.join(setup_log_dir, filename)
            for filename in os.listdir(setup_log_dir)
            if filename.endswith(".md") and os.path.splitext(filename)[0].isdigit()
        ]
        if not candidates:
            return ""

        def sort_key(path):
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem.isdigit():
                return (0, int(stem))
            return (1, os.path.getmtime(path))

        latest_path = sorted(candidates, key=sort_key)[-1]
        try:
            with open(latest_path, "r", encoding="utf-8") as file_obj:
                return file_obj.read()
        except OSError as exc:
            self.memory_stats["errors"].append(f"Could not read latest setup log: {exc}")
            return ""

    def _record_agent_step(
        self,
        step_id,
        thought,
        action,
        assistant_content,
        success,
        observation,
        prompt_observation,
        mutates_environment,
        env_revision_before,
        env_revision_after,
        planner_usage,
    ):
        step = AgentStep(
            step_id=step_id,
            thought=thought,
            action=action,
            success=success,
            exit_code=None,
            mutates_environment=mutates_environment,
            env_revision_before=env_revision_before,
            env_revision_after=env_revision_after,
            observation_raw=observation or "",
            observation_prompt=prompt_observation or "",
        )
        step.metadata = build_observation_metadata(step.observation_raw)
        step.metadata["prompt_chars"] = len(step.observation_prompt)
        step.metadata["safety_compressed"] = step.observation_prompt != step.observation_raw
        step.token_usage.planner_input_tokens = planner_usage["input_tokens"]
        step.token_usage.planner_output_tokens = planner_usage["output_tokens"]
        self.agent_steps.append(step)

        self.planner.append_step(
            step_id=step_id,
            assistant_content=assistant_content,
            observation_content=step.observation_prompt,
        )
        self._maybe_compress_old_observation()

    def _maybe_compress_old_observation(self):
        if not self.enable_observation_compression or not self.observation_compressor:
            return

        target_idx = len(self.agent_steps) - 1 - self.compression_delay
        if target_idx < 0:
            return

        target_step = self.agent_steps[target_idx]
        if target_step.compression.applied:
            return

        if target_step.metadata.get("safety_compressed"):
            return

        if len(target_step.observation_raw or "") < self.compression_threshold_chars:
            return

        self.compression_stats["candidate_steps"] += 1
        start_idx = max(0, target_idx - self.compression_context_before)
        context_steps = self.agent_steps[start_idx:]

        try:
            reduced_result, record = self.observation_compressor.compress(
                target_step=target_step,
                context_steps=context_steps,
            )
        except Exception as exc:
            target_step.compression.applied = False
            target_step.compression.reason = f"compression_error: {exc}"
            self.memory_stats["errors"].append(f"Observation compression failed: {exc}")
            print(f"[Compression Warning] Observation compression failed; keeping raw observation: {exc}")
            return
        apply_ok, reason = should_apply_compression(
            target_step,
            record,
            compress_threshold_chars=self.compression_threshold_chars,
            benefit_threshold_tokens=self.compression_benefit_tokens,
        )
        record.applied = apply_ok
        record.reason = reason
        target_step.compression = record
        target_step.token_usage.reflect_input_tokens = record.reflect_input_tokens
        target_step.token_usage.reflect_output_tokens = record.reflect_output_tokens

        self.run_token_ledger.add(
            "reflection",
            input_tokens=record.reflect_input_tokens,
            output_tokens=record.reflect_output_tokens,
        )

        if not apply_ok:
            return

        replaced = self.planner.replace_observation(target_step.step_id, reduced_result)
        if not replaced:
            target_step.compression.applied = False
            target_step.compression.reason = "target_step_not_in_managed_history"
            return

        target_step.observation_prompt = reduced_result
        self.compression_stats["compressed_steps"] += 1
        self.compression_stats["saved_tokens_est"] += record.saved_tokens_est

    def _prepare_observation_for_prompt(self, observation):
        prompt_observation, applied = safety_compress_observation(
            observation or "",
            threshold_chars=self.safety_compression_threshold_chars,
            target_chars=self.safety_compression_target_chars,
        )
        if applied:
            print(
                "[Safety Compression] Reduced observation from "
                f"{len(observation or '')} to {len(prompt_observation)} chars "
                "before adding it to planner history."
            )
        return prompt_observation

    def _append_action_event(self, step_index, action, rc, mutation_class,
                             env_revision_before, env_revision_after, summary):
        """Append one ActionEvent to the ActionLedger (no-op when EnvState is off).

        NOTE 1: ActionEvent.rc is a SUCCESS PROXY (0 on success, 1 on failure), not a
        true exit code — Sandbox.execute() collapses the real rc into a bool before the
        agent ever sees it. Probes (exec_readonly) DO carry real exit codes. Downstream
        ledger consumers (synthesis) only branch on rc==0 vs !=0, so the proxy is safe;
        a true-rc ledger is deferred.
        NOTE 2: env_revision_* are passed in by the caller so they share the SAME source
        as the successful_actions record's environment_revision — no duplicated stamping.
        """
        if not getattr(self, "enable_envstate", False) or self.action_ledger is None:
            return
        from src.envstate.ledger import ActionEvent
        self.action_ledger.append(ActionEvent(
            step=step_index,
            task_id=getattr(self, "current_task_id", None),
            cmd=action,
            rc=rc,
            stdout_path=None,
            stderr_path=None,
            env_revision_before=env_revision_before,
            env_revision_after=env_revision_after,
            mutation_class=mutation_class,
            container_id=getattr(self, "env_container_id", ""),
            summary=summary,
        ))

    def _record_successful_action(self, step_index, action, observation):
        """Track successful actions and maintain the final contiguous verification block."""
        mutates_environment = self.synthesizer.command_mutates_environment(action)
        is_readonly = self.synthesizer.is_readonly_command(action)
        is_runtime_service = self.synthesizer.is_runtime_service_command(action)
        is_runtime_healthcheck = self.synthesizer.is_runtime_healthcheck_command(action)
        observed_test_signal = self.synthesizer.observation_has_effective_test_signal(observation)
        analysis = self.synthesizer.analyze_test_run(action, observation)

        record_revision = self._environment_revision + (1 if mutates_environment else 0)
        mutation_class = self.synthesizer.classify_mutation(action) if mutates_environment else None
        self._append_action_event(
            step_index, action, rc=0, mutation_class=mutation_class,
            env_revision_before=self._environment_revision, env_revision_after=record_revision,
            summary=(observation or "")[:200],
        )

        self.successful_actions.append({
            "step_index": step_index,
            "command": action,
            "observation": observation,
            "environment_revision": record_revision,
            "mutates_environment": mutates_environment,
            "is_readonly": is_readonly,
            "is_runtime_service": is_runtime_service,
            "is_runtime_healthcheck": is_runtime_healthcheck,
            "observed_test_signal": observed_test_signal,
            "test_analysis": analysis,
        })

        if mutates_environment:
            self._environment_revision += 1
            self._invalidate_verification_group("environment_mutation")

        if not analysis["is_test_command"]:
            if mutates_environment:
                self._invalidate_verification_group("non_test_environment_mutation_after_verification")
            return

        self.test_run_attempts.append({
            "step_index": step_index,
            "command": action,
            "environment_revision": self._environment_revision,
            "effective": analysis["is_effective_test_run"],
            "confidence": analysis["confidence"],
            "reason": analysis["reason"],
        })

        if not analysis["is_effective_test_run"]:
            self._invalidate_verification_group("ineffective_test_command")
            print(f"[Skipped Test Command] {action} ({analysis['reason']}).")
            return

        self.successful_test_commands.append(action)
        self._current_verification_group.append(action)
        self.verified_test_commands = list(self._current_verification_group)
        self.verified_test_command = self.verified_test_commands[-1]
        print(f"[Recorded Test Command] {action}")
        print(f"[Verification Block] {len(self.verified_test_commands)} command(s) in final candidate block.")

    def _observation_contains_final_success_bundle(self, observation):
        if not observation:
            return False
        return bool(
            re.search(r"^\s*Verification Bundle:", observation, re.IGNORECASE | re.MULTILINE)
            and re.search(r"^\s*Final Answer:\s*Success\b", observation, re.IGNORECASE | re.MULTILINE)
        )

    def _auto_finalize_from_verified_tests(self, source):
        if not self.verified_test_commands:
            return False

        self.verification_source = source
        self.verification_bundle = {
            "runtime_preparation_commands": list(self.verified_runtime_preparation_commands),
            "test_commands": list(self.verified_test_commands),
        }
        if not self.verified_test_command:
            self.verified_test_command = self.verified_test_commands[-1]
        return True

    def _handle_invalid_final_success_report(self, invalid_report_count):
        if self._auto_finalize_from_verified_tests("auto_finalized_after_invalid_agent_report"):
            print("[Verification Bundle] Auto-finalized from previously verified test commands.")
            return invalid_report_count, True, None

        invalid_report_count += 1
        if invalid_report_count >= MAX_INVALID_FINAL_BUNDLE_REPORTS:
            return (
                invalid_report_count,
                False,
                (
                    "Agent repeatedly emitted invalid final Verification Bundles "
                    "without any previously verified test command."
                ),
            )

        return invalid_report_count, False, None

    def _finalize_verification_from_agent_report(self, raw_llm_output, source="agent_report"):
        bundle = self._extract_verification_bundle(raw_llm_output)
        if not bundle:
            return False

        runtime_commands = self._normalize_command_list(
            bundle.get("runtime_preparation_commands")
        )
        test_commands = self._normalize_command_list(bundle.get("test_commands"))
        if not test_commands:
            print("[Verification Bundle] Missing non-empty `test_commands`; ignoring agent-reported bundle.")
            return False

        supported_bundle = derive_supported_verification_bundle(
            {
                "environment_revision": self._environment_revision,
                "verification_bundle": {
                    "runtime_preparation_commands": list(runtime_commands),
                    "test_commands": list(test_commands),
                },
                "verified_runtime_preparation_commands": self.verified_runtime_preparation_commands,
                "verified_test_commands": self.verified_test_commands,
                "verified_test_command": self.verified_test_command,
                "successful_actions": self.successful_actions,
            },
            synthesizer=self.synthesizer,
        )
        supported_runtime_commands = list(supported_bundle.get("runtime_preparation_commands") or [])
        supported_test_commands = list(supported_bundle.get("test_commands") or [])

        if supported_runtime_commands != list(runtime_commands) or supported_test_commands != list(test_commands):
            print(
                "[Verification Bundle] Rejected agent-reported bundle because at least one command "
                "was not previously observed succeeding in the final environment."
            )
            return False

        self.verified_runtime_preparation_commands = supported_runtime_commands
        self.verified_test_commands = supported_test_commands
        self.verified_test_command = self.verified_test_commands[-1]
        self.verification_source = source
        self.verification_bundle = {
            "runtime_preparation_commands": supported_runtime_commands,
            "test_commands": supported_test_commands,
        }
        source_label = (
            "a successful command observation"
            if source == "agent_observation"
            else "the agent report"
        )
        print(
            "[Verification Bundle] Accepted "
            f"{len(supported_runtime_commands)} runtime preparation command(s) and "
            f"{len(supported_test_commands)} test command(s) from {source_label}."
        )
        return True

    def _normalize_command_list(self, commands):
        if isinstance(commands, str):
            commands = [commands]

        normalized = []
        for command in commands or []:
            if not command:
                continue
            stripped = command.strip()
            if stripped:
                normalized.append(stripped)
        return normalized

    def _extract_verification_bundle(self, raw_llm_output):
        if not raw_llm_output:
            return None

        candidates = []
        success_matches = list(
            re.finditer(
                r"^\s*Final Answer:\s*Success\b",
                raw_llm_output,
                re.IGNORECASE | re.MULTILINE,
            )
        )
        if success_matches:
            for success_match in reversed(success_matches):
                prefix = raw_llm_output[:success_match.start()]
                marker_matches = list(
                    re.finditer(
                        r"^\s*Verification Bundle:",
                        prefix,
                        re.IGNORECASE | re.MULTILINE,
                    )
                )
                for marker_match in reversed(marker_matches):
                    candidate = prefix[marker_match.end():].strip()
                    if candidate:
                        candidates.append(candidate)
                if not marker_matches:
                    for json_start in reversed(list(re.finditer(r"\{", prefix))):
                        candidates.append(prefix[json_start.start():].strip())
        else:
            marker_matches = list(
                re.finditer(
                    r"^\s*Verification Bundle:",
                    raw_llm_output,
                    re.IGNORECASE | re.MULTILINE,
                )
            )
            for marker_match in reversed(marker_matches):
                candidate = raw_llm_output[marker_match.end():].strip()
                final_answer_match = re.search(
                    r"^\s*Final Answer:",
                    candidate,
                    re.IGNORECASE | re.MULTILINE,
                )
                if final_answer_match:
                    candidate = candidate[:final_answer_match.start()].strip()
                if candidate:
                    candidates.append(candidate)

        for candidate in candidates:
            parsed = self._parse_verification_bundle_candidate(candidate)
            if parsed is not None:
                return parsed

        print("[Verification Bundle] Could not locate a JSON object in the final answer.")
        return None

    def _parse_verification_bundle_candidate(self, candidate):
        candidate = (candidate or "").strip()
        if not candidate:
            return None

        if candidate.startswith("```"):
            fenced_match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```",
                candidate,
                re.DOTALL,
            )
            if fenced_match:
                candidate = fenced_match.group(1).strip()

        json_blob = self._extract_first_json_object(candidate)
        if not json_blob:
            return None

        try:
            parsed = json.loads(json_blob)
        except json.JSONDecodeError as exc:
            print(f"[Verification Bundle] Failed to parse JSON: {exc}")
            return None

        if not isinstance(parsed, dict):
            print("[Verification Bundle] Parsed payload is not a JSON object.")
            return None
        return parsed

    def _extract_first_json_object(self, text):
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return None

    def _invalidate_verification_group(self, reason):
        """Drop previously verified commands when later actions mean they no longer prove the final environment."""
        if not self._current_verification_group:
            self.verified_test_commands = []
            self.verified_test_command = None
            return

        print(f"[Verification Reset] Clearing final verification block due to: {reason}.")
        self._current_verification_group = []
        self.verified_test_commands = []
        self.verified_test_command = None

    def _build_run_summary(self, configuration_success, run_error=None):
        summary = {
            "repo_url": self.repo_url,
            "configuration_success": configuration_success,
            "base_image": getattr(getattr(self, "synthesizer", None), "base_image", None),
            "platform_override": getattr(self, "platform_override", None),
            "verified_test_command": self.verified_test_command,
            "verified_test_commands": self.verified_test_commands,
            "verified_runtime_preparation_commands": self.verified_runtime_preparation_commands,
            "successful_test_commands": self.successful_test_commands,
            "test_run_attempts": self.test_run_attempts,
            "successful_actions": self._compact_action_records(
                getattr(self, "successful_actions", [])
            ),
            "failed_actions": self._compact_action_records(getattr(self, "failed_actions", [])),
            "verification_source": self.verification_source,
            "verification_bundle": self.verification_bundle,
            "benchmark_evaluation_target": self.benchmark_evaluation_target,
            "build_recipe": getattr(self, "build_recipe", None),
            "build_recipe_source": getattr(self, "build_recipe_source", None),
            "build_recipe_error": getattr(self, "build_recipe_error", None),
            "self_verify_result": getattr(self, "self_verify_result", None),
            "required_local_services": sorted(getattr(self, "required_local_services", set())),
            "observation_compression_enabled": self.enable_observation_compression,
            "compression_stats": self.compression_stats,
            "long_term_memory_enabled": self.enable_long_term_memory,
            "memory_path": str(self.memory_manager.memory_path) if self.memory_manager else self.memory_path,
            "memory_embedding_model": self.memory_embedding_model,
            "command_timeout_seconds": getattr(self, "command_timeout_seconds", None),
            "memory_stats": self.memory_stats,
            "steps": [
                {
                    "step_id": step.step_id,
                    "action": step.action,
                    "success": step.success,
                    "raw_chars": step.metadata.get("raw_chars", 0),
                    "raw_tokens_est": step.metadata.get("raw_tokens_est", 0),
                    "compressed": step.compression.applied,
                    "compression_reason": step.compression.reason,
                    "saved_tokens_est": step.compression.saved_tokens_est,
                    "reflect_input_tokens": step.compression.reflect_input_tokens,
                    "reflect_output_tokens": step.compression.reflect_output_tokens,
                }
                for step in self.agent_steps
            ],
            "token_usage": {
                "image_selector": self.run_token_ledger.image_selector.__dict__,
                "planner": self.run_token_ledger.planner.__dict__,
                "supervisor": self.run_token_ledger.supervisor.__dict__,
                "worker": self.run_token_ledger.worker.__dict__,
                "reflection": self.run_token_ledger.reflection.__dict__,
                "memory": self.run_token_ledger.memory.__dict__,
                "recipe": getattr(self.run_token_ledger, "recipe", {}).__dict__
                if hasattr(getattr(self.run_token_ledger, "recipe", {}), "__dict__")
                else getattr(self.run_token_ledger, "recipe", {}),
                "total": self.run_token_ledger.total.__dict__,
            },
            "error": run_error,
        }
        if getattr(self, "enable_envstate", False) and self.action_ledger is not None:
            summary["action_ledger"] = self.action_ledger.to_list()
        # §4.4 optional instrumentation: persist orchestrator result + envstate block
        if getattr(self, "enable_supervisor", False) or getattr(self, "enable_fullstate_worker", False):
            orch_result = getattr(self, "_orchestrator_result", None)
            if orch_result:
                summary["orchestrator"] = {
                    "tasks_completed": orch_result.get("tasks_completed"),
                    "stop_reason": orch_result.get("stop_reason"),
                    "final_revision": orch_result.get("final_revision"),
                }
            snap = getattr(self, "env_snapshot", None)
            if snap is not None:
                summary["envstate"] = {
                    "final_revision": snap.revision,
                    "n_requirements": len(snap.requirements),
                    "n_open_failures": len(snap.open_failures),
                }
        if getattr(self, "run_summary_cleanroom", None) is not None:
            summary["cleanroom"] = self.run_summary_cleanroom
        return summary

    def _write_run_summary(self, configuration_success, run_error=None):
        """Persist structured run metadata so the adapter does not need to parse markdown logs."""
        summary = self._build_run_summary(configuration_success, run_error=run_error)
        try:
            with open(self.run_summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            print(f"[DockerAgent] Run summary saved to: {self.run_summary_path}")
        except Exception as e:
            print(f"[DockerAgent] Warning: Could not write run summary: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM-based Docker Environment Configuration Agent")
    parser.add_argument("repo_url", help="GitHub repository URL to configure")
    parser.add_argument("--image", default="auto", help="Base Docker image (default: auto-detect, or specify like 'python:3.10', 'node:18')")
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL, help=f"LLM model to use (default: {DEFAULT_LLM_MODEL})")
    parser.add_argument("--steps", type=int, default=30, help="Maximum number of steps (default: 30)")
    parser.add_argument(
        "--base-commit",
        default=None,
        help="Optional git commit or abbreviated SHA to checkout after cloning the repository.",
    )
    parser.add_argument(
        "--workplace",
        default="workplace",
        help="Directory used to clone the repository and store run artifacts.",
    )
    parser.add_argument("--keep-container", action="store_true", help="Keep container running after completion for inspection")
    parser.add_argument(
        "--enable-observation-compression",
        action="store_true",
        help="Enable AgentDiet-style observation compression (default: disabled)",
    )
    parser.add_argument(
        "--enable-long-term-memory",
        action="store_true",
        help="Enable failure-triggered long-term memory retrieval and post-run memory writing (default: disabled)",
    )
    parser.add_argument(
        "--memory-path",
        default=None,
        help="Path to the JSONL long-term memory store (default: memory/long_term_memories.jsonl)",
    )
    parser.add_argument(
        "--memory-embedding-model",
        default=DEFAULT_MEMORY_EMBEDDING_MODEL,
        help=f"Embedding model for long-term memory (default: {DEFAULT_MEMORY_EMBEDDING_MODEL})",
    )
    parser.add_argument(
        "--command-timeout",
        type=int,
        default=1800,
        help="Per-command timeout inside the sandbox in seconds. Defaults to 1800.",
    )
    parser.add_argument("--enable-envstate", action="store_true",
                        help="Maintain a host EnvState world model + ActionLedger (shadow mode).")
    parser.add_argument("--enable-supervisor", action="store_true",
                        help="Use the EnvState Supervisor/Worker orchestrator instead of the legacy ReAct loop.")
    parser.add_argument("--enable-fullstate-worker", action="store_true",
                        help="ARM A: single planner-less ReAct worker ingesting the full certified "
                             "EnvState snapshot each step, layered root-cause analysis. Shared global "
                             "action cap = --steps * 6. Mutually exclusive with --enable-supervisor.")
    parser.add_argument("--fullstate-worker-prompt", action="store_true",
                        help="ARM C: with --enable-supervisor, swap the Worker to the fullstate "
                             "RCA prompt + full-snapshot context (isolates the planner vs Arm A).")
    parser.add_argument("--enable-cleanroom", action="store_true",
                        help="After synthesis, rebuild the Dockerfile in a clean room and re-run probes + tests.")
    parser.add_argument("--enable-v1", action="store_true",
                        help="Use the v1 three-role orchestrator (Planner/BuildAgent/Maintainer). "
                             "Mutually exclusive with --enable-supervisor and --enable-fullstate-worker.")
    parser.add_argument(
        "--disable-post-synthesis-repair",
        action="store_true",
        help="Disable the post-synthesis clean-room self-verify + recipe-repair phase "
             "(on by default). Use for a raw-agent baseline with no self-repair.",
    )
    parser.add_argument(
        "--self-verify-max-rounds",
        type=int,
        default=2,
        help="Max self-verify repair rounds (deterministic + LLM). Defaults to 2.",
    )

    args = parser.parse_args()

    # Mutual-exclusion guards (§3.1)
    if args.enable_supervisor and args.enable_fullstate_worker:
        parser.error(
            "--enable-supervisor and --enable-fullstate-worker are different arms; "
            "pick one. Mutually exclusive: --enable-supervisor runs Arm B/C; "
            "--enable-fullstate-worker runs Arm A."
        )
    if args.fullstate_worker_prompt and not args.enable_supervisor:
        parser.error(
            "--fullstate-worker-prompt requires --enable-supervisor (Arm C)."
        )
    if args.enable_v1 and (args.enable_supervisor or args.enable_fullstate_worker):
        parser.error(
            "--enable-v1 is mutually exclusive with --enable-supervisor and "
            "--enable-fullstate-worker. Use --arm v1 for the v1 preset."
        )

    agent = DockerAgent(
        args.repo_url,
        base_image=args.image,
        model=args.model,
        workplace=args.workplace,
        base_commit=args.base_commit,
        enable_observation_compression=args.enable_observation_compression,
        enable_long_term_memory=args.enable_long_term_memory,
        enable_envstate=args.enable_envstate,
        enable_supervisor=args.enable_supervisor,
        enable_fullstate_worker=args.enable_fullstate_worker,
        fullstate_worker_prompt=args.fullstate_worker_prompt,
        enable_v1=args.enable_v1,
        enable_cleanroom=args.enable_cleanroom,
        memory_path=args.memory_path,
        memory_embedding_model=args.memory_embedding_model,
        command_timeout_seconds=args.command_timeout,
        enable_post_synthesis_repair=not args.disable_post_synthesis_repair,
        self_verify_max_rounds=args.self_verify_max_rounds,
    )
    agent.run(max_steps=args.steps, keep_container=args.keep_container)
