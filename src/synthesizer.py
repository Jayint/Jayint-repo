class Synthesizer:
    def __init__(self, base_image="python:3.10", workdir="/app"):
        self.base_image = base_image
        self.workdir = workdir
        self.instructions = []
        self.setup_commands = []  # 记录真实执行成功的安装/配置指令
        self.api_key_hints = []  # 记录检测到的 API Key 需求

    def record_success(self, command):
        """Records a successful bash command as a RUN instruction."""
        self.instructions.append(f"RUN {command}")
        
        # 同时记录用于 QuickStart 的原始指令
        if self._is_setup_command(command):
            self.setup_commands.append(command)
    
    def record_api_key_hint(self, key_name, detection_context=""):
        """记录检测到的 API Key 需求"""
        hint = {"key_name": key_name, "context": detection_context}
        if hint not in self.api_key_hints:
            self.api_key_hints.append(hint)
    
    def _is_setup_command(self, command):
        """判断指令是否是环境配置相关的（用于 QuickStart）"""
        setup_keywords = [
            'pip install', 'apt', 'yum', 'npm install', 'yarn add',
            'git clone', 'wget', 'curl', 'make', 'cmake',
            'python -m', 'poetry install', 'conda install'
        ]
        return any(keyword in command.lower() for keyword in setup_keywords)

    def generate_quickstart_with_llm(self, workplace_path, client, model="gpt-4o", file_name="QuickStart.md"):
        """使用 LLM 基于 README.md 和真实安装步骤生成简洁的 QuickStart 文档"""
        import os
        
        if not self.setup_commands:
            print("[Warning] No setup commands recorded. QuickStart.md will not be generated.")
            return
        
        # 过滤掉纯查看类指令，只保留安装配置指令
        relevant_commands = [cmd for cmd in self.setup_commands if self._is_relevant_for_quickstart(cmd)]
        
        if not relevant_commands:
            print("[Warning] No relevant setup commands found.")
            return
        
        # 读取 README.md
        readme_path = os.path.join(workplace_path, "README.md")
        readme_content = ""
        if os.path.exists(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8", errors="ignore") as f:
                    readme_content = f.read()
            except:
                readme_content = "(README.md not found or unreadable)"
        else:
            readme_content = "(README.md not found)"
        
        # 构造 Prompt
        prompt = f"""
You are a technical documentation assistant. Your task is to create a concise QuickStart.md file.

**Input Information:**

1. **Successfully Executed Setup Commands** (these were verified to work in the Docker environment):
```bash
{chr(10).join(relevant_commands)}
```

2. **Original README.md Content** (extract startup/run commands from here):
```
{readme_content[:3000]}  # 限制长度避免 token 溢出
```

**Your Task:**
Generate a **concise** QuickStart.md with the following structure:

1. **## Setup Steps**: List the setup commands above as executable bash code blocks (Step 1, Step 2, etc.).
2. **## How to Run**: Extract the actual startup/run commands from the README. These should be:
   - Commands that START or RUN the application (NOT installation commands)
   - Verification commands like `--version` or `--help`
   - Example usage commands
   - DO NOT include any installation commands (pip install, apt install, yay, nix, etc.)
3. **## API Key Configuration** (ONLY if the project requires API keys based on README analysis):
   - Detect if the project needs API keys (look for keywords: OPENAI_API_KEY, API_KEY, TOKEN, etc. in README)
   - If API keys are needed, provide TWO methods:
     a) **Method 1: Environment Variables** - Show how to export keys in shell
     b) **Method 2: .env File** - Show how to create .env file with required keys
   - Adapt the variable names based on what the README actually uses
4. **## Notes**: Brief notes about other secrets or configurations if needed.

**Requirements:**
- Be extremely concise
- Only include executable bash commands in code blocks
- Do NOT include terminal output, logs, or help menus
- Each command should be on its own line in the code block
- For API keys section, use actual variable names from the README (e.g., if README mentions ANTHROPIC_API_KEY, use that)

Generate the complete QuickStart.md content now:
"""
        
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0
            )
            
            quickstart_content = response.choices[0].message.content.strip()
            
            # 写入文件
            output_path = os.path.join(workplace_path, file_name)
            with open(output_path, "w") as f:
                f.write(quickstart_content)
            
            print(f"QuickStart.md successfully generated at {output_path}")
            return quickstart_content
            
        except Exception as e:
            print(f"[Error] Failed to generate QuickStart.md with LLM: {e}")
            return None
    
    def _is_relevant_for_quickstart(self, command):
        """判断指令是否应该出现在 QuickStart 文档中"""
        # 过滤掉纯信息查询指令
        irrelevant_keywords = ['ls', 'cat', 'echo', 'pwd', 'env', 'grep', 'find', 'head', 'tail']
        first_word = command.strip().split()[0].lower()
        return first_word not in irrelevant_keywords
    
    def generate_dockerfile(self, file_path="Dockerfile"):
        """Generates the final Dockerfile."""
        content = [
            f"FROM {self.base_image}",
            f"WORKDIR {self.workdir}",
            ""
        ]
        content.extend(self.instructions)
        
        with open(file_path, "w") as f:
            f.write("\n".join(content))
        
        print(f"Dockerfile successfully generated at {file_path}")
        return "\n".join(content)
