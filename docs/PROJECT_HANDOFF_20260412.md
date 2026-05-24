# 项目交接记录 2026-04-12

本文档用于在其他设备或新对话中继续该项目。建议新会话开始时先让模型阅读本文件，再继续执行任务。

## 项目目标

本项目是一个面向 Multi-Docker-Eval 的环境配置 agent。目标是让 agent 自动为给定 GitHub 仓库构建可复现 Docker 环境，并生成最终评估所需的 `docker_res.json`、Dockerfile 和测试脚本。

当前重点工作是降低长上下文带来的失败率。已经引入 AgentDiet 风格的轨迹压缩机制，压缩对象仅限 Observation，不压缩 Thought 和 Action。

## 当前模型配置

项目默认模型已经切换为：

```text
MiniMax-M2.7-highspeed
```

默认模型集中定义在：

```text
src/constants.py
```

环境变量优先使用：

```text
MINIMAX_API_KEY
MINIMAX_API_BASE
```

如果走 OpenAI SDK 的 `client.chat.completions.create(...)`，`MINIMAX_API_BASE` 应使用 OpenAI 兼容入口：

```text
https://api.minimaxi.com/v1
```

不要使用 Anthropic 兼容入口：

```text
https://api.minimaxi.com/anthropic
```

否则会出现 nginx 404。

## 关键实现状态

Observation 压缩已经接入 agent 主循环。压缩模式通过参数开启：

```bash
--enable-observation-compression
```

压缩策略要点：

- 只压缩 Observation。
- Thought 和 Action 保持原样。
- 使用统一 LLM prompt 处理不同类型 observation，不再做复杂分类。
- reflection/compression 使用与 setup 阶段相同的模型。
- quickstart 相关 token 不计入，也不再作为重点维护方向。
- token 统计保留总 token 与 planner/image selector/reflection 分桶，不计算费用。

超大 observation 的 safety compression 已接入：当 observation 超过约 200k 字符时，会先进行信息保留式截断/提取，避免在压缩前就爆上下文。

## 重要模块

```text
agent.py
```

agent 主循环、token ledger、Observation compression 接入、Verification Bundle 接受逻辑、agent run summary 输出。

```text
src/planner.py
```

planner prompt、ReAct 输出解析、managed history。MiniMax 曾出现一次输出多个 Action/Observation 的问题，已加 history sanitizer：即使模型一次性吐出多步伪轨迹，也只会把第一组 `Thought + Action` 写入后续 history。

```text
src/observation_compressor.py
```

AgentDiet 风格 observation 压缩、safety compression、step 结构、token ledger。

```text
src/synthesizer.py
```

将成功 setup 命令合成 Dockerfile。最近修复了一个关键问题：`php --version && composer --version 2>/dev/null || echo ...` 这类工具探测命令不应写入 Dockerfile；但 `which composer || (curl ... | php ...)` 里的真实 composer 安装 fallback 仍应保留。

```text
src/sandbox.py
```

容器执行、rollback、runtime service replay、apt bootstrap 和 package manager broken state hint。

```text
multi_docker_eval_adapter.py
```

将 DockerAgent 输出适配为 Multi-Docker-Eval 所需的 `docker_res.json`，并生成最终评估 Dockerfile / eval script。

```text
run_verified_regression.py
```

推荐的回归测试入口。它可以运行单条 `single.jsonl`，也可以运行六条 `verified.jsonl`，并会先跑 adapter，再调用 Multi-Docker-Eval evaluation。

## 最近已解决的问题

1. MiniMax API base URL 错误。

错误表现：ImageSelector 阶段返回 nginx 404 HTML。

原因：项目使用 OpenAI SDK，但 `.env` 中配置成了 Anthropic 兼容入口。

正确配置：

```env
MINIMAX_API_BASE=https://api.minimaxi.com/v1
```

2. MiniMax 不遵守“只输出一个 Thought 和一个 Action”。

错误表现：`setup_logs/0.md` 中第一轮 LLM 输出包含许多伪 Action、伪 Observation 和伪 Final Answer。

修复：`src/planner.py` 中增强 prompt，并新增 sanitizer。raw log 里仍可能看到模型原始越界输出，但后续 planner history 不会被污染。

3. Spomky-Labs__otphp-166 评估 Docker build 失败。

错误表现：最终评估 Dockerfile 中出现：

```dockerfile
RUN php --version && composer --version 2>/dev/null
RUN (curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null)
```

