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
                workplace=workplace
            )
            
            # 如果指定了 base_commit，先切换到该提交
            if base_commit:
                self._checkout_commit(workplace, base_commit)
            
            # 运行 agent 配置环境
            agent.run(max_steps=max_steps, keep_container=False)
            
            # 2. 提取 Dockerfile（在 WORKDIR 后插入 git clone + checkout）
            print(f"\n[Step 2/4] Extracting Dockerfile...")
            dockerfile_path = Path(workplace) / "Dockerfile"
            if dockerfile_path.exists():
                dockerfile_content = dockerfile_path.read_text()
                # 为评估框架插入 git clone + checkout（取代 COPY）
                # 评估框架在 /testbed 应用 patch，统一使用 /testbed
                dockerfile_lines = dockerfile_content.split('\n')
                new_dockerfile_lines = []
                for dl in dockerfile_lines:
                    new_dockerfile_lines.append(
                        dl.replace('WORKDIR /app', 'WORKDIR /testbed')
                           .replace('/app', '/testbed')
                    )
                dockerfile_content = '\n'.join(new_dockerfile_lines)

                git_commands = (
                    f"RUN apt-get update && apt-get install -y git\n"
                    f"RUN git clone {repo_url} /testbed\n"
                    f"RUN cd /testbed && git checkout {base_commit}"
                )
                # 确保测试工具已安装（仅 Python 项目需要 pytest）
                test_deps = ""
                if language.lower() == "python":
                    test_deps = "RUN pip install pytest"
                
                lines = dockerfile_content.split('\n')
                new_lines = []
                for i, line in enumerate(lines):
                    new_lines.append(line)
                    if line.startswith('WORKDIR') and i > 0:
                        new_lines.append(git_commands)
                # 在 Dockerfile 末尾添加测试依赖安装（仅 Python 且还没有 pytest）
                if test_deps and 'pytest' not in dockerfile_content:
                    new_lines.append(test_deps)
                result["dockerfile"] = '\n'.join(new_lines)
                result["build_success"] = True
                print("✓ Dockerfile generated successfully (with git clone)")
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

    def _generate_test_script(self, workplace: str, language: str,
                              problem_statement: str, test_patch: str,
                              dockerfile_content: str = "") -> tuple:
        """
        生成测试脚本，并将 test_patch 注入 Dockerfile。

        Returns:
            (eval_script, setup_scripts, updated_dockerfile)
        """
        workplace_path = Path(workplace)
        patch_info = self._parse_test_patch(test_patch, language) if test_patch else {}
        new_test_funcs = patch_info.get("new_test_funcs", [])

        # 基于语言的默认测试命令
        if language.lower() == "python":
            base_command = self._generate_python_test(workplace_path, test_patch, new_test_funcs)
        elif language.lower() in ("javascript", "typescript"):
            base_command = "npm test"
        elif language.lower() == "java":
            base_command = "mvn test"
        elif language.lower() == "go":
            base_command = "go test ./..."
        elif language.lower() == "rust":
            base_command = "cargo test"
        elif language.lower() == "ruby":
            base_command = "bundle exec rspec"
        elif language.lower() == "php":
            base_command = "vendor/bin/phpunit"
        elif language.lower() in ("c", "c++", "cpp"):
            # C/C++ projects often use Makefile for testing
            base_command = "cd test && make all"
        else:
            base_command = "echo 'No default test command'"

        eval_script = f"""#!/bin/bash

cd /testbed

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
            # 在 Dockerfile 中找到 git checkout 行后插入 git apply
            lines = dockerfile_content.split('\n')
            new_lines = []
            injected = False
            for line in lines:
                new_lines.append(line)
                if not injected and re.search(r'git checkout', line):
                    new_lines.append("COPY test.patch /tmp/test.patch")
                    new_lines.append("RUN cd /testbed && git apply /tmp/test.patch || true")
                    injected = True
            updated_dockerfile = '\n'.join(new_lines)
            if injected:
                print("✓ test_patch injected into Dockerfile (baked into image)")
            else:
                updated_dockerfile = ""  # 未找到合适的注入点，保持原样

        return eval_script, setup_scripts, updated_dockerfile
    
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
