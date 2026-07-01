import os
import re
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
from src.dockerfile_repair import repair_generated_dockerfile
from src.planning import EnvironmentPlanningAgent
from src.verification_bundle import derive_supported_verification_bundle
from src.constants import DEFAULT_LLM_MODEL, DEFAULT_MEMORY_EMBEDDING_MODEL
from src.evaluation_target import (
    RATBENCH_TARGET,
    coerce_benchmark_target,
    is_ratbench_target,
    normalize_evaluation_target,
)
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
PLAN_AUTO_DIGEST_INTERVAL_STEPS = 3
PLAN_CONSULTATION_TRIGGER_LOG_LIMIT = 200

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
        memory_path=None,
        memory_embedding_model=DEFAULT_MEMORY_EMBEDDING_MODEL,
        command_timeout_seconds=1800,
        enable_dockerfile_repair=False,
        dockerfile_repair_rounds=1,
    ):
        self.repo_url = repo_url
        self.model = model
        self.base_commit = base_commit
        self.problem_statement = problem_statement or ""
        self.test_patch = test_patch or ""
        self.evaluation_target = normalize_evaluation_target(benchmark_evaluation_target)
        self.benchmark_evaluation_target = coerce_benchmark_target(benchmark_evaluation_target)
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
        self.environment_build_plan = None
        self.environment_build_plan_obj = None
        self.environment_planner = None
        self.environment_plan_context = ""
        self.environment_plan_host_json = None
        self.environment_plan_host_md = None
        self.environment_plan_container_json = "/tmp/repo2run_environment_plan.json"
        self.environment_plan_container_md = "/tmp/repo2run_environment_plan.md"
        self.planning_execution_state = {
            "completed_node_ids": [],
            "failed_node_ids": [],
            "node_attempts": {},
            "last_feedback": None,
        }
        self.plan_auto_digest_interval_steps = PLAN_AUTO_DIGEST_INTERVAL_STEPS
        self.planning_consultation_stats = self._default_planning_consultation_stats()
        self.planning_warnings = []
        self.planning_source = None
        self.run_summary_path = os.path.join(self.workplace, "agent_run_summary.json")
        self._environment_revision = 0
        self._current_verification_group = []
        self.enable_observation_compression = enable_observation_compression
        self.enable_long_term_memory = enable_long_term_memory
        self.memory_path = memory_path
        self.memory_embedding_model = memory_embedding_model
        self.command_timeout_seconds = command_timeout_seconds
        self.enable_dockerfile_repair = bool(enable_dockerfile_repair)
        self.dockerfile_repair_rounds = max(0, int(dockerfile_repair_rounds or 0))
        self.dockerfile_repair_report = {"enabled": self.enable_dockerfile_repair}
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
        api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("MINIMAX_API_BASE") or os.getenv("OPENAI_API_BASE")
        if not api_key:
            raise ValueError("MINIMAX_API_KEY or OPENAI_API_KEY not found in environment variables.")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None
        )

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
        
        # 4. Build an initial read-only environment plan. The former ImageSelector
        # role is now handled inside EnvironmentPlanningAgent as BaseImagePlanner.
        platform_override = None
        self.logs_dir = os.path.join(self.workplace, "logs")
        planning_log_dir = os.path.join(self.logs_dir, "planning_logs")
        self.planning_log_dir = planning_log_dir
        print("[DockerAgent] Building initial environment plan...")
        environment_planner = EnvironmentPlanningAgent(
            self.client,
            model,
            evaluation_target=self.evaluation_target,
        )
        self.environment_planner = environment_planner
        base_image_override = None if base_image == "auto" else base_image
        environment_plan = environment_planner.create_initial_plan(
            repo_path=self.workplace,
            platform="linux",
            language_hint=self.language or None,
            base_image_override=base_image_override,
            log_dir=planning_log_dir,
        )
        self.language_handler = environment_planner.language_handler
        evidence = environment_planner.repository_evidence
        self.repo_docs = evidence.docs if evidence else ""
        planning_usage = environment_planner.get_token_usage()
        self.run_token_ledger.add(
            "image_selector",
            input_tokens=planning_usage["input_tokens"],
            output_tokens=planning_usage["output_tokens"],
        )

        # 5. Auto-detect base image if set to "auto" or use the explicit override.
        if base_image == "auto":
            base_image = (
                environment_plan.repo_summary.get("recommended_base_image")
                or "python:3.11"
            )
            platform_override = environment_plan.repo_summary.get("platform_override")
            print(f"[DockerAgent] Selected base image: {base_image}")
            if platform_override:
                print(f"[DockerAgent] Platform override: {platform_override} (for ARM64 compatibility)")
            print(f"[DockerAgent] Planning logs saved to: {planning_log_dir}")
        else:
            platform_override = environment_plan.repo_summary.get("platform_override")
            print(f"[DockerAgent] Using user-specified base image: {base_image}")
            if platform_override:
                print(f"[DockerAgent] Platform override: {platform_override} (from planning)")
        
        # 6. Setup Sandbox with a copied workspace so rollback restores repo state too.
        self.sandbox = self._create_sandbox(
            base_image=base_image,
            platform_override=platform_override,
        )
        self.platform_override = platform_override  # Expose for adapter to read

        # 7. Refine the initial plan with read-only sandbox probes. This gives
        # the setup agent concrete environment facts before it starts mutating
        # the sandbox.
        print("[DockerAgent] Refining environment plan with sandbox exploration...")
        try:
            environment_plan = environment_planner.refine_plan_with_sandbox_exploration(
                environment_plan,
                self.sandbox,
                log_dir=planning_log_dir,
            )
            print("[DockerAgent] Sandbox planning exploration completed.")
        except Exception as exc:
            environment_plan.validator_warnings.append(
                f"Sandbox planning exploration failed: {exc}"
            )
            print(f"[DockerAgent] Warning: sandbox planning exploration failed: {exc}")

        self._initialize_planning_execution_state(environment_plan)
        self._refresh_environment_plan(
            environment_plan,
            reason="initial sandbox-refined plan",
            step_index=0,
        )
        
        # 8. Initialize Planner and Synthesizer
        # Load only repository structure from planning_logs. Configuration
        # files should be inspected explicitly by the agent when needed.
        repo_structure = ""
        
        # Load structure.txt
        structure_file = os.path.join(planning_log_dir, "structure.txt")
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
            environment_plan_context=self.environment_plan_context,
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

    def run(self, max_steps=30, keep_container=False):
        """Runs the ReAct loop to configure the environment."""
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
                is_plan_view_action = self._is_plan_view_action(action)
                execution_action = action
                action_rewrite_note = ""
                if not (is_rollback_action or is_memory_retrieval_action or is_plan_view_action):
                    execution_action, action_rewrite_note = self._prepare_action_for_execution(action)
                    if execution_action != action:
                        print(
                            "\n[System] Removed lossy output filtering before execution.\n"
                            f"[Executed Action]\n{execution_action}"
                        )
                planning_feedback = ""
                if is_plan_view_action:
                    print("\n[System] Agent requested the current environment plan.")
                    success, observation = True, self._view_environment_plan_observation()
                elif is_memory_retrieval_action:
                    print("\n[System] Agent requested long-term memory retrieval for the last failure.")
                    success, observation = True, self._retrieve_long_term_memory_observation()
                elif is_rollback_action:
                    print("\n[System] Agent requested an explicit rollback to the last successful snapshot.")
                    success, observation = self.sandbox.rollback(reason="agent_requested")
                    if success:
                        self._sync_environment_plan_files_to_sandbox()
                else:
                    success, observation = self.sandbox.execute(execution_action)
                    if action_rewrite_note:
                        observation = self._append_system_note(observation, action_rewrite_note)
                prompt_observation = self._prepare_observation_for_prompt(observation)
                
                print(f"\n[Observation]\n{observation if observation.strip() else '(No output)'}")
                
                # 3. Synthesize if successful
                mutates_environment = False
                accepted_observation_final = False
                if (
                    success
                    and not is_rollback_action
                    and not is_memory_retrieval_action
                    and not is_plan_view_action
                ):
                    self.synthesizer.record_success(execution_action)
                    mutates_environment = self.synthesizer.command_mutates_environment(execution_action)
                    self._record_successful_action(step + 1, execution_action, observation)
                    planning_feedback = self._update_environment_plan_after_execution(
                        step_index=step + 1,
                        action=execution_action,
                        observation=observation,
                        success=True,
                    )
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
                            self._record_failed_action(step + 1, execution_action, prompt_observation)
                            self._remember_failure_for_memory(execution_action, prompt_observation)
                            planning_feedback = self._update_environment_plan_after_execution(
                                step_index=step + 1,
                                action=execution_action,
                                observation=prompt_observation,
                                success=False,
                            )
                            prompt_observation = self._append_long_term_memory_hint(prompt_observation)
                if planning_feedback:
                    prompt_observation = self._append_system_note(
                        prompt_observation,
                        planning_feedback,
                    )
                if not (is_rollback_action or is_memory_retrieval_action or is_plan_view_action):
                    automatic_plan_digest = self._maybe_auto_environment_plan_digest(
                        step_index=step + 1,
                        action=execution_action,
                        success=success,
                        planning_feedback_emitted=bool(planning_feedback),
                    )
                    if automatic_plan_digest:
                        prompt_observation = self._append_system_note(
                            prompt_observation,
                            automatic_plan_digest,
                        )

                if self.enable_observation_compression:
                    self._record_agent_step(
                        step_id=step + 1,
                        thought=thought or "",
                        action=execution_action,
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
                    if self._generate_final_dockerfile(dockerfile_path):
                        self._maybe_generate_long_term_memories(configuration_success)
                    else:
                        configuration_success = False
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
                        if self._generate_final_dockerfile(dockerfile_path):
                            self._maybe_generate_long_term_memories(configuration_success)
                        else:
                            configuration_success = False
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
            evaluation_target=self.evaluation_target,
        )

    def _is_explicit_rollback_action(self, action):
        normalized = (action or "").strip()
        return normalized in {"__ROLLBACK__", "__ROLLBACK_TO_LAST_SUCCESS__"}

    def _is_memory_retrieval_action(self, action):
        return (action or "").strip() == "__RETRIEVE_MEMORY__"

    def _is_plan_view_action(self, action):
        return (action or "").strip() == "__VIEW_PLAN__"

    def _prepare_action_for_execution(self, action):
        """Remove lossy trailing output filters when sandbox would reject the command."""
        action = action or ""
        should_rewrite = self._action_has_rejected_output_filter(action)
        if not should_rewrite:
            return action, ""

        rewritten = self._strip_trailing_output_filters(action)
        if not rewritten or rewritten == action:
            return action, ""

        note = (
            "[SYSTEM] The model requested a setup/test/probe command with a lossy "
            "output filter. The host removed the trailing filter before execution so "
            "the step is not wasted and the full exit status/output remains available.\n"
            f"[SYSTEM] Requested Action: `{action}`\n"
            f"[SYSTEM] Executed Action: `{rewritten}`"
        )
        return rewritten, note

    def _action_has_rejected_output_filter(self, action):
        sandbox = getattr(self, "sandbox", None)
        checker = getattr(sandbox, "_command_pipes_setup_or_test_through_output_filter", None)
        if callable(checker):
            try:
                return bool(checker(action or ""))
            except Exception:
                pass
        return bool(
            re.search(
                r"\|\s*(?:head|tail|grep)\b",
                action or "",
                flags=re.IGNORECASE,
            )
        )

    def _strip_trailing_output_filters(self, action):
        command = (action or "").strip()
        if not command:
            return command

        previous = None
        while previous != command:
            previous = command
            command = re.sub(
                r"\s*(?:2>\s*&\s*1\s*)?\|\s*(?:head|tail)\b"
                r"(?:\s+-n\s*\d+|\s+-\d+|\s+\d+)?\s*$",
                "",
                command,
                flags=re.IGNORECASE,
            ).strip()
            command = re.sub(
                r"\s*(?:2>\s*&\s*1\s*)?\|\s*grep\b(?:\s+[^|;&]+)?\s*$",
                "",
                command,
                flags=re.IGNORECASE,
            ).strip()

        return command

    def _initialize_planning_execution_state(self, plan):
        completed = []
        for node in getattr(plan, "nodes", []) or []:
            if node.type == "runtime":
                completed.append(node.id)
        self.planning_execution_state = {
            "completed_node_ids": sorted(set(completed)),
            "failed_node_ids": [],
            "node_attempts": {},
            "last_feedback": None,
        }

    def _default_planning_consultation_stats(self):
        return {
            "explicit_view_requests": 0,
            "automatic_plan_digests": 0,
            "planning_update_feedback": 0,
            "auto_digest_interval_steps": getattr(
                self,
                "plan_auto_digest_interval_steps",
                PLAN_AUTO_DIGEST_INTERVAL_STEPS,
            ),
            "last_consultation_step": None,
            "triggers": [],
        }

    def _ensure_planning_consultation_stats(self):
        stats = getattr(self, "planning_consultation_stats", None)
        if not isinstance(stats, dict):
            stats = self._default_planning_consultation_stats()
            self.planning_consultation_stats = stats
        defaults = self._default_planning_consultation_stats()
        for key, value in defaults.items():
            stats.setdefault(key, value if not isinstance(value, list) else [])
        stats["auto_digest_interval_steps"] = getattr(
            self,
            "plan_auto_digest_interval_steps",
            PLAN_AUTO_DIGEST_INTERVAL_STEPS,
        )
        return stats

    def _record_plan_consultation(self, kind, trigger, step_index=None):
        stats = self._ensure_planning_consultation_stats()
        if kind == "explicit_view":
            stats["explicit_view_requests"] += 1
        elif kind == "automatic_digest":
            stats["automatic_plan_digests"] += 1
        elif kind == "planning_update_feedback":
            stats["planning_update_feedback"] += 1
        stats["last_consultation_step"] = step_index
        triggers = stats.setdefault("triggers", [])
        triggers.append(
            {
                "kind": kind,
                "trigger": trigger,
                "step_index": step_index,
            }
        )
        if len(triggers) > PLAN_CONSULTATION_TRIGGER_LOG_LIMIT:
            del triggers[: len(triggers) - PLAN_CONSULTATION_TRIGGER_LOG_LIMIT]

    def _refresh_environment_plan(self, plan, reason, step_index=None):
        self.environment_build_plan_obj = plan
        self.environment_build_plan = plan.to_dict() if plan else None
        if self.environment_planner and plan:
            self.environment_plan_context = self.environment_planner.format_initial_plan(plan)
        else:
            self.environment_plan_context = ""
        self.planning_warnings = list(getattr(plan, "validator_warnings", []) or [])
        self.planning_source = getattr(plan, "plan_source", None)
        self._write_environment_plan_files(reason=reason, step_index=step_index)

    def _write_environment_plan_files(self, reason, step_index=None):
        if not self.environment_build_plan_obj:
            return

        planning_dir = os.path.join(getattr(self, "logs_dir", self.workplace), "planning")
        os.makedirs(planning_dir, exist_ok=True)
        self.environment_plan_host_json = os.path.join(
            planning_dir,
            "current_environment_plan.json",
        )
        self.environment_plan_host_md = os.path.join(
            planning_dir,
            "current_environment_plan.md",
        )

        payload = self._current_environment_plan_payload(reason, step_index)
        markdown = self._format_environment_plan_markdown(payload)
        with open(self.environment_plan_host_json, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, indent=2, ensure_ascii=False)
        with open(self.environment_plan_host_md, "w", encoding="utf-8") as file_obj:
            file_obj.write(markdown)

        self._sync_environment_plan_files_to_sandbox(markdown=markdown, payload=payload)

    def _sync_environment_plan_files_to_sandbox(self, markdown=None, payload=None):
        sandbox = getattr(self, "sandbox", None)
        if not sandbox or not hasattr(sandbox, "write_text_file"):
            return

        if payload is None:
            payload = self._current_environment_plan_payload(
                reason="sync current plan to sandbox",
                step_index=None,
            )
        if markdown is None:
            markdown = self._format_environment_plan_markdown(payload)

        json_text = json.dumps(payload, indent=2, ensure_ascii=False)
        for path, content in (
            (self.environment_plan_container_json, json_text),
            (self.environment_plan_container_md, markdown),
        ):
            success, output = sandbox.write_text_file(path, content)
            if not success:
                print(f"[Planning File] Warning: {output}")

    def _current_environment_plan_payload(self, reason, step_index=None):
        next_todo = self._next_plan_todo_item()
        state = dict(self.planning_execution_state or {})
        state["next_todo"] = next_todo
        state["plan_files"] = {
            "host_json": self.environment_plan_host_json,
            "host_markdown": self.environment_plan_host_md,
            "container_json": self.environment_plan_container_json,
            "container_markdown": self.environment_plan_container_md,
        }
        return {
            "plan_file_version": 1,
            "updated_reason": reason,
            "updated_after_step": step_index,
            "execution_state": state,
            "environment_build_plan": self.environment_build_plan_obj.to_dict(),
        }

    def _format_environment_plan_markdown(self, payload):
        plan = payload.get("environment_build_plan", {})
        summary = plan.get("repo_summary", {}) or {}
        state = payload.get("execution_state", {}) or {}
        completed = set(state.get("completed_node_ids") or [])
        failed = set(state.get("failed_node_ids") or [])
        next_todo = state.get("next_todo")

        lines = [
            "# Repo2Run Environment Plan",
            "",
            f"- Updated reason: {payload.get('updated_reason')}",
            f"- Updated after step: {payload.get('updated_after_step')}",
            f"- Plan source: {plan.get('plan_source')}",
            f"- Primary language: {summary.get('primary_language') or 'unknown'}",
            f"- Package manager: {summary.get('package_manager') or 'unknown'}",
            f"- Test framework: {summary.get('test_framework') or 'unknown'}",
            f"- Recommended base image: {summary.get('recommended_base_image') or 'unknown'}",
            f"- Target platform: {summary.get('target_platform') or 'unknown'}",
            "",
            "## How To Use This Plan",
            "",
            "- This file is advisory planning state, not execution evidence.",
            "- Command hints do not count for Dockerfile replay or Verification Bundle until executed successfully.",
            "- After completing a setup step, check the next todo before starting an unrelated branch.",
            f"- Container markdown path: `{self.environment_plan_container_md}`",
            f"- Container JSON path: `{self.environment_plan_container_json}`",
            "- You can request the current plan with `Action: __VIEW_PLAN__`.",
            "",
            "## Current Todo",
            "",
        ]
        summary_insert_index = lines.index("## How To Use This Plan") - 1
        host_environment = summary.get("planning_host_environment") or {}
        if host_environment:
            lines.insert(
                summary_insert_index,
                "- Planning host environment: "
                f"{host_environment.get('normalized_os') or host_environment.get('os_name')}/"
                f"{host_environment.get('normalized_arch') or host_environment.get('machine')}",
            )
            summary_insert_index += 1
        sandbox_runtime = summary.get("sandbox_runtime_environment") or {}
        if sandbox_runtime:
            lines.insert(
                summary_insert_index,
                "- Sandbox runtime environment: "
                f"{sandbox_runtime.get('platform') or 'unknown'}",
            )
        if next_todo:
            lines.extend(self._format_plan_todo_item(next_todo, prefix="- NEXT: "))
        else:
            lines.append("- NEXT: no remaining planned todo item.")

        lines.extend(["", "## Ordered Todo List", ""])
        for item in plan.get("ordered_todo_list", []) or []:
            node_id = item.get("node_id")
            if node_id in completed:
                marker = "[x]"
            elif node_id in failed:
                marker = "[!]"
            else:
                marker = "[ ]"
            lines.extend(self._format_plan_todo_item(item, prefix=f"- {marker} "))

        risk_notes = plan.get("risk_notes", []) or []
        if risk_notes:
            lines.extend(["", "## Risk Notes", ""])
            lines.extend(f"- {note}" for note in risk_notes[:12])

        fallback_plan = plan.get("fallback_plan", []) or []
        if fallback_plan:
            lines.extend(["", "## Fallback Plan", ""])
            for item in fallback_plan[:12]:
                lines.append(
                    "- Trigger: "
                    f"{item.get('trigger')} | Suggested action: {item.get('suggested_action')}"
                )

        warnings = plan.get("validator_warnings", []) or []
        if warnings:
            lines.extend(["", "## Validator Warnings", ""])
            lines.extend(f"- {warning}" for warning in warnings[:12])

        return "\n".join(lines).rstrip() + "\n"

    def _format_plan_todo_item(self, item, prefix="- "):
        command_hint = item.get("command_hint")
        evidence = ", ".join(str(value) for value in (item.get("evidence") or [])[:3])
        lines = [
            (
                f"{prefix}{item.get('step')}. [{item.get('task_type')}] "
                f"{item.get('node_id')}"
            )
        ]
        if command_hint:
            lines.append(f"  - Command hint: `{command_hint}`")
        if evidence:
            lines.append(f"  - Evidence: {evidence}")
        if item.get("description"):
            lines.append(f"  - Description: {item.get('description')}")
        return lines

    def _view_environment_plan_observation(self):
        if not self.environment_build_plan_obj:
            return "[SYSTEM] No Planning Agent plan is available yet."
        self._record_plan_consultation(
            "explicit_view",
            trigger="agent requested __VIEW_PLAN__",
            step_index=None,
        )
        self._write_environment_plan_files(
            reason="agent requested current plan view",
            step_index=None,
        )
        if self.environment_plan_host_md and os.path.exists(self.environment_plan_host_md):
            with open(self.environment_plan_host_md, "r", encoding="utf-8") as file_obj:
                markdown = file_obj.read()
        else:
            markdown = self._format_environment_plan_markdown(
                self._current_environment_plan_payload(
                    reason="agent requested current plan view",
                    step_index=None,
                )
            )
        return (
            "[SYSTEM] Current Planning Agent state follows. Use the NEXT todo to choose "
            "the next setup action, unless the latest real Observation proves the plan is wrong.\n\n"
            f"{self._truncate_for_recipe(markdown, 16000)}"
        )

    def _maybe_auto_environment_plan_digest(
        self,
        step_index,
        action,
        success,
        planning_feedback_emitted=False,
    ):
        if not self.environment_build_plan_obj:
            return ""

        triggers = []
        matched_node_id = self._match_plan_node_for_action(action)
        if success and matched_node_id and not planning_feedback_emitted:
            triggers.append("successful planned step")
        elif not success:
            triggers.append("failed setup step")

        interval = getattr(
            self,
            "plan_auto_digest_interval_steps",
            PLAN_AUTO_DIGEST_INTERVAL_STEPS,
        )
        if interval and step_index and step_index % interval == 0:
            triggers.append(f"periodic {interval}-step check")

        if not triggers:
            return ""

        trigger = "; ".join(triggers)
        self._record_plan_consultation(
            "automatic_digest",
            trigger=trigger,
            step_index=step_index,
        )
        self._write_environment_plan_files(
            reason=f"automatic plan digest after step {step_index}: {trigger}",
            step_index=step_index,
        )
        return self._format_environment_plan_digest(
            trigger=trigger,
            step_index=step_index,
            matched_node_id=matched_node_id,
        )

    def _format_environment_plan_digest(self, trigger, step_index, matched_node_id=None):
        state = self.planning_execution_state or {}
        completed = list(state.get("completed_node_ids") or [])
        failed = list(state.get("failed_node_ids") or [])
        next_todo = self._next_plan_todo_item()
        next_text = "no remaining planned todo item"
        if next_todo:
            next_text = (
                f"{next_todo.get('step')}. [{next_todo.get('task_type')}] "
                f"{next_todo.get('node_id')}"
            )
            if next_todo.get("command_hint"):
                next_text += f" | hint: {next_todo.get('command_hint')}"

        lines = [
            "[Automatic Plan Digest]",
            f"- Trigger: {trigger} after step {step_index}.",
            f"- Last matched planned node: {matched_node_id or 'none'}.",
            f"- Next todo: {next_text}.",
            f"- Completed planned nodes: {len(completed)}{self._format_plan_node_preview(completed)}.",
        ]
        if failed:
            lines.append(
                f"- Failed planned nodes: {len(failed)}{self._format_plan_node_preview(failed)}."
            )
        lines.extend(
            [
                f"- Full plan: `{self.environment_plan_container_md}` (`Action: __VIEW_PLAN__`).",
                "- Next action should advance the NEXT todo, or explicitly request `__VIEW_PLAN__` before switching strategy.",
            ]
        )
        return "\n".join(lines)

    def _format_plan_node_preview(self, node_ids, limit=4):
        if not node_ids:
            return ""
        preview = ", ".join(str(node_id) for node_id in node_ids[:limit])
        if len(node_ids) > limit:
            preview += f", +{len(node_ids) - limit} more"
        return f" ({preview})"

    def _update_environment_plan_after_execution(
        self,
        step_index,
        action,
        observation,
        success,
    ):
        if not self.environment_build_plan_obj:
            return ""

        matched_node_id = self._match_plan_node_for_action(action)
        previous_next = self._next_plan_todo_item()
        previous_next_id = previous_next.get("node_id") if previous_next else None
        changed = False
        if matched_node_id:
            changed = self._record_plan_node_attempt(
                node_id=matched_node_id,
                step_index=step_index,
                action=action,
                success=success,
            ) or changed

        updated_plan = self.environment_build_plan_obj
        if not success and self.environment_planner:
            updated_plan = self.environment_planner.update_plan_from_execution_feedback(
                self.environment_build_plan_obj,
                failed_action=action or "",
                observation=observation or "",
            )
            if updated_plan is not self.environment_build_plan_obj:
                changed = True

        if changed:
            reason = (
                f"execution feedback after step {step_index}: "
                f"{'success' if success else 'failure'}"
            )
            self._refresh_environment_plan(updated_plan, reason=reason, step_index=step_index)
        else:
            self._write_environment_plan_files(
                reason=f"execution feedback observed after step {step_index}",
                step_index=step_index,
            )

        next_todo = self._next_plan_todo_item()
        next_id = next_todo.get("node_id") if next_todo else None
        should_emit_feedback = (
            changed
            or (success and matched_node_id and matched_node_id == previous_next_id)
            or (previous_next_id != next_id)
        )
        if not should_emit_feedback:
            return ""

        self._record_plan_consultation(
            "planning_update_feedback",
            trigger="plan state changed after execution feedback",
            step_index=step_index,
        )
        next_text = "no remaining planned todo item"
        if next_todo:
            next_text = (
                f"{next_todo.get('step')}. [{next_todo.get('task_type')}] "
                f"{next_todo.get('node_id')}"
            )
            if next_todo.get("command_hint"):
                next_text += f" | hint: {next_todo.get('command_hint')}"

        status = "completed" if success else "failed"
        matched_text = matched_node_id or "no exact planned node matched"
        return (
            "[Planning Update]\n"
            f"- Step {step_index} {status}; matched planned node: {matched_text}.\n"
            f"- Next todo: {next_text}.\n"
            f"- Full plan: `{self.environment_plan_container_md}` (`Action: __VIEW_PLAN__`).\n"
            "- Next action should advance this NEXT todo, or request `__VIEW_PLAN__` before changing strategy."
        )

    def _record_plan_node_attempt(self, node_id, step_index, action, success):
        changed = False
        state = self.planning_execution_state
        attempts = state.setdefault("node_attempts", {})
        node_attempts = attempts.setdefault(node_id, [])
        node_attempts.append(
            {
                "step_index": step_index,
                "command": action or "",
                "success": bool(success),
            }
        )
        state["last_feedback"] = {
            "step_index": step_index,
            "node_id": node_id,
            "success": bool(success),
        }

        completed = set(state.setdefault("completed_node_ids", []))
        failed = set(state.setdefault("failed_node_ids", []))
        if success and node_id not in completed:
            completed.add(node_id)
            failed.discard(node_id)
            changed = True
        elif not success and node_id not in failed:
            failed.add(node_id)
            changed = True
        state["completed_node_ids"] = sorted(completed)
        state["failed_node_ids"] = sorted(failed)
        return changed

    def _next_plan_todo_item(self):
        if not self.environment_build_plan_obj:
            return None
        completed = set(self.planning_execution_state.get("completed_node_ids") or [])
        for item in self.environment_build_plan_obj.ordered_todo_list:
            if item.get("node_id") not in completed:
                return item
        return None

    def _match_plan_node_for_action(self, action):
        normalized_action = self._normalize_plan_action(action)
        if not normalized_action or not self.environment_build_plan_obj:
            return None

        candidates = []
        for item in self.environment_build_plan_obj.ordered_todo_list:
            candidates.append((item.get("node_id"), item.get("node_id")))
            candidates.append((item.get("node_id"), item.get("command_hint")))
        for node in self.environment_build_plan_obj.nodes:
            candidates.append((node.id, node.id))
            candidates.append((node.id, node.command_hint))

        for node_id, candidate in candidates:
            normalized_candidate = self._normalize_plan_action(candidate)
            if not node_id or not normalized_candidate:
                continue
            if normalized_action == normalized_candidate:
                return node_id
            if normalized_candidate in normalized_action:
                return node_id
            if normalized_action in normalized_candidate:
                return node_id
        return None

    def _normalize_plan_action(self, action):
        text = (action or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+2>&1\s*$", "", text)
        text = re.sub(r"^\s*cd\s+\S+\s+&&\s+", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip().lower()

    def _append_system_note(self, observation, note):
        if not note:
            return observation
        if not observation:
            return note
        return f"{observation.rstrip()}\n\n{note}"

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

    def _generate_final_dockerfile(self, dockerfile_path):
        self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
        return self._maybe_repair_generated_dockerfile(dockerfile_path)

    def _maybe_repair_generated_dockerfile(self, dockerfile_path):
        if not self.enable_dockerfile_repair:
            self.dockerfile_repair_report = {"enabled": False}
            return True

        if not self.verified_test_commands:
            self.dockerfile_repair_report = {
                "enabled": True,
                "final_success": False,
                "error": "Dockerfile repair was enabled but no verified test commands are available.",
            }
            print("[Dockerfile Repair] Skipped: no verified test commands are available.")
            return False

        artifact_dir = Path(self.logs_dir) / "dockerfile_repair_logs"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_path = artifact_dir / "dockerfile_repair_report.json"
        print(
            "[Dockerfile Repair] Validating generated Dockerfile with fresh image build "
            f"and up to {self.dockerfile_repair_rounds} repair round(s)."
        )

        try:
            full_report = repair_generated_dockerfile(
                dockerfile_path=Path(dockerfile_path),
                context_dir=Path(self.workplace),
                client=self.client,
                model=self.model,
                run_summary=self._build_run_summary(configuration_success=True, run_error=None),
                repo_url=self.repo_url,
                base_commit=self.base_commit,
                workdir=getattr(self.synthesizer, "workdir", "/app"),
                runtime_commands=list(self.verified_runtime_preparation_commands),
                test_commands=list(self.verified_test_commands),
                evaluation_target=self.evaluation_target,
                artifact_dir=artifact_dir,
                max_repair_rounds=self.dockerfile_repair_rounds,
                build_timeout_seconds=self.command_timeout_seconds,
                test_timeout_seconds=self.command_timeout_seconds,
                docker_platform=getattr(self, "platform_override", None),
            )
            with report_path.open("w", encoding="utf-8") as report_file:
                json.dump(full_report, report_file, indent=2, ensure_ascii=False)
            self.dockerfile_repair_report = self._compact_dockerfile_repair_report(
                full_report,
                report_path,
            )
        except Exception as exc:
            self.dockerfile_repair_report = {
                "enabled": True,
                "final_success": False,
                "error": str(exc),
                "report_path": str(report_path),
            }
            print(f"[Dockerfile Repair] Failed with error: {exc}")
            return False

        if self.dockerfile_repair_report.get("final_success"):
            print(
                "[Dockerfile Repair] Fresh image validation succeeded "
                f"after {self.dockerfile_repair_report.get('attempt_count', 0)} attempt(s)."
            )
            return True

        print(
            "[Dockerfile Repair] Fresh image validation failed. "
            f"Full report: {self.dockerfile_repair_report.get('report_path')}"
        )
        return False

    def _compact_dockerfile_repair_report(self, report, report_path):
        attempts = list((report or {}).get("attempts") or [])
        repair_rounds = list((report or {}).get("repair_rounds") or [])
        last_attempt = attempts[-1] if attempts else {}
        last_build = last_attempt.get("docker_build") or {}
        last_test_execution = last_attempt.get("test_execution") or {}

        return {
            "enabled": True,
            "final_success": bool((report or {}).get("final_success")),
            "error": (report or {}).get("error"),
            "report_path": str(report_path),
            "attempt_count": len(attempts),
            "repair_round_count": len(repair_rounds),
            "image_tag": (report or {}).get("image_tag"),
            "last_attempt": {
                "success": bool(last_attempt.get("success")),
                "docker_build_returncode": last_build.get("returncode"),
                "docker_build_timed_out": last_build.get("timed_out"),
                "test_all_effective": last_test_execution.get("all_test_commands_effective")
                if last_test_execution
                else None,
                "effective_test_command_count": last_test_execution.get(
                    "effective_test_command_count"
                )
                if last_test_execution
                else None,
            },
        }

    def _synthesize_final_build_recipe(self):
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
        analysis = self.synthesizer.analyze_test_run(action or "", observation or "")
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
            "test_analysis": analysis,
        })
        if self._test_command_satisfies_final_target(action or "", observation or "", analysis):
            self._record_final_verification_command(
                action or "",
                analysis,
                source="failed_command_with_executed_tests",
            )

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

    def _record_successful_action(self, step_index, action, observation):
        """Track successful actions and maintain the final contiguous verification block."""
        mutates_environment = self.synthesizer.command_mutates_environment(action)
        is_readonly = self.synthesizer.is_readonly_command(action)
        is_runtime_service = self.synthesizer.is_runtime_service_command(action)
        is_runtime_healthcheck = self.synthesizer.is_runtime_healthcheck_command(action)
        observed_test_signal = self.synthesizer.observation_has_effective_test_signal(observation)
        analysis = self.synthesizer.analyze_test_run(action, observation)

        self.successful_actions.append({
            "step_index": step_index,
            "command": action,
            "observation": observation,
            "environment_revision": self._environment_revision + (1 if mutates_environment else 0),
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
        if not self._test_command_satisfies_final_target(action, observation, analysis):
            print(
                f"[Recorded Diagnostic Test Command] {action} "
                f"(not final proof for {self.evaluation_target})."
            )
            return

        self._record_final_verification_command(action, analysis, source="successful_command")

    def _record_final_verification_command(self, action, analysis, source):
        if action in self.verified_test_commands:
            return
        self._current_verification_group.append(action)
        self.verified_test_commands = list(self._current_verification_group)
        self.verified_test_command = self.verified_test_commands[-1]
        print(f"[Recorded Test Command] {action} ({source}; {analysis['reason']})")
        print(f"[Verification Block] {len(self.verified_test_commands)} command(s) in final candidate block.")

    def _test_command_satisfies_final_target(self, action, observation, analysis):
        if not analysis.get("is_test_command"):
            return False
        if not is_ratbench_target(self.evaluation_target):
            return bool(analysis.get("is_effective_test_run"))
        if self._is_collect_only_test_command(action):
            return False
        return self.synthesizer.observation_has_effective_test_signal(observation)

    def _verified_commands_satisfy_evaluation_target(self, test_commands):
        commands = [command for command in test_commands or [] if command]
        if not commands:
            return False
        if not is_ratbench_target(self.evaluation_target):
            return True
        return any(
            self.synthesizer.is_test_command(command)
            and not self._is_collect_only_test_command(command)
            for command in commands
        )

    def _is_collect_only_test_command(self, command):
        normalized = " " + re.sub(r"\s+", " ", command or "").strip() + " "
        return " --collect-only " in normalized or " --co " in normalized

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
        if not self._verified_commands_satisfy_evaluation_target(self.verified_test_commands):
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
                "verification_bundle": {
                    "runtime_preparation_commands": list(runtime_commands),
                    "test_commands": list(test_commands),
                },
                "benchmark_evaluation_target": self.benchmark_evaluation_target,
                "verified_runtime_preparation_commands": self.verified_runtime_preparation_commands,
                "verified_test_commands": self.verified_test_commands,
                "verified_test_command": self.verified_test_command,
                "successful_actions": self.successful_actions,
                "failed_actions": self.failed_actions,
            },
            synthesizer=self.synthesizer,
            evaluation_target=self.evaluation_target,
        )
        supported_runtime_commands = list(supported_bundle.get("runtime_preparation_commands") or [])
        supported_test_commands = list(supported_bundle.get("test_commands") or [])

        if supported_runtime_commands != list(runtime_commands) or supported_test_commands != list(test_commands):
            print(
                "[Verification Bundle] Rejected agent-reported bundle because at least one command "
                "was not previously observed succeeding in the final environment."
            )
            return False
        if not self._verified_commands_satisfy_evaluation_target(supported_test_commands):
            print(
                "[Verification Bundle] Rejected agent-reported bundle because it does not "
                f"satisfy the {self.evaluation_target} final verification target."
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
        return {
            "repo_url": self.repo_url,
            "configuration_success": configuration_success,
            "base_image": getattr(getattr(self, "synthesizer", None), "base_image", None),
            "platform_override": getattr(self, "platform_override", None),
            "evaluation_target": self.evaluation_target,
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
            "environment_build_plan": getattr(self, "environment_build_plan", None),
            "environment_plan_files": {
                "host_json": getattr(self, "environment_plan_host_json", None),
                "host_markdown": getattr(self, "environment_plan_host_md", None),
                "container_json": getattr(self, "environment_plan_container_json", None),
                "container_markdown": getattr(self, "environment_plan_container_md", None),
            },
            "planning_logs_dir": getattr(self, "planning_log_dir", None),
            "planning_execution_state": getattr(self, "planning_execution_state", {}),
            "planning_consultation_stats": self._ensure_planning_consultation_stats(),
            "planning_warnings": getattr(self, "planning_warnings", []),
            "planning_source": getattr(self, "planning_source", None),
            "build_recipe": getattr(self, "build_recipe", None),
            "build_recipe_source": getattr(self, "build_recipe_source", None),
            "build_recipe_error": getattr(self, "build_recipe_error", None),
            "dockerfile_repair": getattr(
                self,
                "dockerfile_repair_report",
                {"enabled": getattr(self, "enable_dockerfile_repair", False)},
            ),
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
                "reflection": self.run_token_ledger.reflection.__dict__,
                "memory": self.run_token_ledger.memory.__dict__,
                "recipe": getattr(self.run_token_ledger, "recipe", {}).__dict__
                if hasattr(getattr(self.run_token_ledger, "recipe", {}), "__dict__")
                else getattr(self.run_token_ledger, "recipe", {}),
                "total": self.run_token_ledger.total.__dict__,
            },
            "error": run_error,
        }

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
    parser.add_argument(
        "--enable-dockerfile-repair",
        action="store_true",
        help=(
            "After generating the Dockerfile, build a fresh image, run the verified "
            "test command(s), and repair the Dockerfile on failure."
        ),
    )
    parser.add_argument(
        "--dockerfile-repair-rounds",
        type=int,
        default=1,
        help="Maximum Dockerfile repair rounds when --enable-dockerfile-repair is set. Defaults to 1.",
    )
    parser.add_argument(
        "--evaluation-target",
        choices=["repo2run", RATBENCH_TARGET],
        default="repo2run",
        help=(
            "Final verification semantics. repo2run keeps collect-only EBSR proof; "
            "ratbench targets full pytest execution/pass-rate evidence for ESSR."
        ),
    )
    
    args = parser.parse_args()
    
    agent = DockerAgent(
        args.repo_url,
        base_image=args.image,
        model=args.model,
        workplace=args.workplace,
        base_commit=args.base_commit,
        enable_observation_compression=args.enable_observation_compression,
        enable_long_term_memory=args.enable_long_term_memory,
        memory_path=args.memory_path,
        memory_embedding_model=args.memory_embedding_model,
        command_timeout_seconds=args.command_timeout,
        enable_dockerfile_repair=args.enable_dockerfile_repair,
        dockerfile_repair_rounds=args.dockerfile_repair_rounds,
        benchmark_evaluation_target={"evaluation_target": args.evaluation_target},
    )
    agent.run(max_steps=args.steps, keep_container=args.keep_container)