第一行在 composer 安装前执行，导致 exit code 127。

原因：synthesizer 将工具版本探测命令错误记录为 setup 指令，并且在重放时丢掉了 `|| echo ...` fallback。

修复：`src/synthesizer.py` 现在将常见工具的 `--version` / `-v` / `version` 探测视为 read-only，不写入 Dockerfile；同时保留真正的 installer fallback。

## 最近测试状态

在最近一次代码修复后，以下命令通过：

```bash
python -m py_compile src/synthesizer.py
python -m unittest discover -s tests -v
```

全量单测结果：

```text
86 tests OK
```

注意：最近一次 `Spomky-Labs__otphp-166` 的失败输出目录 `outputs/verified_regression_compressed/20260411_161039` 是修复前的旧结果，不应再用它判断当前代码是否失败。

## 推荐运行命令

运行 `single.jsonl` 的压缩版本：

```bash
cd /Users/panjianying/Desktop/Jayint-repo

.venv/bin/python run_verified_regression.py \
  --dataset single.jsonl \
  --output-root outputs/verified_regression_compressed \
  --model MiniMax-M2.7-highspeed \
  --enable-observation-compression \
  --max-steps 100 \
  --max-workers 1
```

运行六条 verified 回归测试：

```bash
cd /Users/panjianying/Desktop/Jayint-repo

.venv/bin/python run_verified_regression.py \
  --dataset verified.jsonl \
  --output-root outputs/verified_regression_compressed \
  --model MiniMax-M2.7-highspeed \
  --enable-observation-compression \
  --max-steps 100 \
  --max-workers 1
```

如果 `.venv/bin/python` 不存在，可用实际环境中的 Python，但要保证 Multi-Docker-Eval 依赖可用。

## 如何看结果

每次运行会生成 timestamp 目录，例如：

```text
outputs/verified_regression_compressed/YYYYMMDD_HHMMSS/
```

优先看：

```text
summary.json
results/<instance_id>.json
eval_output/<run_id>/<instance_id>/combined_report.json
eval_output/<run_id>/<instance_id>/build/build_image.log
```

agent 构建过程看：

```text
workplace/multi_docker_eval_<instance_id>/setup_logs/
workplace/multi_docker_eval_<instance_id>/agent_run_summary.json
```

压缩效果重点看：

```text
agent_run_summary.json 中的 observation_compression_enabled
agent_run_summary.json 中的 compression_stats
agent_run_summary.json 中的 token_usage
```

## 长期记忆模块状态

长期记忆机制目前主要处于方案阶段。方案文档在：

```text
doc/LONG_TERM_MEMORY_PLAN.md
```

当前倾向：

- 不做两层记忆。
- 只记录“多次失败后最终解决”的问题。
- 以整个 setup 过程为单位生成记忆，优先分析最终完整 setup log，而不是逐 step 生成。
- link generation 保留 embedding 相似度方式，不用规则连边。
- 不做 memory evolution。
- 检索只在指令失败后，由 agent 自行决定是否 retrieve。
- system prompt 保持短而稳定。

该模块尚未作为完整功能接入主流程。

## 当前工作树注意事项

本仓库当前有大量运行产物和历史输出目录变动，例如 `outputs/`、`eval_output/`、`multi_docker_eval_output/`、`workplace/` 等。继续开发时不要盲目 `git reset --hard` 或批量删除，先区分源码改动与运行产物。

当前应重点关注的源码/文档改动包括：

```text
agent.py
multi_docker_eval_adapter.py
run_verified_regression.py
src/constants.py
src/image_selector.py
src/planner.py
src/synthesizer.py
tests/test_planner_history.py
tests/test_synthesizer.py
.env.example
README.md
verify_multi_docker_eval.sh
doc/LONG_TERM_MEMORY_PLAN.md
doc/PROJECT_HANDOFF_20260412.md
```

## 下一步建议

1. 重新运行 `single.jsonl` 的压缩版本，验证 Spomky-Labs__otphp-166 是否不再因为 `composer --version` Docker build 失败。
2. 如果单条通过，再运行六条 `verified.jsonl` 回归测试。
3. 回归通过后，检查 `summary.json`、每条 `agent_run_summary.json` 的 compression/token 统计，确认压缩收益和失败模式。
4. 在确认运行产物不需要入库后，只提交源码、测试和文档相关改动，避免把大批 `outputs/`、`workplace/`、`eval_output/` 结果混进提交。
