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
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from agent import DockerAgent


class MultiDockerEvalAdapter:
    """适配器：将 DockerAgent 输出转换为 Multi-Docker-Eval 评估格式"""
    
    def __init__(self, output_dir: str = "./multi_docker_eval_output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def process_single_instance(self, instance: Dict[str, Any], 
                               base_image: str = "python:3.10",
                               model: str = "gpt-4o",
                               max_steps: int = 30) -> Dict[str, Any]:
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
        patch = instance.get("patch", "")
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
                "error": None
            }
        }
        
        # 创建workplace目录（使用项目目录下的workplace，便于查看）
        workplace = os.path.join("./workplace", f"multi_docker_eval_{instance_id}")
        os.makedirs(workplace, exist_ok=True)
        
        try:
            # 1. 运行 DockerAgent 进行环境配置
            print(f"[Step 1/4] Running DockerAgent for environment configuration...")
            agent = DockerAgent(
                repo_url=repo_url,
                base_image="auto",  # Use LLM-based 4-step image selection
                model=model,
                workplace=workplace,
                base_commit=base_commit  # checkout before image selection for accurate LLM analysis
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
            print(f"\n[Step 2/4] Extracting Dockerfile...")
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
                    
                    dockerfile_content = f"""{syntax_directive}{base_image_line}
WORKDIR /testbed

# Install git for cloning
RUN apt-get update && apt-get install -y git

# Clone repository and checkout base commit
RUN git clone {repo_url} /testbed
RUN cd /testbed && git checkout {base_commit}

# Agent's verified setup instructions
{chr(10).join(processed_instructions) if processed_instructions else '# No additional setup instructions from agent'}
"""
                    result["dockerfile"] = dockerfile_content
                    result["build_success"] = True
                    print(f"✓ Dockerfile generated with {len(agent_run_instructions)} agent instructions")
            else:
                print("✗ Dockerfile not found")
                result["logs"]["error"] = "Dockerfile generation failed"
            
            # 3. 生成测试脚本 & 将 test_patch 注入镜像
            print(f"\n[Step 3/4] Generating test script...")
            test_script, setup_scripts, dockerfile_with_patch = self._generate_test_script(
                workplace=workplace,
                language=language,
                problem_statement=problem_statement,
                test_patch=test_patch,
                dockerfile_content=result.get("dockerfile", "")
            )
            result["eval_script"] = test_script
            result["setup_scripts"] = setup_scripts
            if dockerfile_with_patch:
                result["dockerfile"] = dockerfile_with_patch
            
            # 4. 验证测试 (可选，取决于是否需要在适配器中执行)
            print(f"\n[Step 4/4] Test script generated")
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

    def _generate_test_script(self, workplace: str, language: str,
                              problem_statement: str, test_patch: str,
                              dockerfile_content: str = "") -> tuple:
        """
        生成测试脚本，并将 test_patch 注入 Dockerfile。
        优先从 setup_logs 中提取 Agent 实际验证成功的测试命令。

        Returns:
            (eval_script, setup_scripts, updated_dockerfile)
        """
        workplace_path = Path(workplace)
        patch_info = self._parse_test_patch(test_patch, language) if test_patch else {}
        new_test_funcs = patch_info.get("new_test_funcs", [])

        # 首先尝试从 setup_logs 中提取实际验证成功的测试命令
        extracted_command = self._extract_test_command_from_setup_logs(workplace)
        if extracted_command:
            base_command = extracted_command
            print(f"  Using extracted test command: {base_command}")
        else:
            # 回退到基于语言的默认测试命令
            print(f"  No test command found in setup_logs, using default for {language}")
            base_command = self._get_default_test_command(language, workplace_path, test_patch, new_test_funcs)

        # 根据工作目录调整命令路径
        # 如果命令包含 cd 到子目录，需要处理
        base_command = self._adjust_test_command_for_testbed(base_command)

        return self._build_eval_script(base_command, language, test_patch, dockerfile_content)

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

    def _adjust_test_command_for_testbed(self, command: str) -> str:
        """
        调整测试命令，确保在 /testbed 目录下正确执行。
        处理相对路径问题。
        """
        if not command:
            return command

        # 如果命令以 cd 开头，确保它从 /testbed 开始
        if command.startswith("cd "):
            # 已经有 cd，保持不变（假设 Dockerfile 中 WORKDIR 是 /testbed）
            return command

        # 如果命令是相对路径的可执行文件，确保路径正确
        # 例如 "./run_tests.sh" -> 保持不变
        return command

    def _build_eval_script(self, base_command: str, language: str,
                           test_patch: str, dockerfile_content: str) -> tuple:
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
        bundle install --without code_quality || bundle install
    fi
fi
"""
        elif language.lower() == "python":
            dependency_check = """
# Check if requirements.txt was modified and reinstall if needed
if [ -f requirements.txt ]; then
    pip install -r requirements.txt || true
fi
"""
        elif language.lower() in ("javascript", "typescript"):
            dependency_check = """
# Check if package.json was modified and reinstall if needed
if [ -f package.json ]; then
    npm install || true
fi
"""

        eval_script = f"""#!/bin/bash

cd /testbed

{dependency_check}
{base_command}
TEST_EXIT_CODE=$?

echo "echo OMNIGRIL_EXIT_CODE=$TEST_EXIT_CODE"
exit $TEST_EXIT_CODE
"""

        # 若有 test_patch 且有 Dockerfile，将 test_patch 注入镜像 build context
        setup_scripts = {}
        updated_dockerfile = ""
        if test_patch and dockerfile_content:
            setup_scripts["test.patch"] = test_patch
            
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
            new_lines.append("RUN cd /testbed && git apply /tmp/test.patch || true")
            
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
            
            updated_dockerfile = '\n'.join(new_lines)
            print("✓ test_patch injected into Dockerfile (baked into image)")

        return eval_script, setup_scripts, updated_dockerfile
    
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
            json.dump(result, f, indent=2)
        print(f"\nResult saved to: {output_file}")
    
    def process_dataset(self, dataset_path: str, 
                       base_image: str = "auto",
                       model: str = "gpt-4o",
                       max_steps: int = 30,
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
                max_steps=max_steps
            )
            results.append(result)
        
        # 保存汇总结果（评估框架期望字典格式，以 instance_id 为 key）
        summary_file = self.output_dir / "docker_res.json"
        docker_res_dict = {r["instance_id"]: r for r in results}
        with open(summary_file, "w") as f:
            json.dump(docker_res_dict, f, indent=2)
        
        # 打印统计信息
        total = len(results)
        build_success = sum(1 for r in results if r["build_success"])
        
        print(f"\n{'='*60}")
        print(f"SUMMARY")
        print(f"{'='*60}")
        print(f"Total instances: {total}")
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
        default="python:3.10",
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
        limit=args.limit
    )


if __name__ == "__main__":
    main()
