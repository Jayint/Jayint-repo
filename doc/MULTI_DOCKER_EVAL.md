# Multi-Docker-Eval 完整运行指南

本文档记录从下载数据集到获取评估结果的完整流程。

## 完整运行流程（含实例）

### 步骤1: 环境准备

```bash
# 进入项目目录
cd /Users/panjianying/Desktop/Jayint-repo

# 激活虚拟环境
source .venv/bin/activate

# 安装依赖（如未安装）
uv pip install -r requirements.txt

# 确认 API Key 已配置
cat .env | grep OPENAI_API_KEY
```

### 步骤2: 下载数据集

```bash
# 使用 Python 下载 HuggingFace 数据集
python -c "
from datasets import load_dataset
ds = load_dataset('litble/Multi-Docker-Eval', split='test')
ds.to_json('task.jsonl', orient='records', lines=True)
print(f'Downloaded {len(ds)} instances')
"
```

**验证下载成功**:
```bash
# 查看数据集大小
wc -l task.jsonl                    # 应显示 334

# 查看第一条数据格式
head -1 task.jsonl | python -m json.tool
```

### 步骤3: 提取单个实例（测试用）

```bash
# 提取第一个实例用于测试
head -1 task.jsonl > single.jsonl

# 查看实例内容
cat single.jsonl | python -m json.tool | grep -E "(instance_id|repo|language)"
```

**预期输出**:
```json
{
    "instance_id": "getlogbook__logbook-183",
    "repo": "getlogbook/logbook",
    "language": "python"
}
```

### 步骤4: 运行 Agent 配置环境

```bash
# 运行单个实例
python multi_docker_eval_adapter.py single.jsonl \
    --limit 1 \
    --model gpt-4o \
    --max-steps 30
```

**运行过程说明**:
- Agent 会克隆仓库到临时 workplace 目录
- 自动识别依赖并安装（如 `pip install .`）
- 生成 Dockerfile 到 workplace 目录
- 生成测试脚本
- 输出结果到 `multi_docker_eval_output/`

**成功标志**:
```
============================================================
SUMMARY
============================================================
Total instances: 1
Build success: 1/1 (100.0%)
Results saved to: multi_docker_eval_output/docker_res.json
============================================================
```

### 步骤5: 查看 Agent 输出结果

```bash
# 查看生成的 docker_res.json
cat multi_docker_eval_output/docker_res.json | python -m json.tool

# 查看具体字段
cat multi_docker_eval_output/docker_res.json | python -c "
import json, sys
d = json.load(sys.stdin)[0]
print(f'Instance: {d[\"instance_id\"]}')
print(f'Build Success: {d[\"build_success\"]}')
print(f'Dockerfile length: {len(d[\"dockerfile\"])} chars')
print(f'Test Script: {d[\"test_script\"][:100]}...')
"
```

**输出位置**:
- 汇总结果: `multi_docker_eval_output/docker_res.json`
- 单个实例: `multi_docker_eval_output/{instance_id}.json`

### 步骤6: 运行 Multi-Docker-Eval 官方评估

```bash
# 确保在虚拟环境中
source .venv/bin/activate

# 安装评估框架依赖
uv pip install -r Multi-Docker-Eval/evaluation/requirements.txt

# 运行评估
PYTHONPATH=Multi-Docker-Eval:$PYTHONPATH python Multi-Docker-Eval/evaluation/main.py \
    base.dataset="single.jsonl" \
    base.docker_res="multi_docker_eval_output/docker_res.json" \
    base.run_id="DockerAgent" \
    base.output_path="eval_output"
```

### 步骤7: 查看评估结果

```bash
# 查看评估报告
cat eval_output/DockerAgent/final_report.json | python -m json.tool

# 查看详细指标
python -c "
import json
with open('eval_output/DockerAgent/final_report.json') as f:
    r = json.load(f)
print(f'Dataset instances: {r[\"dataset_instances\"]}')
print(f'Provided instances: {r[\"provided_instances\"]}')
print(f'Provided rate: {r[\"provided_rate\"]}%')
print(f'Resolved: {r[\"summary\"][\"details\"][\"resolved\"]}')
"
```

**评估结果位置**:
- 汇总报告: `eval_output/{run_id}/final_report.json`
- 镜像信息: `eval_output/{run_id}/image_sizes.json`

## 批量运行完整数据集

```bash
# 运行全部 334 个实例（耗时较长）
python multi_docker_eval_adapter.py task.jsonl \
    --output-dir ./eval_results \
    --model gpt-4o \
    --max-steps 30

# 然后评估
PYTHONPATH=Multi-Docker-Eval:$PYTHONPATH python Multi-Docker-Eval/evaluation/main.py \
    base.dataset="task.jsonl" \
    base.docker_res="eval_results/docker_res.json" \
    base.run_id="DockerAgent_full" \
    base.output_path="eval_output_full"
```

## 快速参考

| 文件/目录 | 用途 |
|-----------|------|
| `task.jsonl` | 下载的数据集（334条） |
| `single.jsonl` | 单条测试数据 |
| `multi_docker_eval_output/docker_res.json` | Agent 输出结果 |
| `multi_docker_eval_output/{instance_id}.json` | 单个实例详细结果 |
| `eval_output/{run_id}/final_report.json` | 官方评估报告 |

## 故障排查

### 问题: `ModuleNotFoundError: No module named 'datasets'`

```bash
uv pip install datasets
```

