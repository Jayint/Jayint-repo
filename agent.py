import os
import re
import json
import argparse
import subprocess
import shutil
from openai import OpenAI
from src.sandbox import Sandbox
from src.planner import Planner
from src.synthesizer import Synthesizer
from src.image_selector import ImageSelector
from src.observation_compressor import (
    AgentStep,
    ObservationCompressor,
    RunTokenLedger,
    build_observation_metadata,
    should_apply_compression,
)
from dotenv import load_dotenv

# Load environment variables (OPENAI_API_KEY, etc.)
# override=True ensures .env values take precedence over system env vars
load_dotenv(override=True)

class DockerAgent:
    def __init__(
        self,
        repo_url,
        base_image="auto",
        model="qwen3-max-2026-01-23",
        workplace="workplace",
        base_commit=None,
        enable_observation_compression=False,
    ):
        self.repo_url = repo_url
        self.workplace = os.path.abspath(workplace)
        self.successful_test_commands = []
        self.verified_test_command = None
        self.verified_test_commands = []
        self.verified_runtime_preparation_commands = []
        self.test_run_attempts = []
        self.successful_actions = []
        self.verification_source = None
        self.verification_bundle = None
        self.run_summary_path = os.path.join(self.workplace, "agent_run_summary.json")
        self._environment_revision = 0
        self._current_verification_group = []
        self.enable_observation_compression = enable_observation_compression
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
        
        # 3. Initialize LLM client first (needed for image selection)
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_API_BASE")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found in environment variables.")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url if base_url else None
        )
        
        # 4. Auto-detect base image if set to "auto" or not specified
        platform_override = None
        log_dir = os.path.join(self.workplace, "image_selector_logs")
        if base_image == "auto":
            print("[DockerAgent] Analyzing repository to select optimal base image...")
            selector = ImageSelector(self.client, model)
            selected_image, language_handler, docs, platform_override = selector.select_base_image(
                repo_path=self.workplace,
                platform="linux",
                log_dir=log_dir
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
            print(f"[DockerAgent] Image selection logs saved to: {log_dir}")
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
        self.sandbox = Sandbox(
            base_image=base_image, 
            workdir="/app", 
            platform=platform_override,  # Use linux/amd64 if ARM64 issues detected
            seed_dir=self.workplace
        )
        self.platform_override = platform_override  # Expose for adapter to read
        
        # 6. Initialize Planner and Synthesizer
        # Load repository structure and relevant config files from image_selector_logs if available
        repo_structure = ""
        config_files_content = ""
        
        # Load structure.txt
        structure_file = os.path.join(log_dir, "structure.txt")
        if os.path.exists(structure_file):
            try:
                with open(structure_file, 'r') as f:
                    repo_structure = f.read()
                print(f"[DockerAgent] Loaded repository structure from: {structure_file}")
            except Exception as e:
                print(f"[DockerAgent] Warning: Could not read structure.txt: {e}")
        
        # Load relevant config files from summary.json
        summary_file = os.path.join(log_dir, "summary.json")
        if os.path.exists(summary_file):
            try:
                with open(summary_file, 'r') as f:
                    summary = json.load(f)
                relevant_files = summary.get("relevant_files", [])
                config_contents = []
                for rel_file in relevant_files:
                    file_path = os.path.join(self.workplace, rel_file)
                    if os.path.exists(file_path) and os.path.getsize(file_path) < 50000:  # Skip files > 50KB
                        try:
                            with open(file_path, 'r') as f:
                                content = f.read()
                            # Truncate very long files to first 200 lines
                            lines = content.split('\n')
                            if len(lines) > 200:
                                content = '\n'.join(lines[:200]) + f"\n... ({len(lines) - 200} more lines truncated)"
                            config_contents.append(f"=== {rel_file} ===\n{content}\n")
                        except Exception as e:
                            print(f"[DockerAgent] Warning: Could not read {rel_file}: {e}")
                if config_contents:
                    config_files_content = "\n".join(config_contents)
                    print(f"[DockerAgent] Loaded {len(config_contents)} relevant config files")
            except Exception as e:
                print(f"[DockerAgent] Warning: Could not read summary.json: {e}")
        
        # Combine structure and config files
        combined_repo_info = repo_structure
        if config_files_content:
            combined_repo_info += "\n\n=== Relevant Configuration Files ===\n\n" + config_files_content
        
        # Setup log directory for LLM calls (similar to image_selector_logs)
        setup_log_dir = os.path.join(self.workplace, "setup_logs")
        os.makedirs(setup_log_dir, exist_ok=True)
        
        self.planner = Planner(self.client, model=model, language_handler=self.language_handler, repo_structure=combined_repo_info, log_dir=setup_log_dir)
        self.synthesizer = Synthesizer(base_image=base_image)
        self.observation_compressor = None
        if self.enable_observation_compression:
            self.observation_compressor = ObservationCompressor(self.client, model=model)
            self.planner.init_managed_history(self.repo_url)
        print(f"[DockerAgent] Setup logs will be saved to: {setup_log_dir}")

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

    def _prepare_workplace(self):
        """Clones the repository to the local workplace directory."""
        if os.path.exists(self.workplace):
            print(f"Cleaning up existing workplace at {self.workplace}...")
            shutil.rmtree(self.workplace)
        
        os.makedirs(self.workplace)
        print(f"Cloning {self.repo_url} into {self.workplace}...")
        try:
            subprocess.run(
                ["git", "clone", self.repo_url, "."],
                cwd=self.workplace,
                check=True,
                capture_output=True
            )
            print("Clone successful.")
        except subprocess.CalledProcessError as e:
            print(f"Clone failed: {e.stderr.decode()}")
            raise e

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
        
        try:
            for step in range(max_steps):
                print(f"\n{'='*20} Step {step + 1} {'='*20}")
                
                # 1. Plan next step
                if self.enable_observation_compression:
                    thought, action, raw_llm_output, is_finished, usage_info = self.planner.plan(
                        repo_url=self.repo_url,
                        manage_history=False,
                    )
                else:
                    thought, action, raw_llm_output, is_finished, usage_info = self.planner.plan(
                        self.repo_url,
                        observation,
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
                    # Success must be backed by an actual effective test command observed at runtime.
                    if "Final Answer: Success" in raw_llm_output:
                        if self._finalize_verification_from_agent_report(raw_llm_output):
                            configuration_success = True
                        elif self.verified_test_command:
                            self.verification_source = "heuristic_fallback"
                            configuration_success = True
                        else:
                            print("[Warning] Agent claimed success but no effective verified test command was recorded.")
                            print("[Warning] Marking this run as FAILED to avoid producing unverifiable artifacts.")
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
                            mutates_environment=False,
                            env_revision_before=self._environment_revision,
                            env_revision_after=self._environment_revision,
                            planner_usage=usage_info,
                        )
                    continue

                print(f"\n[Action]\n{action}")
                
                # 2. Execute Action in Sandbox
                env_revision_before = self._environment_revision
                success, observation = self.sandbox.execute(action)
                
                print(f"\n[Observation]\n{observation if observation.strip() else '(No output)'}")
                
                # 3. Synthesize if successful
                mutates_environment = False
                if success:
                    self.synthesizer.record_success(action)
                    mutates_environment = self.synthesizer.command_mutates_environment(action)
                    self._record_successful_action(step + 1, action, observation)
                else:
                    print("\n[System] Command failed. Sandbox rolled back to previous state.")

                if self.enable_observation_compression:
                    self._record_agent_step(
                        step_id=step + 1,
                        thought=thought or "",
                        action=action,
                        assistant_content=raw_llm_output,
                        success=success,
                        observation=observation,
                        mutates_environment=mutates_environment,
                        env_revision_before=env_revision_before,
                        env_revision_after=self._environment_revision,
                        planner_usage=usage_info,
                    )

            # 4. Final Output - 只有配置成功才生成 Dockerfile
            if configuration_success:
                print(f"\n{'='*20} Environment Configuration Complete {'='*20}")
                # 生成 Dockerfile 到 workplace 目录
                dockerfile_path = os.path.join(self.workplace, "Dockerfile")
                self.synthesizer.generate_dockerfile(file_path=dockerfile_path)
            else:
                print(f"\n{'='*20} Environment Configuration FAILED {'='*20}")
                print("[Warning] Configuration did not complete successfully. No Dockerfile will be generated.")
            
        except Exception as e:
            run_error = str(e)
            print(f"An error occurred during execution: {e}")
        finally:
            self._write_run_summary(configuration_success, run_error)
            self.sandbox.close(keep_alive=keep_container)

    def _record_agent_step(
        self,
        step_id,
        thought,
        action,
        assistant_content,
        success,
        observation,
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
            observation_prompt=observation or "",
        )
        step.metadata = build_observation_metadata(step.observation_raw)
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

        if len(target_step.observation_raw or "") < self.compression_threshold_chars:
            return

        self.compression_stats["candidate_steps"] += 1
        start_idx = max(0, target_idx - self.compression_context_before)
        context_steps = self.agent_steps[start_idx:]

        reduced_result, record = self.observation_compressor.compress(
            target_step=target_step,
            context_steps=context_steps,
        )
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
        self._current_verification_group.append(action)
        self.verified_test_commands = list(self._current_verification_group)
        self.verified_test_command = self.verified_test_commands[-1]
        print(f"[Recorded Test Command] {action}")
        print(f"[Verification Block] {len(self.verified_test_commands)} command(s) in final candidate block.")

    def _finalize_verification_from_agent_report(self, raw_llm_output):
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

        validated_test_commands = self._validate_reported_test_commands(test_commands)
        if not validated_test_commands:
            return False
        validated_runtime_commands = self._validate_reported_runtime_preparation_commands(
            runtime_commands
        )
        dropped_runtime_commands = [
            command for command in runtime_commands if command not in validated_runtime_commands
        ]
        if dropped_runtime_commands:
            print(
                "[Verification Bundle] Dropping invalid runtime preparation commands: "
                f"{dropped_runtime_commands}"
            )

        self.verified_runtime_preparation_commands = validated_runtime_commands
        self.verified_test_commands = list(validated_test_commands)
        self.verified_test_command = self.verified_test_commands[-1]
        self.verification_source = "agent_report"
        self.verification_bundle = {
            "runtime_preparation_commands": list(validated_runtime_commands),
            "test_commands": list(validated_test_commands),
        }
        print(
            "[Verification Bundle] Accepted "
            f"{len(validated_runtime_commands)} runtime preparation command(s) and "
            f"{len(validated_test_commands)} test command(s)."
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
        if not raw_llm_output or "Verification Bundle" not in raw_llm_output:
            return None

        marker = "Verification Bundle:"
        marker_index = raw_llm_output.find(marker)
        if marker_index == -1:
            return None

        candidate = raw_llm_output[marker_index + len(marker):]
        final_answer_index = candidate.find("Final Answer:")
        if final_answer_index != -1:
            candidate = candidate[:final_answer_index]
        candidate = candidate.strip()
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
            print("[Verification Bundle] Could not locate a JSON object in the final answer.")
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

    def _validate_reported_test_commands(self, test_commands):
        matched_indices = []
        cursor = -1
        for command in test_commands:
            matched_index = self._find_successful_action_index(command, cursor + 1)
            if matched_index is None:
                print(f"[Verification Bundle] Test command was not observed as a successful action: {command}")
                return []

            entry = self.successful_actions[matched_index]
            if not self._successful_action_proves_tests(entry):
                print(
                    "[Verification Bundle] Reported test command did not produce a reliable test signal: "
                    f"{command}"
                )
                return []

            if matched_indices:
                if not self._intervening_actions_are_ignorable(matched_indices[-1], matched_index):
                    print(
                        "[Verification Bundle] Found non-ignorable successful actions between test commands; "
                        "rejecting the report."
                    )
                    return []

            matched_indices.append(matched_index)
            cursor = matched_index

        return list(test_commands)

    def _validate_reported_runtime_preparation_commands(self, runtime_commands):
        validated_commands = []
        cursor = -1

        for command in runtime_commands:
            matched_index = self._find_successful_action_index(command, cursor + 1)
            if matched_index is None:
                print(
                    "[Verification Bundle] Runtime preparation command was not observed as a "
                    f"successful action and will be ignored: {command}"
                )
                continue

            entry = self.successful_actions[matched_index]
            if not self._successful_action_is_runtime_preparation_candidate(entry):
                print(
                    "[Verification Bundle] Runtime preparation command is not a valid ephemeral runtime action "
                    f"and will be ignored: {command}"
                )
                continue

            validated_commands.append(command)
            cursor = matched_index

        return validated_commands

    def _find_successful_action_index(self, command, start_index):
        command = (command or "").strip()
        for index in range(start_index, len(self.successful_actions)):
            if self.successful_actions[index]["command"].strip() == command:
                return index
        return None

    def _intervening_actions_are_ignorable(self, start_index, end_index):
        for entry in self.successful_actions[start_index + 1:end_index]:
            if not self._is_ignorable_successful_action(entry):
                return False
        return True

    def _is_ignorable_successful_action(self, entry):
        return entry.get("is_readonly") or entry.get("is_runtime_healthcheck")

    def _successful_action_proves_tests(self, entry):
        observation = entry.get("observation") or ""
        if self.synthesizer.observation_looks_like_help_text(observation):
            return False
        if self.synthesizer.observation_has_empty_test_run_signal(observation):
            return False
        if self.synthesizer.observation_has_effective_test_signal(observation):
            return True

        analysis = entry.get("test_analysis") or {}
        return bool(analysis.get("is_effective_test_run"))

    def _successful_action_is_runtime_preparation_candidate(self, entry):
        command = entry.get("command") or ""
        if entry.get("is_readonly") or entry.get("is_runtime_healthcheck"):
            return False
        if self.synthesizer.is_persistent_setup_command(command):
            return False
        return True

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

    def _write_run_summary(self, configuration_success, run_error=None):
        """Persist structured run metadata so the adapter does not need to parse markdown logs."""
        summary = {
            "repo_url": self.repo_url,
            "configuration_success": configuration_success,
            "verified_test_command": self.verified_test_command,
            "verified_test_commands": self.verified_test_commands,
            "verified_runtime_preparation_commands": self.verified_runtime_preparation_commands,
            "successful_test_commands": self.successful_test_commands,
            "test_run_attempts": self.test_run_attempts,
            "verification_source": self.verification_source,
            "verification_bundle": self.verification_bundle,
            "observation_compression_enabled": self.enable_observation_compression,
            "compression_stats": self.compression_stats,
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
                "total": self.run_token_ledger.total.__dict__,
            },
            "error": run_error,
        }
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
    parser.add_argument("--model", default="qwen3-max-2026-01-23", help="LLM model to use (default: qwen3-max-2026-01-23)")
    parser.add_argument("--steps", type=int, default=30, help="Maximum number of steps (default: 30)")
    parser.add_argument("--keep-container", action="store_true", help="Keep container running after completion for inspection")
    parser.add_argument(
        "--enable-observation-compression",
        action="store_true",
        help="Enable AgentDiet-style observation compression (default: disabled)",
    )
    
    args = parser.parse_args()
    
    agent = DockerAgent(
        args.repo_url,
        base_image=args.image,
        model=args.model,
        enable_observation_compression=args.enable_observation_compression,
    )
    agent.run(max_steps=args.steps, keep_container=args.keep_container)
