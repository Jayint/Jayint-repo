import re

class Planner:
    def __init__(self, client, model="gpt-4o"):
        self.client = client
        self.model = model
        self.history = []
        self.total_cost = 0.0  # 累计成本
        
        # 2026年2月官方价格 (美元/1M tokens)
        self.pricing = {
            # GPT-5 系列
            "gpt-5.2": {"input": 1.75, "output": 14.00},
            "gpt-5.2-pro": {"input": 21.00, "output": 168.00},
            "gpt-5.2-codex": {"input": 1.75, "output": 14.00},
            "gpt-5.1": {"input": 1.25, "output": 10.00},
            "gpt-5.1-codex": {"input": 1.25, "output": 10.00},
            "gpt-5": {"input": 1.25, "output": 10.00},
            "gpt-5-pro": {"input": 15.00, "output": 120.00},
            "gpt-5-mini": {"input": 0.25, "output": 2.00},
            "gpt-5-nano": {"input": 0.05, "output": 0.40},
            
            # GPT-4 系列
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
            "gpt-4": {"input": 30.00, "output": 60.00},
            "gpt-4.1": {"input": 2.00, "output": 8.00},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
            "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
            
            # GPT-3.5 系列
            "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
            
            # o 系列（推理模型）
            "o1": {"input": 15.00, "output": 60.00},
            "o1-pro": {"input": 150.00, "output": 600.00},
            "o3": {"input": 2.00, "output": 8.00},
            "o3-mini": {"input": 1.10, "output": 4.40},
            "o4-mini": {"input": 1.10, "output": 4.40},
        }
        
        self.system_prompt = (
            "You are an expert environment configuration agent. Your task is to set up a Docker "
            "environment for a given GitHub repository so that its code can run successfully.\n"
            "Current State: The repository has already been cloned and mounted into the working directory .\n\n"
            "Use the following ReAct format:\n"
            "Thought: <your reasoning>\n"
            "Action: <bash command to execute>\n"
            "Observation: <result of the command, will be provided by the system, DO NOT GENERATE THIS>\n\n"
            "Mission Guidelines:\n"
            "1. **Analyze & Setup**: Identify dependency files and install all necessary packages/tools.\n"
            "2. **Read README**: After setup, read `README.md` to find 'QuickStart' or startup instructions.\n"
            "3. **Verification**:\n"
            "   - If 'QuickStart' instructions are found, execute them to verify the environment.\n"
            "   - If no 'QuickStart' is found, analyze entry points (like `main.py`, `app.py`, etc.) and attempt to start the project for verification.\n"
            "   - **Secret/API_KEY Handling**: If verification fails due to missing API_KEYs or other secrets (which you cannot provide), identify exactly which keys are needed and how they should be configured.\n"
            "4. **Finalize**: Only output 'Final Answer: Success' after the environment is configured and verified (as much as possible).\n\n"
            "CRITICAL CONSTRAINTS (Environment Limitations):\n"
            "- You are running INSIDE a Docker container, NOT on a host machine.\n"
            "- FORBIDDEN commands: `docker build`, `docker run`, `docker-compose`, `systemctl`, `service`, `dockerd`, `sudo`\n"
            "- If the repository contains a Dockerfile, DO NOT try to build it. Instead, analyze it to understand dependencies and install them directly using package managers (pip, apt, npm, etc.).\n"
            "- Use ONLY: package managers (pip/uv/apt/yum/npm/etc.), language runtimes (python/node/etc.), and the project's own entry points.\n\n"
            "IMPORTANT:\n"
            "- Only output ONE Thought and ONE Action at a time.\n"
            "- Stop immediately after the Action."
        )

    def plan(self, repo_url, last_observation=None):
        """
        Generates the next step in the ReAct loop.
        Returns: thought, action, content, is_finished, cost_info
        """
        # 1. Initialize history with repository information on the first turn
        if not self.history:
            self.history.append({"role": "user", "content": f"Repository URL: {repo_url}"})

        # 2. Append the last observation as a new user message
        if last_observation is not None:
            self.history.append({"role": "user", "content": f"Observation: {last_observation}"})

        # 3. Construct the message list for the API call
        messages = [{"role": "system", "content": self.system_prompt}] + self.history

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            stop=["Observation:"]
        )

        content = response.choices[0].message.content
        
        # 4. Append the assistant's response (Thought and Action) to history
        self.history.append({"role": "assistant", "content": content})

        # 5. 计算本次调用成本
        usage = response.usage
        cost_info = self._calculate_cost(usage)

        thought = self._extract_tag(content, "Thought")
        action = self._extract_tag(content, "Action")
        is_finished = "Final Answer:" in content

        return thought, action, content, is_finished, cost_info

    def _calculate_cost(self, usage):
        """计算单次 API 调用的成本"""
        input_tokens = usage.prompt_tokens
        output_tokens = usage.completion_tokens
        total_tokens = usage.total_tokens
        
        # 获取当前模型的价格，如果没有则使用 gpt-4o 的价格
        price = self.pricing.get(self.model, self.pricing["gpt-4o"])
        
        # 计算成本（美元）
        input_cost = (input_tokens / 1_000_000) * price["input"]
        output_cost = (output_tokens / 1_000_000) * price["output"]
        step_cost = input_cost + output_cost
        
        self.total_cost += step_cost
        
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "step_cost": step_cost,
            "total_cost": self.total_cost
        }

    def _extract_tag(self, text, tag):
        pattern = rf"{tag}:\s*(.*?)(?=\n\w+:|$)"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            content = match.group(1).strip()
            # 1. Remove triple backticks (code blocks)
            content = re.sub(r"^```bash\n?", "", content)
            content = re.sub(r"^```\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
            # 2. Remove single backticks (command substitution characters)
            if content.startswith('`') and content.endswith('`'):
                content = content[1:-1].strip()
            return content.strip()
        return None