### 问题: `Repository Not Found` (HuggingFace)

需要在 HuggingFace 网站申请数据集访问权限。

### 问题: `ModuleNotFoundError: No module named 'evaluation'`

```bash
PYTHONPATH=Multi-Docker-Eval:$PYTHONPATH python ...
```

### 问题: `ModuleNotFoundError: No module named 'hydra'`

```bash
uv pip install -r Multi-Docker-Eval/evaluation/requirements.txt
```

## 输入输出格式

### 输入格式 (JSONL)

```json
{
  "instance_id": "repo_name__issue_123",
  "repo_url": "https://github.com/user/repo.git",
  "base_commit": "abc123...",
  "problem_statement": "Fix bug in...",
  "patch": "diff --git a/file.py...",
  "test_patch": "diff --git a/test_file.py...",
  "language": "python"
}
```

### 输出格式 (docker_res.json)

```json
[
  {
    "instance_id": "repo_name__issue_123",
    "repo_url": "https://github.com/user/repo.git",
    "language": "python",
    "dockerfile": "FROM python:3.10\nWORKDIR /app\nRUN pip install...",
    "test_script": "#!/bin/bash\nset -e\ncd /app\npython -m pytest...",
    "build_success": true,
    "test_success": false,
    "logs": {
      "agent_steps": [],
      "error": null
    }
  }
]
```

## 评估指标

Multi-Docker-Eval 会计算以下指标：

1. **F2P (Fail-to-Pass)**: 测试从失败到通过的成功率
2. **Build Success Rate**: Docker 镜像构建成功率
3. **Commit Rate**: 平均每个任务的 commit 次数
4. **Time Efficiency**: 平均完成时间

## 高级配置

### 自定义基础镜像

```bash
python multi_docker_eval_adapter.py dataset.jsonl \
    --base-image ubuntu:22.04 \
    --model gpt-4o
```

### 调整 Agent 参数

编辑 [`agent.py`](file:///Users/panjianying/Desktop/Jayint-repo/agent.py) 中的参数：

```python
# 修改最大步骤数
agent.run(max_steps=50, keep_container=False)

# 修改 Planner 的 system prompt
# 编辑 src/planner.py 中的 self.system_prompt
```

### 多语言支持

适配器自动根据语言选择基础镜像：

- Python → `python:3.10`
- JavaScript/TypeScript → `node:18`
- Java → `openjdk:17`
- Go → `golang:1.21`
- Rust → `rust:1.75`

## 项目架构

```
Jayint-repo/
├── agent.py                          # DockerAgent 主类
├── src/
│   ├── planner.py                    # ReAct 规划器
│   ├── sandbox.py                    # Docker 沙箱
│   └── synthesizer.py                # Dockerfile 生成器
├── multi_docker_eval_adapter.py      # Multi-Docker-Eval 适配器
└── doc/
    └── MULTI_DOCKER_EVAL.md          # 本文档
```

## 核心流程

```
输入 (JSONL) 
    ↓
MultiDockerEvalAdapter.process_single_instance()
    ↓
DockerAgent.run()
    ├─ Planner: 规划环境配置步骤 (ReAct)
    ├─ Sandbox: 执行 bash 命令 + commit 回滚
    └─ Synthesizer: 生成 Dockerfile
    ↓
生成 test_script
    ↓
输出 (docker_res.json)
    ↓
Multi-Docker-Eval 评估框架
```

## 注意事项

1. **Docker 必须运行**: 确保本机 Docker Engine 已启动
2. **API 成本**: 每个实例平均消耗 10-30 次 LLM 调用
3. **磁盘空间**: Agent 会创建中间镜像，建议 >20GB 可用空间
4. **超时设置**: 默认每个实例最多 30 步，可通过 `--max-steps` 调整

## 故障排除

### 问题1: Docker 连接失败

```
Error: Cannot connect to the Docker daemon
```

**解决**: 启动 Docker Desktop 或 Docker Engine

### 问题2: API Key 错误

```
ValueError: OPENAI_API_KEY not found
```

**解决**: 检查 `.env` 文件配置

### 问题3: 容器构建超时

```
Command failed (exit 124). Rolling back...
```

**解决**: 增加 `--max-steps` 参数或检查仓库依赖

## 贡献

参与 Multi-Docker-Eval 评估后，可提交结果到排行榜：

1. 生成 `docker_res.json`
2. Fork Multi-Docker-Eval 仓库
3. 提交 PR 附带结果和模型信息

## 引用

如果使用本项目参与评估，请引用：

```bibtex
@article{fu2024multidockereval,
  title={Multi-Docker-Eval: A 'Shovel of the Gold Rush' Benchmark on Automatic Environment Building for Software Engineering},
  author={Fu, Kelin and Liu, Tianyu and Shang, Zeyu and Ma, Yingwei and Yang, Jian and Liu, Jiaheng and Bian, Kaigui},
  journal={arXiv preprint arXiv:2512.06915},
  year={2024}
}
```

## 相关资源

- [Multi-Docker-Eval 论文](https://arxiv.org/abs/2512.06915)
- [Multi-Docker-Eval GitHub](https://github.com/Z2sJ4t/Multi-Docker-Eval)
- [Multi-Docker-Eval 数据集](https://huggingface.co/datasets/litble/Multi-Docker-Eval)
- [Leaderboard](https://github.com/Z2sJ4t/Multi-Docker-Eval#leaderboard)
