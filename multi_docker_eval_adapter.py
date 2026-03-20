"""
Multi-Docker-Eval Benchmark Adapter
将 DockerAgent 适配到 Multi-Docker-Eval 评估标准

输入格式 (JSONL):
{
    "instance_id": "repo_name__issue_number",
    "repo_url": "https://github.com/user/repo.git", 
    "base_commit": "commit_hash",
    "problem_statement": "Issue description...",
    "patch": "diff content...",
    "test_patch": "test diff content...",
    "language": "python"
}

输出格式 (docker_res JSON):
{
    "instance_id": "repo_name__issue_number",
    "dockerfile": "FROM python:3.10\nRUN...",
    "test_script": "#!/bin/bash\npython -m pytest...",
    "build_success": true,
    "test_success": true,
    "logs": {...}
}
"""

import os
import json
import argparse
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from agent import DockerAgent


class MultiDockerEvalAdapter:
    """适配器：将 DockerAgent 输出转换为 Multi-Docker-Eval 评估格式"""
    
    def __init__(self, output_dir: str = "./multi_docker_eval_output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._last_test_command_source = None
        self._last_runtime_preparation_source = None
        
    def process_single_instance(self, instance: Dict[str, Any], 
                               base_image: str = "auto",
                               model: str = "gpt-4o",
                               max_steps: int = 30,
                               enable_observation_compression: bool = False) -> Dict[str, Any]:
        """
        处理单个评估实例
        
        Args:
            instance: 输入数据(包含 repo_url, problem_statement, patch 等)
            base_image: Docker 基础镜像
            model: LLM 模型
            max_steps: 最大步骤数
            
        Returns:
            docker_res 格式的结果字典
        """
        instance_id = instance.get("instance_id", "unknown")
        # Support both 'repo' and 'repo_url' field names
        repo_name = instance.get("repo", instance.get("repo_url", ""))
        if not repo_name.startswith("http"):
            repo_url = f"https://github.com/{repo_name}.git"
        else:
            repo_url = repo_name
        base_commit = instance.get("base_commit")
        problem_statement = instance.get("problem_statement", "")
        test_patch = instance.get("test_patch", "")
        language = instance.get("language", "unknown")
        
        print(f"\n{'='*60}")
        print(f"Processing instance: {instance_id}")
        print(f"Repository: {repo_url}")
        print(f"Language: {language}")
        print(f"{'='*60}\n")
        
        result = {
            "instance_id": instance_id,
            "repo_url": repo_url,
            "language": language,
            "dockerfile": None,
            "eval_script": None,  # 评估框架期望的字段名
            "build_success": False,
            "test_success": False,
            "platform": None,  # Docker platform override (e.g., linux/amd64 for ARM hosts)
                "logs": {
                    "agent_steps": [],
                    "error": None,
                    "verified_test_command": None,
                    "verified_test_commands": [],
                    "verified_runtime_preparation_commands": [],
                    "test_command_source": None,
                    "runtime_preparation_source": None,
                    "verification_source": None,
                    "skip_evaluation": False,
                    "platform_support": None,
                }
            }

        platform_support = self._assess_platform_support(instance, language)
        result["logs"]["platform_support"] = platform_support
        if not platform_support["supported"]:
            reason = platform_support["reason"]
            print(f"⚠ Skipping {instance_id}: {reason}")
            result["logs"]["error"] = reason
            result["logs"]["test_command_source"] = "unsupported_platform"
            result["logs"]["skip_evaluation"] = True
            self._save_result(instance_id, result)
            return result
        
        # 创建workplace目录（使用项目目录下的workplace，便于查看）
        workplace = os.path.join("./workplace", f"multi_docker_eval_{instance_id}")
        os.makedirs(workplace, exist_ok=True)
        
        try:
            # 1. 运行 DockerAgent 进行环境配置
            print("[Step 1/4] Running DockerAgent for environment configuration...")
            agent = DockerAgent(
                repo_url=repo_url,
                base_image=base_image or "auto",
                model=model,
                workplace=workplace,
                base_commit=base_commit,  # checkout before image selection for accurate LLM analysis
                enable_observation_compression=enable_observation_compression,
            )
            
            # base_commit 已在 DockerAgent.__init__ 中完成 checkout
            # 此处无需再次 checkout
            
            # 运行 agent 配置环境
            agent.run(max_steps=max_steps, keep_container=False)
            
            # 记录 platform override（用于后续评估框架构建镜像时使用正确平台）
            if hasattr(agent, 'platform_override') and agent.platform_override:
                result["platform"] = agent.platform_override
                print(f"[Adapter] Platform override recorded: {agent.platform_override}")
            
            # 2. 提取 Dockerfile（复用 Agent 的配置指令）
            print("\n[Step 2/4] Extracting Dockerfile...")
            dockerfile_path = Path(workplace) / "Dockerfile"
            if dockerfile_path.exists():
                original_dockerfile = dockerfile_path.read_text()
                
                # 从原始 Dockerfile 提取基础镜像和所有 RUN 指令
                # 注意：RUN 指令可能是多行的（如 RUN python3 -c "..."）
                base_image_line = None
                agent_run_instructions = []
                
                lines = original_dockerfile.split('\n')
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if line.startswith('FROM '):
                        base_image_line = line
                    elif line.startswith('RUN '):
                        # 检测是否为多行指令（引号未闭合）
                        full_instruction = line
                        # 统计引号数量（排除转义引号）
                        quote_count = line.count('"') - line.count('\\"')
                        
                        # 如果引号数量为奇数，说明引号未闭合，需要继续读取
                        while quote_count % 2 == 1 and i + 1 < len(lines):
                            i += 1
                            next_line = lines[i]
                            full_instruction += '\n' + next_line
                            quote_count += next_line.count('"') - next_line.count('\\"')
                        
                        # 将 Agent sandbox 中的工作目录替换为评估框架要求的 /testbed
                        # Agent 默认工作目录为 /app，评估框架统一使用 /testbed
                        full_instruction = full_instruction.replace('/app/', '/testbed/')
                        full_instruction = full_instruction.replace('cd /app', 'cd /testbed')
                        full_instruction = full_instruction.replace('"/app"', '"/testbed"')
                        full_instruction = full_instruction.replace("'/app'", "'/testbed'")
                        full_instruction = self._normalize_run_instruction_for_docker(full_instruction)
                        agent_run_instructions.append(full_instruction)
                    i += 1
                
                if not base_image_line:
                    print("✗ No FROM Dockerfile: missing FROM instruction")
                    result["logs"]["error"] = "Invalid Dockerfile: missing FROM instruction"
                else:
                    # 处理多行 RUN 指令：将多行 python3 -c "..." 转为 BuildKit heredoc 格式
                    # Docker BuildKit 支持: RUN <<'EOF'\npython3 - <<'PYEOF'\n...\nPYEOF\nEOF
                    processed_instructions = []
                    script_counter = [0]
                    
                    for instr in agent_run_instructions:
                        if '\n' in instr and instr.startswith('RUN '):
                            lines = instr.split('\n')
                            first_line = lines[0]
                            if 'python3 -c "' in first_line or 'python -c "' in first_line or "python3 -c '" in first_line:
                                cmd_match = re.match(r'(RUN\s+)(python3?\s+-c\s+)(["\'])(.*)', first_line)
                                if cmd_match:
                                    quote = cmd_match.group(3)    # " or '
                                    first_content = cmd_match.group(4)  # 第一行内容
                                    
                                    # 收集所有代码行（去除最后的闭合引号行）
                                    remaining_lines = lines[1:]
                                    if remaining_lines and remaining_lines[-1].strip() == quote:
                                        remaining_lines = remaining_lines[:-1]
                                    
                                    # 拼接完整脚本
                                    script_lines = []
                                    if first_content:
                                        script_lines.append(first_content)
                                    script_lines.extend(remaining_lines)
                                    script_content = '\n'.join(script_lines)
                                    
                                    # 使用 Docker BuildKit heredoc 格式（已验证可行）:
                                    # RUN <<'SH_EOF'
                                    # python3 - <<'PYTHON_EOF'
                                    # <script content>
                                    # PYTHON_EOF
                                    # SH_EOF
                                    script_counter[0] += 1
                                    processed_instr = f"RUN <<'SH_EOF_{script_counter[0]}'\npython3 - <<'PYTHON_EOF_{script_counter[0]}'\n{script_content}\nPYTHON_EOF_{script_counter[0]}\nSH_EOF_{script_counter[0]}"
                                    processed_instructions.append(processed_instr)
                                    continue
                            # 其他多行指令：用反斜杠续行
                            escaped_instr = ' \\\n'.join(lines)
                            processed_instructions.append(escaped_instr)
                        else:
                            processed_instructions.append(instr)
                    
                    # 构建正确的 Dockerfile：
                    # 1. 基础镜像
                    # 2. 安装 git
                    # 3. git clone + checkout
                    # 4. Agent 的 RUN 指令（复用已验证的配置）
                    
                    # 检测是否有多行 heredoc 指令，需要启用 BuildKit 语法
                    has_heredoc = any('<<' in instr for instr in processed_instructions)
                    syntax_directive = "# syntax=docker/dockerfile:1\n" if has_heredoc else ""
                    
                    checkout_line = (
                        f"RUN cd /testbed && git checkout {base_commit}"
                        if base_commit
                        else "# No base commit provided; using repository default branch HEAD"
                    )

                    dockerfile_content = f"""{syntax_directive}{base_image_line}
WORKDIR /testbed

# Install git for cloning
RUN apt-get update && apt-get install -y git

# Clone repository and checkout base commit
RUN git clone {repo_url} /testbed
{checkout_line}

# Agent's verified setup instructions
{chr(10).join(processed_instructions) if processed_instructions else '# No additional setup instructions from agent'}
"""
                    result["dockerfile"] = dockerfile_content
                    print(f"✓ Dockerfile generated with {len(agent_run_instructions)} agent instructions")
            else:
                print("✗ Dockerfile not found")
                result["logs"]["error"] = "Dockerfile generation failed"
                result["logs"]["skip_evaluation"] = True
            
            # 3. 生成测试脚本 & 将 test_patch 注入镜像
            print("\n[Step 3/4] Generating test script...")
            test_script, setup_scripts, dockerfile_with_patch = self._generate_test_script(
                workplace=workplace,
                language=language,
                problem_statement=problem_statement,
                test_patch=test_patch,
                dockerfile_content=result.get("dockerfile", ""),
                structured_runtime_preparation_commands=getattr(agent, "verified_runtime_preparation_commands", None),
                structured_test_command=getattr(agent, "verified_test_command", None),
                structured_test_commands=getattr(agent, "verified_test_commands", None),
            )
            result["eval_script"] = test_script
            result["setup_scripts"] = setup_scripts
            result["logs"]["verified_test_command"] = getattr(agent, "verified_test_command", None)
            result["logs"]["verified_test_commands"] = getattr(agent, "verified_test_commands", []) or []
            result["logs"]["verified_runtime_preparation_commands"] = (
                getattr(agent, "verified_runtime_preparation_commands", []) or []
            )
            result["logs"]["test_command_source"] = getattr(self, "_last_test_command_source", None)
            result["logs"]["runtime_preparation_source"] = getattr(
                self,
                "_last_runtime_preparation_source",
                None,
            )
            result["logs"]["verification_source"] = getattr(agent, "verification_source", None)
            if dockerfile_with_patch:
                result["dockerfile"] = dockerfile_with_patch
            if not result["dockerfile"] or not result["eval_script"]:
                result["logs"]["skip_evaluation"] = True
            result["build_success"] = bool(result["dockerfile"] and result["eval_script"] and not result["logs"]["skip_evaluation"])
            
            # 4. 验证测试 (可选，取决于是否需要在适配器中执行)
            print("\n[Step 4/4] Test script generated")
            print("Test validation will be performed by Multi-Docker-Eval framework")
            
            # 保存结果
            self._save_result(instance_id, result)
            
        except Exception as e:
            print(f"\n✗ Error processing instance {instance_id}: {e}")
            result["logs"]["error"] = str(e)
            
        finally:
            # 保留临时目录供查看（如需清理，取消下面注释）
            print(f"\n[Workplace Preserved] {workplace}")
            print(f"To inspect: ls -la {workplace}")
            # if os.path.exists(workplace):
            #     shutil.rmtree(workplace)
        
        return result
    
    def _select_base_image(self, language: str, default: str) -> str:
        """已废弃：镜像选择由 DockerAgent auto 模式（四步 LLM 流程）接管"""
        return "auto"
    
    def _checkout_commit(self, workplace: str, commit: str):
        """切换到指定的 git commit"""
        try:
            subprocess.run(
                ["git", "checkout", commit],
                cwd=workplace,
                check=True,
                capture_output=True
            )
            print(f"Checked out commit: {commit}")
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to checkout commit {commit}: {e.stderr.decode()}")
    
    def _parse_test_patch(self, test_patch: str, language: str = "python") -> Dict[str, Any]:
        """从 test_patch diff 中提取测试文件路径和新增的测试函数名"""
        test_files = []
        new_test_funcs = []  # (file, func_name)

        current_file = None
        for line in test_patch.splitlines():
            # 提取文件名
            m = re.match(r'^\+\+\+ b/(.*)', line)
            if m:
                current_file = m.group(1)
                if current_file not in test_files:
                    test_files.append(current_file)
            
            # 根据语言提取测试函数
            if language.lower() == "python":
                # Python: def test_xxx(
                m = re.match(r'^\+def (test_\w+)', line)
                if m and current_file:
                    new_test_funcs.append((current_file, m.group(1)))
            elif language.lower() in ("javascript", "typescript"):
                # JS/TS: test('xxx' or it('xxx' or describe('xxx'
                m = re.match(r"^\+\s*(?:test|it|describe)\(['\"]([^'\"]+)", line)
                if m and current_file:
                    new_test_funcs.append((current_file, m.group(1)))
            elif language.lower() == "go":
                # Go: func TestXxx(
                m = re.match(r'^\+func (Test\w+)\(', line)
                if m and current_file:
                    new_test_funcs.append((current_file, m.group(1)))
            elif language.lower() == "rust":
                # Rust: #[test] fn test_xxx(
                m = re.match(r'^\+fn (\w+)\(', line)
                if m and current_file:
                    new_test_funcs.append((current_file, m.group(1)))
            elif language.lower() == "java":
                # Java: @Test public void testXxx(
                m = re.match(r'^\+\s*public void (\w+)\(', line)
                if m and current_file:
                    new_test_funcs.append((current_file, m.group(1)))
            # C/C++ 没有标准测试函数格式，通常通过 Makefile 运行

        return {"test_files": test_files, "new_test_funcs": new_test_funcs}

    def _assess_platform_support(self, instance: Dict[str, Any], language: str) -> Dict[str, Any]:
        """
        Detect benchmark instances that require a non-Linux toolchain/runtime.

        The current adapter only produces Linux container specs, so these instances
        must be skipped explicitly instead of being evaluated with misleading Linux tests.
        """
        problem_statement = instance.get("problem_statement", "")
        patch = instance.get("patch", "")
        test_patch = instance.get("test_patch", "")
        evidence_blob = "\n".join([problem_statement, patch, test_patch]).lower()

        windows_patterns = {
            "visual_studio_project": [
                r"\.vcproj\b",
                r"\.vcxproj\b",
            ],
            "msvc_toolchain": [
                r"\bmsvc\b",
                r"visual c\+\+",
                r"\bmsbuild\b",
                r"\bnmake\b",
                r"\bdevenv\b",
                r"\bappveyor\b",
                r"windowsservercore",
            ],
        }
        embedded_patterns = {
            "iar_toolchain": [
                r"\.ewp\b",
                r"\bembedded workbench\b",
                r"\biar\b",
                r"\bewarm\b",
            ],
        }
        macos_patterns = {
            "xcode_toolchain": [
                r"\.xcodeproj\b",
                r"\.xcworkspace\b",
                r"\bxcodebuild\b",
                r"\bcocoapods\b",
                r"\bpod install\b",
            ],
        }

        indicators: List[str] = []
        required_platform = "linux"

        for label, patterns in windows_patterns.items():
            if any(re.search(pattern, evidence_blob) for pattern in patterns):
                indicators.append(label)
        if indicators:
            required_platform = "windows"

        if not indicators:
            for label, patterns in embedded_patterns.items():
                if any(re.search(pattern, evidence_blob) for pattern in patterns):
                    indicators.append(label)
            if indicators:
                required_platform = "embedded"

        if not indicators:
            for label, patterns in macos_patterns.items():
                if any(re.search(pattern, evidence_blob) for pattern in patterns):
                    indicators.append(label)
            if indicators:
                required_platform = "macos"

        supported = not indicators
        reason = None
        if not supported:
            reason = (
                f"This instance appears to require a {required_platform}-specific build/test path "
                f"({', '.join(indicators)}), but the current adapter only generates Linux container evaluations."
            )

        return {
            "supported": supported,
            "detected_runtime": "linux",
            "required_platform": required_platform,
            "indicators": indicators,
            "reason": reason,
        }

    def _extract_test_command_from_setup_logs(self, workplace: str) -> Optional[str]:
        """
        从 setup_logs 中提取 Agent 实际验证成功的测试命令。
        策略：在含 'Final Answer: Success' 的日志中，提取 Final Answer 之前
        最后一次出现的测试命令（Agent 最终验证通过所用的那条），而非第一次出现的。
        """
        setup_logs_dir = Path(workplace) / "setup_logs"
        if not setup_logs_dir.exists():
            return None

        # 按编号排序查找所有 setup log 文件
        log_files = sorted(setup_logs_dir.glob("*.md"), key=lambda x: int(x.stem))

        # 匹配 Action 行中整条命令（取行内 Action: 后的全部内容，再后处理判断是否为测试命令）
        # 格式：Action: <cmd>  或  Action: `<cmd>`
        action_line_pattern = re.compile(
            r'^Action:\s*`?([^\n`]+?)`?\s*$',
            re.MULTILINE
        )
        # 测试命令关键词：只要命令含这些词之一，就认为是测试命令
        test_keywords = (
            'ctest', 'pytest', 'python -m pytest', 'python3 -m pytest',
            'make test', 'make check', 'npm test', 'bundle exec rake',
            'bundle exec rspec', 'go test', 'cargo test', 'mvn test',
            'vendor/bin/phpunit', 'run_all', 'run_tests',
            '--target test',  # cmake --build build --target test
        )
        # 排除纯查看/安装类命令（不是测试命令）
        exclude_keywords = (
            'cat ', 'ls ', 'find ', 'echo ', 'apt-get', 'pip install',
            'gem install', 'npm install', 'make -j',
        )

        for log_file in reversed(log_files):  # 从最新的开始查找
            content = log_file.read_text()
            # 仅处理包含成功验证的日志
            if "Final Answer: Success" not in content and "100% tests passed" not in content:
                continue

            # 截取 Final Answer 之前的内容，避免提取到 Final Answer 后面的无关内容
            success_pos = content.find("Final Answer: Success")
            if success_pos == -1:
                success_pos = len(content)
            content_before_success = content[:success_pos]

            # 找出所有 Action 行，过滤出测试命令，取最后一个（Agent 最终使用的）
            last_test_cmd = None
            for m in action_line_pattern.finditer(content_before_success):
                cmd = m.group(1).strip()
                cmd_lower = cmd.lower()
                # 检查是否含测试关键词
                is_test = any(kw in cmd_lower for kw in test_keywords)
                # 排除明显的非测试命令
                is_excluded = any(kw in cmd_lower for kw in exclude_keywords)
                if is_test and not is_excluded:
                    last_test_cmd = cmd
            if last_test_cmd:
                cmd = last_test_cmd
                # 清理多余空格
                cmd = re.sub(r'\s+', ' ', cmd)
                # 替换 Agent sandbox 路径 /app 为评估框架路径 /testbed
                cmd = cmd.replace('/app/', '/testbed/')
                cmd = cmd.replace('cd /app', 'cd /testbed')
                print(f"  Extracted test command from setup_logs: {cmd}")
                return cmd
        return None

    def _load_run_summary(self, workplace: str) -> Optional[Dict[str, Any]]:
        """Load the structured runtime summary emitted by DockerAgent."""
        summary_file = Path(workplace) / "agent_run_summary.json"
        if not summary_file.exists():
            return None

        try:
            return json.loads(summary_file.read_text())
        except Exception as e:
            print(f"  Warning: Failed to read agent_run_summary.json: {e}")
            return None

    def _normalize_commands(self, commands: Optional[List[str]]) -> List[str]:
        """Drop empty entries while preserving order."""
        if isinstance(commands, str):
            commands = [commands]

        normalized_commands: List[str] = []
        for command in commands or []:
            if not command:
                continue
            stripped = command.strip()
            if stripped:
                normalized_commands.append(stripped)
        return normalized_commands

    def _extract_structured_runtime_preparation_commands(self, workplace: str) -> Tuple[List[str], Optional[str]]:
        """Read runtime preparation commands reported by DockerAgent."""
        summary = self._load_run_summary(workplace)
        if not summary:
            return [], None

        commands = self._normalize_commands(summary.get("verified_runtime_preparation_commands"))
        if commands:
            print(
                f"  Loaded structured runtime preparation command list ({len(commands)}): {commands}"
            )
            return commands, "runtime_verified_runtime_preparation_commands"

        bundle = summary.get("verification_bundle") or {}
        commands = self._normalize_commands(bundle.get("runtime_preparation_commands"))
        if commands:
            print(
                f"  Loaded runtime preparation commands from verification_bundle ({len(commands)}): {commands}"
            )
            return commands, "runtime_verification_bundle"

        return [], None

    def _extract_structured_test_commands(self, workplace: str) -> Tuple[List[str], Optional[str]]:
        """Read the best available structured test command list and its source."""
        summary = self._load_run_summary(workplace)
        if not summary:
            return [], None

        commands = self._normalize_commands(summary.get("verified_test_commands"))
        if commands:
            print(f"  Loaded structured test command list ({len(commands)}): {commands}")
            return commands, "runtime_verified_test_commands"

        command = summary.get("verified_test_command")
        if command:
            print(f"  Loaded structured test command: {command}")
            return [command], "runtime_verified_test_command"

        successful_commands = summary.get("successful_test_commands") or []
        if successful_commands:
            command = successful_commands[-1]
            print(f"  Falling back to last successful structured test command: {command}")
            return [command], "runtime_successful_test_commands"

        return [], None

    def _resolve_test_commands(
        self,
        workplace: str,
        structured_test_command: Optional[str],
        structured_test_commands: Optional[List[str]],
    ) -> Tuple[List[str], str]:
        """Resolve the best available structured test command sequence and its source."""
        commands = self._normalize_commands(structured_test_commands)
        if commands:
            return commands, "agent_runtime_argument_list"

        if structured_test_command:
            return [structured_test_command], "agent_runtime_argument"

        commands, source = self._extract_structured_test_commands(workplace)
        if commands:
            return commands, source or "runtime_summary"

        command = self._extract_test_command_from_setup_logs(workplace)
        if command:
            return [command], "legacy_setup_logs"

        return [], "language_default"

    def _resolve_runtime_preparation_commands(
        self,
        workplace: str,
        structured_runtime_preparation_commands: Optional[List[str]],
    ) -> Tuple[List[str], str]:
        commands = self._normalize_commands(structured_runtime_preparation_commands)
        if commands:
            return commands, "agent_runtime_argument_list"

        commands, source = self._extract_structured_runtime_preparation_commands(workplace)
        if commands:
            return commands, source or "runtime_summary"

        return [], "runtime_inferred_service_setup"

    def _generate_test_script(self, workplace: str, language: str,
                              problem_statement: str, test_patch: str,
                              dockerfile_content: str = "",
                              structured_runtime_preparation_commands: Optional[List[str]] = None,
                              structured_test_command: Optional[str] = None,
                              structured_test_commands: Optional[List[str]] = None) -> tuple:
        """
        生成测试脚本，并将 test_patch 注入 Dockerfile。
        优先使用 Agent 运行时记录的结构化测试命令，老数据再回退到 setup_logs。

        Returns:
            (eval_script, setup_scripts, updated_dockerfile)
        """
        workplace_path = Path(workplace)
        patch_info = self._parse_test_patch(test_patch, language) if test_patch else {}
        new_test_funcs = patch_info.get("new_test_funcs", [])

        # 优先使用运行时结构化记录，旧数据才回退到 setup_logs。
        extracted_commands, command_source = self._resolve_test_commands(
            workplace,
            structured_test_command,
            structured_test_commands,
        )
        self._last_test_command_source = command_source
        if extracted_commands:
            base_commands = extracted_commands
            print(f"  Using {len(base_commands)} test command(s) from {command_source}: {base_commands}")
        else:
            # 回退到基于语言的默认测试命令
            print(f"  No structured or legacy test command found, using default for {language}")
            base_commands = [self._get_default_test_command(language, workplace_path, test_patch, new_test_funcs)]

        runtime_preparation_commands, runtime_source = self._resolve_runtime_preparation_commands(
            workplace,
            structured_runtime_preparation_commands,
        )
        self._last_runtime_preparation_source = runtime_source

        # 根据工作目录调整命令路径
        # 如果命令包含 cd 到子目录，需要处理
        base_commands = [
            self._adjust_command_for_testbed(command)
            for command in base_commands
            if command
        ]
        runtime_preparation_commands = [
            self._adjust_command_for_testbed(command)
            for command in runtime_preparation_commands
            if command
        ]

        rebuild_commands = self._infer_post_patch_rebuild_commands(
            language=language,
            dockerfile_content=dockerfile_content,
            base_command=" && ".join(base_commands),
            test_patch=test_patch,
        )

        return self._build_eval_script(
            base_commands,
            language,
            test_patch,
            dockerfile_content,
            runtime_preparation_commands=runtime_preparation_commands,
            rebuild_commands=rebuild_commands,
        )

    def _get_default_test_command(self, language: str, workplace_path: Path,
                                   test_patch: str, new_test_funcs: List) -> str:
        """获取基于语言的默认测试命令"""
        if language.lower() == "python":
            return self._generate_python_test(workplace_path, test_patch, new_test_funcs)
        elif language.lower() in ("javascript", "typescript"):
            return "npm test"
        elif language.lower() == "java":
            return "mvn test"
        elif language.lower() == "go":
            return "go test ./..."
        elif language.lower() == "rust":
            return "cargo test"
        elif language.lower() == "ruby":
            # 根据 test_patch 检测使用 RSpec 还是 Minitest
            if test_patch and ("RSpec" in test_patch or "RSpec.describe" in test_patch):
                return "bundle exec rspec"
            else:
                # 默认使用 Minitest (rake test)
                # 使用 BUNDLE_WITHOUT 环境变量跳过 code_quality 组
                return "BUNDLE_WITHOUT=code_quality bundle exec rake test"
        elif language.lower() == "php":
            return "vendor/bin/phpunit"
        elif language.lower() in ("c", "c++", "cpp"):
            # C/C++ 项目可能有多种测试方式，尝试检测
            return self._detect_cpp_test_command(workplace_path)
        else:
            return "echo 'No default test command'"

    def _detect_cpp_test_command(self, workplace_path: Path) -> str:
        """检测 C/C++ 项目的测试命令"""
        # 检查是否有 cmake 构建目录
        if (workplace_path / "cmake_build").exists():
            return "cd cmake_build && ctest --output-on-failure"
        if (workplace_path / "build").exists():
            return "cd build && ctest --output-on-failure"
        # 检查 Makefile
        if (workplace_path / "Makefile").exists():
            return "make test"
        if (workplace_path / "test" / "Makefile").exists():
            return "cd test && make all"
        # 默认回退
        return "cd test && make all"

    def _adjust_command_for_testbed(self, command: str) -> str:
        """
        调整命令，确保在 /testbed 目录下正确执行。
        处理相对路径问题。
        """
        if not command:
            return command

        command = command.replace("/app/", "/testbed/")
        command = command.replace("cd /app", "cd /testbed")

        # 如果命令以 cd 开头，允许它在 /testbed 或子目录中自行切换。
        if command.startswith("cd "):
            return command

        # 相对路径可执行文件保持不变，依赖前面的 `cd /testbed` 作为工作目录。
        return command

    def _adjust_test_command_for_testbed(self, command: str) -> str:
        """Backward-compatible wrapper for older tests and call sites."""
        return self._adjust_command_for_testbed(command)

    def _build_eval_script(
        self,
        base_commands: List[str],
        language: str,
        test_patch: str,
        dockerfile_content: str,
        runtime_preparation_commands: Optional[List[str]] = None,
        rebuild_commands: Optional[List[str]] = None,
    ) -> tuple:
        """
        构建最终的 eval_script，处理依赖检查和 test_patch 注入。

        Returns:
            (eval_script, setup_scripts, updated_dockerfile)
        """
        # 生成 eval_script，包含依赖检查和重新安装逻辑
        # 关键：gold_fix patch 可能在运行时被应用，需要检测并重新安装依赖
        dependency_check = ""
        if language.lower() == "ruby":
            dependency_check = """
# Check if Gemfile was modified by patch and reinstall if needed
if [ -f Gemfile.lock ]; then
    BUNDLE_CHECK=$(bundle check 2>&1 || true)
    if echo "$BUNDLE_CHECK" | grep -q "The following gems are missing"; then
        echo "Gemfile dependencies changed, reinstalling..."
        rm -f Gemfile.lock
        bundle install --without code_quality || bundle install || exit 1
    fi
fi
"""
        elif language.lower() == "python":
            dependency_check = """
# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || exit 1
fi
"""
        elif language.lower() in ("javascript", "typescript"):
            dependency_check = """
# Check if package.json was modified and reinstall if needed
if [ -f package.json ]; then
    npm install || exit 1
fi
"""

        runtime_preparation_commands = runtime_preparation_commands or []
        runtime_service_setup = self._build_runtime_preparation_block(runtime_preparation_commands)
        if not runtime_service_setup:
            runtime_service_setup = self._infer_runtime_service_setup(base_commands, dockerfile_content)
        rebuild_commands = rebuild_commands or []
        eval_commands = list(rebuild_commands) + list(base_commands)
        command_block = " && \\\n".join(f"(\n{command}\n)" for command in eval_commands)

        eval_script = f"""#!/bin/bash

cd /testbed

{dependency_check}
{runtime_service_setup}
cd /testbed

set +e
{command_block}
TEST_EXIT_CODE=$?
set -e

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
"""

        # 若有 test_patch 且有 Dockerfile，将 test_patch 注入镜像 build context
        setup_scripts = {}
        updated_dockerfile = ""
        if test_patch and dockerfile_content:
            setup_scripts["test.patch"] = test_patch
            setup_scripts["apply_test_patch.sh"] = self._build_test_patch_apply_script()
            
            # 检测 test_patch 中使用的测试框架
            test_framework = self._detect_test_framework(test_patch, language)
            
            # 在 Dockerfile 中找到最后一个 RUN 行后插入 test.patch 相关命令
            # 关键：git apply 后必须重新安装依赖（patch 可能修改了 Gemfile/requirements.txt）
            lines = dockerfile_content.split('\n')
            new_lines = []
            for line in lines:
                new_lines.append(line)
            
            # 添加 test.patch 处理
            new_lines.append("")
            new_lines.append("# Apply test patch and reinstall dependencies")
            new_lines.append("COPY test.patch /tmp/test.patch")
            new_lines.append("COPY apply_test_patch.sh /tmp/apply_test_patch.sh")
            new_lines.append("RUN chmod +x /tmp/apply_test_patch.sh && /bin/bash /tmp/apply_test_patch.sh")
            
            # 根据 patch 是否修改了依赖文件，决定是否重新安装
            if language.lower() == "ruby":
                # Ruby: patch 可能修改 Gemfile，必须重新 bundle install
                # 同时安装 mocha（Minitest stub 方法需要）
                # 注意：mocha 需要 minitest 先加载，所以在 minitest/autorun 后添加 require
                new_lines.append("RUN gem install mocha minitest-mock && bundle add mocha --group test || true")
                # 使用 sed 在 minitest/autorun 行后插入 mocha/minitest require
                new_lines.append("RUN sed -i '/require.*minitest\\/autorun/a require \"mocha/minitest\"' /testbed/test/test_helper.rb || true")
                # Delete Gemfile.lock to avoid dependency conflicts
                new_lines.append("RUN rm -f /testbed/Gemfile.lock")
                new_lines.append("RUN bundle install --without code_quality || bundle install")
            elif language.lower() == "python":
                # Python: patch 可能修改 requirements.txt
                new_lines.append("RUN pip install -r requirements.txt || true")
            elif language.lower() in ("javascript", "typescript"):
                # JS/TS: patch 可能修改 package.json
                new_lines.append("RUN npm install || true")
            
            # 添加测试框架依赖安装
            if test_framework:
                new_lines.append(f"RUN {test_framework}")
                print(f"  Installing test framework: {test_framework}")

            for rebuild_command in rebuild_commands:
                print(f"  Rebuilding during evaluation after patch application: {rebuild_command}")
            
            updated_dockerfile = '\n'.join(new_lines)
            print("✓ test_patch injected into Dockerfile (baked into image)")

        return eval_script, setup_scripts, updated_dockerfile

    def _build_runtime_preparation_block(self, runtime_preparation_commands: List[str]) -> str:
        """Run agent-verified runtime preparation commands before the final tests."""
        if not runtime_preparation_commands:
            return ""

        return (
            "# Runtime preparation commands verified by the setup agent\n"
            "set -e\n"
            f"{chr(10).join(runtime_preparation_commands)}\n"
            "set +e\n"
        )

    def _normalize_run_instruction_for_docker(self, instruction: str) -> str:
        """Rewrite bash-only snippets into POSIX-compatible RUN instructions."""
        if not instruction.startswith("RUN "):
            return instruction

        command = instruction[4:]
        normalized_command = self._normalize_shell_command_for_docker_run(command)
        return f"RUN {normalized_command}"

    def _normalize_shell_command_for_docker_run(self, command: str) -> str:
        """Docker RUN uses /bin/sh by default, so avoid bash-only `source`."""
        if not command:
            return command
        return re.sub(r"(^|(?:&&|\|\||;)\s*)source\s+", r"\1. ", command)

    def _infer_runtime_service_setup(self, base_commands: List[str], dockerfile_content: str) -> str:
        """Start background services at runtime when tests depend on live daemons."""
        combined_commands = "\n".join(base_commands or []).lower()
        dockerfile_lower = (dockerfile_content or "").lower()
        setup_blocks: List[str] = []

        redis_needed = "redis-cli" in combined_commands or "redis-server" in combined_commands
        redis_available = "redis-server" in dockerfile_lower or "apt-get install -y redis-server" in dockerfile_lower
        redis_started_in_test = "redis-server --daemonize yes" in combined_commands
        if redis_needed and redis_available and not redis_started_in_test:
            setup_blocks.append(
                """# Start Redis for tests that depend on a live server
redis-server --daemonize yes || true
for attempt in $(seq 1 30); do
    if redis-cli ping >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
redis-cli ping >/dev/null 2>&1 || exit 1
"""
            )

        return "\n".join(setup_blocks)

    def _build_test_patch_apply_script(self) -> str:
        """Build a strict test patch applicator for the Docker build context."""
        return """#!/bin/bash
set -euo pipefail

cd /testbed

echo "[test_patch] validating patch with git apply --check"
if git apply --check /tmp/test.patch; then
    echo "[test_patch] git apply --check passed"
    git apply /tmp/test.patch
    echo "[test_patch] git apply succeeded"
    exit 0
fi

echo "[test_patch] git apply --check failed, trying patch fallback"
if command -v patch >/dev/null 2>&1; then
    if patch --batch --fuzz=5 -p1 -i /tmp/test.patch; then
        echo "[test_patch] patch fallback succeeded"
        exit 0
    fi
    echo "[test_patch] patch fallback failed"
else
    echo "[test_patch] patch command is not available for fallback"
fi

echo "[test_patch] unable to apply /tmp/test.patch"
exit 1
"""
    
    def _detect_test_framework(self, test_patch: str, language: str) -> str:
        """检测 test_patch 中使用的测试框架，返回安装命令"""
        if language.lower() == "ruby":
            # 优先检测 RSpec（通过 RSpec 特有的语法）
            if "RSpec" in test_patch or "RSpec.describe" in test_patch:
                # 需要安装 rspec 并添加到 Gemfile，然后执行 bundle install 确保所有依赖安装
                return "gem install rspec rspec-core && bundle add rspec --group development && bundle install || true"
            # Minitest 格式（def test_xxx）是 Ruby 默认测试方式，不需要额外安装
            # 注意：不要误判 describe/it，因为 Minitest 也可能用 minitest/spec 的 describe
            return ""
        elif language.lower() == "python":
            # 检测 pytest vs unittest
            if "def test_" in test_patch and "import unittest" not in test_patch:
                return "pip install pytest"
            return ""
        elif language.lower() in ("javascript", "typescript"):
            # 检测 jest vs mocha
            if "describe(" in test_patch or "it(" in test_patch:
                return "npm install --save-dev jest"
            return ""
        return ""

    def _infer_post_patch_rebuild_commands(
        self,
        language: str,
        dockerfile_content: str,
        base_command: str,
        test_patch: str,
    ) -> List[str]:
        """Rebuild compiled artifacts after test_patch is baked into the image."""
        language_key = language.lower()
        spec = self._get_rebuild_inference_spec(language_key)
        if not spec:
            return []
        if not test_patch:
            return []
        if not dockerfile_content:
            return []

        if self._command_matches_any((base_command or "").lower(), spec["eval_rebuilding_patterns"]):
            return []

        rebuild_commands: List[str] = []
        for line in dockerfile_content.splitlines():
            if not line.startswith("RUN "):
                continue
            command = line[4:].strip()
            command_lower = command.lower()

            if any(skip in command_lower for skip in spec["common_skip_substrings"]):
                continue
            if self._command_matches_any(command_lower, spec["test_only_patterns"]):
                continue

            if not self._command_matches_any(command_lower, spec["candidate_patterns"]):
                continue

            sanitized = self._sanitize_rebuild_command(command, language_key)
            if sanitized and sanitized not in rebuild_commands:
                rebuild_commands.append(sanitized)

        return rebuild_commands

    def _get_rebuild_inference_spec(self, language: str) -> Optional[Dict[str, Any]]:
        common_skip_substrings = (
            "apt-get",
            "yum ",
            "apk ",
            "dnf ",
            "git clone",
            "pip install",
            "npm install",
            "bundle install",
            "composer install",
        )
        common_test_only_patterns = (
            r"\bctest\b",
            r"\bpytest\b",
            r"\bnpm\s+test\b",
            r"\bmake\s+test\b",
            r"\bmake\s+check\b",
        )

        specs = {
            "c": {
                "candidate_patterns": (
                    r"\bcmake\b",
                    r"\bmake\b",
                    r"\bninja\b",
                    r"\bmeson\b",
                    r"\./configure\b",
                    r"\bautoreconf\b",
                ),
                "eval_rebuilding_patterns": (
                    r"\bcmake\s+--build\b",
                    r"\bmake\b",
                    r"\bninja\b",
                    r"\bmeson\s+compile\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"\bgo\s+test\b",
                    r"\bcargo\s+test\b",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "c++": {
                "candidate_patterns": (
                    r"\bcmake\b",
                    r"\bmake\b",
                    r"\bninja\b",
                    r"\bmeson\b",
                    r"\./configure\b",
                    r"\bautoreconf\b",
                ),
                "eval_rebuilding_patterns": (
                    r"\bcmake\s+--build\b",
                    r"\bmake\b",
                    r"\bninja\b",
                    r"\bmeson\s+compile\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"\bgo\s+test\b",
                    r"\bcargo\s+test\b",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "cpp": {
                "candidate_patterns": (
                    r"\bcmake\b",
                    r"\bmake\b",
                    r"\bninja\b",
                    r"\bmeson\b",
                    r"\./configure\b",
                    r"\bautoreconf\b",
                ),
                "eval_rebuilding_patterns": (
                    r"\bcmake\s+--build\b",
                    r"\bmake\b",
                    r"\bninja\b",
                    r"\bmeson\s+compile\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"\bgo\s+test\b",
                    r"\bcargo\s+test\b",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "java": {
                "candidate_patterns": (
                    r"^(?:\./)?mvnw?\b.*\b(?:compile|test-compile|package|install|verify|process-test-classes|process-classes)\b",
                    r"^mvn\b.*\b(?:compile|test-compile|package|install|verify|process-test-classes|process-classes)\b",
                    r"^(?:\./)?gradlew\b.*\b(?:assemble|build|classes|testclasses|jar|bootjar|compilejava|compiletestjava)\b",
                    r"^gradle\b.*\b(?:assemble|build|classes|testclasses|jar|bootjar|compilejava|compiletestjava)\b",
                    r"^sbt\b.*\b(?:compile|package|assembly|test:compile)\b",
                ),
                "eval_rebuilding_patterns": (
                    r"^(?:\./)?mvnw?\b.*\b(?:test|verify)\b",
                    r"^mvn\b.*\b(?:test|verify)\b",
                    r"^(?:\./)?gradlew\b.*\btest\b",
                    r"^gradle\b.*\btest\b",
                    r"^sbt\b.*\btest\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"\bgo\s+test\b",
                    r"\bcargo\s+test\b(?!.*--no-run)",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "kotlin": {
                "candidate_patterns": (
                    r"^(?:\./)?mvnw?\b.*\b(?:compile|test-compile|package|install|verify|process-test-classes|process-classes)\b",
                    r"^mvn\b.*\b(?:compile|test-compile|package|install|verify|process-test-classes|process-classes)\b",
                    r"^(?:\./)?gradlew\b.*\b(?:assemble|build|classes|testclasses|jar|bootjar|compilejava|compiletestjava)\b",
                    r"^gradle\b.*\b(?:assemble|build|classes|testclasses|jar|bootjar|compilejava|compiletestjava)\b",
                    r"^sbt\b.*\b(?:compile|package|assembly|test:compile)\b",
                ),
                "eval_rebuilding_patterns": (
                    r"^(?:\./)?mvnw?\b.*\b(?:test|verify)\b",
                    r"^mvn\b.*\b(?:test|verify)\b",
                    r"^(?:\./)?gradlew\b.*\btest\b",
                    r"^gradle\b.*\btest\b",
                    r"^sbt\b.*\btest\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"\bgo\s+test\b",
                    r"\bcargo\s+test\b(?!.*--no-run)",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "scala": {
                "candidate_patterns": (
                    r"^(?:\./)?mvnw?\b.*\b(?:compile|test-compile|package|install|verify|process-test-classes|process-classes)\b",
                    r"^mvn\b.*\b(?:compile|test-compile|package|install|verify|process-test-classes|process-classes)\b",
                    r"^(?:\./)?gradlew\b.*\b(?:assemble|build|classes|testclasses|jar|bootjar|compilejava|compiletestjava)\b",
                    r"^gradle\b.*\b(?:assemble|build|classes|testclasses|jar|bootjar|compilejava|compiletestjava)\b",
                    r"^sbt\b.*\b(?:compile|package|assembly|test:compile)\b",
                ),
                "eval_rebuilding_patterns": (
                    r"^(?:\./)?mvnw?\b.*\b(?:test|verify)\b",
                    r"^mvn\b.*\b(?:test|verify)\b",
                    r"^(?:\./)?gradlew\b.*\btest\b",
                    r"^gradle\b.*\btest\b",
                    r"^sbt\b.*\btest\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"\bgo\s+test\b",
                    r"\bcargo\s+test\b(?!.*--no-run)",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "rust": {
                "candidate_patterns": (
                    r"^cargo\s+build\b",
                    r"^cargo\s+test\b.*--no-run",
                    r"^cargo\s+nextest\b",
                ),
                "eval_rebuilding_patterns": (
                    r"^cargo\s+test\b",
                    r"^cargo\s+nextest\s+run\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"^cargo\s+test\b(?!.*--no-run)",
                    r"^cargo\s+nextest\s+run\b",
                    r"\bgo\s+test\b",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
            "go": {
                "candidate_patterns": (
                    r"^go\s+build\b",
                    r"^go\s+install\b",
                    r"^go\s+test\b.*(?:\s-c\b|\s-o\b)",
                ),
                "eval_rebuilding_patterns": (
                    r"^go\s+test\b",
                ),
                "test_only_patterns": common_test_only_patterns + (
                    r"^go\s+test\b(?!.*(?:\s-c\b|\s-o\b))",
                    r"\bcargo\s+test\b",
                ),
                "common_skip_substrings": common_skip_substrings,
            },
        }
        return specs.get(language)

    def _command_matches_any(self, command: str, patterns: Tuple[str, ...]) -> bool:
        if not command:
            return False
        return any(re.search(pattern, command, re.IGNORECASE) for pattern in patterns)

    def _sanitize_rebuild_command(self, command: str, language: str) -> str:
        if language in {"c", "c++", "cpp"}:
            return re.sub(r"\bmkdir\s+([^\s&;]+)", r"mkdir -p \1", command)
        if language in {"java", "kotlin", "scala"}:
            return self._sanitize_jvm_rebuild_command(command)
        if language == "rust":
            return self._sanitize_rust_rebuild_command(command)
        if language == "go":
            return self._sanitize_go_rebuild_command(command)
        return command

    def _sanitize_jvm_rebuild_command(self, command: str) -> str:
        normalized = command.lower()
        if normalized.startswith(("mvn ", "./mvnw", "mvnw ")):
            sanitized = command
            if "-DskipTests" not in sanitized and "-dskiptests" not in normalized:
                sanitized += " -DskipTests"
            if "-DskipITs" not in sanitized and "-dskipits" not in normalized:
                sanitized += " -DskipITs"
            return sanitized

        if normalized.startswith(("gradle ", "./gradlew")):
            sanitized = command
            if "testclasses" not in normalized:
                sanitized += " testClasses"
            if " -x test" not in normalized:
                sanitized += " -x test"
            return sanitized

        if normalized.startswith("sbt "):
            return "sbt Test/compile"

        return command

    def _sanitize_rust_rebuild_command(self, command: str) -> str:
        normalized = command.lower()
        if normalized.startswith("cargo test") and "--no-run" not in normalized:
            return command + " --no-run"
        if normalized.startswith("cargo nextest run"):
            return "cargo test --no-run"
        return command

    def _sanitize_go_rebuild_command(self, command: str) -> str:
        normalized = command.lower()
        if normalized.startswith("go test") and " -c" not in normalized and "\t-c" not in normalized:
            return command.replace("go test", "go build", 1)
        return command
    
    def _generate_python_test(self, workplace_path: Path, test_patch: str,
                              new_test_funcs: List = None) -> str:
        """为 Python 项目生成测试命令，优先跑 test_patch 新增的测试函数"""
        if new_test_funcs:
            # 只跑新增的测试函数，用 :: 语法指定
            # 去重（同一文件的多个函数合并为一条命令）
            file_to_funcs: Dict[str, List[str]] = {}
            for f, func in new_test_funcs:
                file_to_funcs.setdefault(f, []).append(func)
            targets = []
            for f, funcs in file_to_funcs.items():
                for func in funcs:
                    targets.append(f"{f}::{func}")
            test_targets = " ".join(targets)
            print(f"  Targeting test functions: {test_targets}")
            return f"python -m pytest {test_targets} -v"
        # 没有 test_patch 信息，运行全部测试
        return "python -m pytest -v"
    
    def _save_result(self, instance_id: str, result: Dict[str, Any]):
        """保存结果到文件"""
        output_file = self.output_dir / f"{instance_id}.json"
        with open(output_file, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\nResult saved to: {output_file}")
    
    def process_dataset(self, dataset_path: str, 
                       base_image: str = "auto",
                       model: str = "gpt-4o",
                       max_steps: int = 30,
                       enable_observation_compression: bool = False,
                       limit: Optional[int] = None) -> str:
        """
        批量处理数据集
        
        Args:
            dataset_path: JSONL 格式的数据集路径
            base_image: Docker 基础镜像
            model: LLM 模型
            max_steps: 每个实例的最大步骤数
            limit: 限制处理的实例数量(用于测试)
            
        Returns:
            汇总结果文件路径
        """
        results = []
        
        with open(dataset_path, 'r') as f:
            instances = [json.loads(line) for line in f]
        
        if limit:
            instances = instances[:limit]
        
        print(f"Processing {len(instances)} instances from {dataset_path}")
        
        for i, instance in enumerate(instances, 1):
            print(f"\n{'#'*60}")
            print(f"Instance {i}/{len(instances)}")
            print(f"{'#'*60}")
            
            result = self.process_single_instance(
                instance=instance,
                base_image=base_image,
                model=model,
                max_steps=max_steps,
                enable_observation_compression=enable_observation_compression,
            )
            results.append(result)
        
        # 保存汇总结果（评估框架期望字典格式，以 instance_id 为 key）
        summary_file = self.output_dir / "docker_res.json"
        docker_res_dict = {
            r["instance_id"]: r
            for r in results
            if not r["logs"].get("skip_evaluation") and r.get("dockerfile") and r.get("eval_script")
        }
        with open(summary_file, "w") as f:
            json.dump(docker_res_dict, f, indent=2)
        
        # 打印统计信息
        total = len(results)
        build_success = sum(1 for r in results if r["build_success"])
        skipped = sum(1 for r in results if r["logs"].get("skip_evaluation"))
        evaluable = len(docker_res_dict)
        
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        print(f"Total instances: {total}")
        print(f"Evaluable instances: {evaluable}/{total}")
        print(f"Skipped instances: {skipped}/{total}")
        print(f"Build success: {build_success}/{total} ({100*build_success/total:.1f}%)")
        print(f"Results saved to: {summary_file}")
        print(f"{'='*60}\n")
        
        return str(summary_file)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Docker-Eval Adapter for DockerAgent"
    )
    parser.add_argument(
        "dataset",
        help="Path to Multi-Docker-Eval dataset (JSONL format)"
    )
    parser.add_argument(
        "--output-dir",
        default="./multi_docker_eval_output",
        help="Output directory for results"
    )
    parser.add_argument(
        "--base-image",
        default="auto",
        help="Default Docker base image"
    )
    parser.add_argument(
        "--model",
        default="qwen3-max-2026-01-23",
        help="LLM model to use"
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=30,
        help="Maximum steps per instance"
    )
    parser.add_argument(
        "--enable-observation-compression",
        action="store_true",
        help="Enable AgentDiet-style observation compression"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of instances to process (for testing)"
    )
    
    args = parser.parse_args()
    
    adapter = MultiDockerEvalAdapter(output_dir=args.output_dir)
    adapter.process_dataset(
        dataset_path=args.dataset,
        base_image=args.base_image,
        model=args.model,
        max_steps=args.max_steps,
        enable_observation_compression=args.enable_observation_compression,
        limit=args.limit
    )


if __name__ == "__main__":
    main()
