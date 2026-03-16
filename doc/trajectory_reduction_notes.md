# DockerAgent 轨迹压缩设计笔记

## 目标

借鉴论文 `Improving the Efficiency of LLM Agent Systems through Trajectory Reduction` 的思路，
在不明显伤害环境配置成功率的前提下，降低这个项目中的 prompt 膨胀问题。

这份文档刻意写成“实现备忘录”，而不是论文摘要。它的目的，是把后续值得做的事情先整理成一份
可执行的 backlog。

## 为什么这个项目很适合做这件事

当前 agent loop 和论文里分析的问题非常像：

- `src/planner.py` 会把每一步的 observation 和 assistant 回复不断追加到 `history`
- 工具输出经常很长，比如：
  - 包管理器安装日志
  - 完整测试日志
  - 文件列表
  - README / 配置文件的大段内容
  - 编译输出
- 这些长输出会在后续很多轮里被重复喂给模型

这个项目尤其适合做 trajectory reduction，因为它的很多上下文是高度结构化、重复性强、而且可规则化压缩的。
很多内容甚至不需要第二个 LLM，先用规则就能安全压短。

## 从论文里得到的核心启发

最重要的启发，不是“把旧文本总结一下”。

真正重要的是：

1. 把轨迹压缩当成一个独立的系统能力
2. 不要让主 planner 自己决定怎么压缩
3. 在运行过程中持续做，而不是等上下文满了再处理
4. 只有当规则压缩不够时，再让一个便宜模型接手

映射到这个仓库里，最自然的做法就是：在 `Sandbox.execute()` 和 `Planner.plan()` 之间增加一层 reducer。

## 当前仓库里的几个明显痛点

### 1. 完整 observation 会一直保留

`Planner.history` 是单调增长的，目前保存的是原始 observation。

### 2. 很多长输出在过一两步之后就没什么价值

典型例子：

- `pip install` 成功后的安装日志
- `pytest` 中大量 PASS 行
- `ls`、`find`、`tree`、`sed -n`、`cat` 的探索输出
- 编译和下载进度日志

### 3. planner 没有“上下文已过期”的概念

一旦某个问题已经解决，或者某条路径已经排除，相关的大段日志依然留在 prompt 里。

### 4. 日志和 prompt 状态耦合过深

项目现在已经把详细日志落盘了，这很好。但这些原始日志没有必要一直完整地留在模型上下文里。

## 推荐设计

## 1. 增加结构化轨迹层

不要继续只靠 `Planner.history` 里的原始字符串表示每一步。建议引入一层结构化的 step 记录。

建议的数据形状：

```python
StepRecord = {
    "step_index": int,
    "thought": str | None,
    "action": str | None,
    "raw_observation": str,
    "prompt_observation": str,
    "observation_kind": str,
    "was_reduced": bool,
    "reduction_method": str | None,
    "token_estimate_raw": int | None,
    "token_estimate_reduced": int | None,
}
```

其中：

- `raw_observation` 是审计 / 调试的原始真相
- `prompt_observation` 是后续真正送进模型的压缩版本

## 2. 在 planner 之外增加 reducer 模块

建议新增一个模块，例如：

- `src/trajectory_reducer.py`

这个 reducer 应该由 `agent.py` 调用，而不是让 LLM 自己决定什么时候做。

大致流程：

1. 在 sandbox 中执行 action
2. 生成一个 `StepRecord`
3. 如果有必要，对 observation 做压缩
4. 把压缩后的记录交给 planner 用作后续上下文

这正对应论文里最重要的系统设计经验：不要期待主 agent 擅长管理自己的 trajectory。

## 3. 优先做规则压缩，不要一上来就依赖第二个 LLM

这个仓库很适合先做确定性的 reducer。

建议先做下面几类：

### 测试输出 reducer

保留：

- 失败测试名
- 错误摘要
- traceback 尾部
- 最终统计信息，比如 `passed`、`failed`、`skipped`

删除或折叠：

- 大段连续的通过测试行
- 与失败原因无关的重复 warning

### 安装日志 reducer

保留：

- 能识别出的已安装包名
- resolver / dependency conflict 错误
- 缺失系统依赖的错误
- 最终成功 / 失败标志

删除或折叠：

- 下载进度
- wheel 构建噪声
- 重复依赖行

### 文件探索 reducer

针对 `ls`、`find`、`cat`、`sed -n` 这类命令：

保留：

- 文件名
- 命中的路径
- 行号范围
- 如果很明显，可以附一条简短 takeaway

删除或折叠：

- 巨大的目录枚举
- 已经看过重点之后的整段文件内容

### 通用长输出 reducer

如果没有命中特殊 reducer：

- 保留头部和尾部
- 强保留包含 `error`、`failed`、`warning`、`traceback`、`exception`、`not found` 的行
- 用简短标记替换中间省略部分

## 4. 再加一个可选的便宜 LLM reducer 作为 fallback

只有在规则 reducer 已经存在后，再考虑增加一个便宜模型做 reflection reducer。

适合触发它的场景：

- 输出很长
- 没有命中确定性 reducer
- 或者规则压完之后仍然太长

这个 reducer 需要满足：

- 只吃一个小的 sliding window
- 每次只压缩一条较旧的 step
- 保留可执行事实，比如路径、命令、版本、失败测试名
- 不得捏造原始 observation 里不存在的命令

## 5. 使用 sliding-window 策略

论文里的参数思路可以直接拿来试。

这个项目一个不错的初始配置是：

- 延迟目标 step：`a = 2`
- 提供局部上下文：`b = 1`
- 只有 observation 超过阈值时才压：`theta ~= 500 tokens`

它的实际含义是：

- 永远不要压最新的 2 步
- 只回头处理稍旧一点的 observation
- 太短的 observation 不值得动

## 6. 原始日志继续落盘，prompt 中只保留压缩版

这一点很重要。

不要替换或破坏：

- `setup_logs/*.md`
- sandbox 的原始输出
- 生成出来的 Dockerfile 和 summary 文件

正确做法应该是：

- 原始输出继续留着，用于调试和复现
- 另外存一份压缩后的 prompt 版本

可以考虑新增这些文件：

- `workplace/.../agent_run_summary.json`
- `workplace/.../trajectory.json`

例如 `trajectory.json` 可以长这样：

```json
{
  "steps": [
    {
      "step_index": 3,
      "action": "pytest tests",
      "raw_observation_path": "setup_logs/3.md",
      "prompt_observation": "141 passed, 5 skipped, 17 warnings. No failures.",
      "reduction_method": "pytest_summary",
      "was_reduced": true
    }
  ]
}
```

## 7. 把 prompting 和 logging 彻底拆开

现在的 `Planner.history` 实际上同时承担了两件事：

- prompt 状态
- 运行记录

这两件事应该拆开。

建议分成三层：

- 面向 planner 的压缩上下文
- 运行期的结构化轨迹存储
- 原始 markdown / debug 日志

这样以后做压缩、复盘、评测都会轻松很多。

## 建议的实现阶段

## 阶段 1：不增加额外 LLM 成本

这是低风险、高回报的第一步。

1. 在 `agent.py` 中增加 `StepRecord` 支持
2. 增加只含确定性规则的 `TrajectoryReducer`
3. 改 planner 的输入构造，让它吃压缩 observation
4. 原始日志保持不变
5. 在 run summary 里记录压缩相关指标

这一阶段的成功标准：

- 平均 prompt tokens 下降
- `resolved` 不下降
- 小规模 smoke benchmark 上的平均 step 数不明显变差

## 阶段 2：重构 planner history

1. 停止把原始 observation 字符串直接塞进 `Planner.history`
2. 每轮根据结构化 step records 重新构造 planner messages
3. action 历史尽量原样保留，但旧 observation 可以压缩
4. 增加“不可压缩的固定事实”能力

这些 pinned facts 可能包括：

- 选中的 base image
- 已确认安装的重要系统包
- 当前已知失败命令
- 最终验证通过的测试命令
- platform override

## 阶段 3：可选的 reflection model

1. 增加一个和主 planner 分离的低成本 reducer model
2. 只在下面这些情况调用它：
   - 输出超过阈值
   - 规则 reducer 置信度低
   - 或者压缩率仍然不理想
3. 做 A/B 对比：
   - 不压缩
   - 仅规则压缩
   - 规则压缩 + reducer LLM

## 阶段 4：补上评测 harness

至少记录：

- 总 prompt tokens
- 总 completion tokens
- reducer 的 prompt / completion tokens
- 平均 step 数
- resolved rate
- 各类 step 的压缩比例
- 被压缩 step 的占比

加分项：

- 每步时延
- reducer 按命令类型的命中率

## 大概率会改到的文件

主要文件：

- `agent.py`
- `src/planner.py`
- `src/sandbox.py`
- `multi_docker_eval_adapter.py`

可能新增的文件：

- `src/trajectory_reducer.py`
- `src/trajectory_types.py`

后续可选：

- `scripts/eval_reducer_ablation.py`
- `doc/trajectory_reduction_experiment_plan.md`

## 几个很值得做的具体重构

### 重构 A：让 planner history 变成派生数据

不要再直接 mutate `Planner.history` 保存原始字符串，而是每轮从结构化 trajectory records 动态构造 prompt。

好处：

- 更容易压缩
- 更容易固定关键事实
- 更容易精确查看“模型到底看到了什么”

### 重构 B：增加 observation 分类

先把 observation 分类成：

- `test_output`
- `install_output`
- `file_listing`
- `file_snippet`
- `build_output`
- `generic`

这样 reducer 的选择就能变得确定、可解释、可调试。

### 重构 C：增加 pinned state

维护一个很小、但始终会进 prompt 的状态块：

- 当前 base image
- 工作目录
- 已确认的重要依赖
- 当前最佳测试命令
- 最新失败症状

这样能避免关键事实在压缩时被误删。

### 重构 D：对成功测试运行做激进摘要

这个仓库在配置环境时经常会反复跑测试。一旦测试成功，后续 prompt 往往只需要：

- 跑了哪条命令
- pass / fail 摘要
- 如果 warning 重要，再附少量 warning

这应该是最优先实现的 reducer 之一。

### 重构 E：对探索类输出做激进压缩

像 `ls`、`find`、`cat`、`sed -n` 这样的输出，在一两轮之后几乎不应该继续完整留在 prompt 里。

## 风险和保护措施

### 风险 1：把真正的失败原因压没了

缓解措施：

- 强保留强错误信号行
- 对未知输出保留头尾
- 固定保存“最新失败命令”和它的失败摘要

### 风险 2：丢掉精确命令或路径

缓解措施：

- 不要改写 action 命令本身
- 精确保留路径、包名、版本号、测试 ID

### 风险 3：reducer 成本比节省的还多

缓解措施：

- 第一阶段只做规则压缩
- 所有压缩都设阈值门槛
- 单独记录 reducer 的开销

### 风险 4：benchmark 成功率回退

缓解措施：

- 先在小规模 benchmark slice 上试
- 同时比较 step 数和 resolved rate
- 保留一个可以快速关闭 reduction 的开关

## 建议尽快补的指标

建议在 `agent_run_summary.json` 里加类似这些字段：

```json
{
  "planner_prompt_tokens_total": 0,
  "planner_completion_tokens_total": 0,
  "reducer_prompt_tokens_total": 0,
  "reducer_completion_tokens_total": 0,
  "observations_reduced": 0,
  "raw_observation_chars_total": 0,
  "reduced_observation_chars_total": 0
}
```

这样“有没有省”就会变成可量化事实，而不是主观感觉。

## 最值得先做的切片

如果只允许先做一件事，那应该是：

1. 确定性 observation reducer
2. 只处理较旧 observation
3. 不增加任何额外 LLM 调用

最先实现的 reducer，我建议优先覆盖：

- `pytest` 以及类似测试输出
- 包管理器输出
- 大型文件列表输出

这组组合大概率能以最小风险换来最大的节省。

## 现阶段不建议做的事

- 让主 planner 自己决定擦掉什么
- 用摘要替换原始日志
- 压缩最近一步
- 对所有命令输出只套一个通用 summarization prompt
- 在没有保留 takeaway 的情况下直接删掉旧 observation

## 一句话版总结

这篇论文给这个项目的最大启发，是要把 prompt 膨胀当成系统设计问题，而不是 prompt 文案问题。

最有希望的路线是：

- 增加结构化 step records
- 在 planner 外部压缩旧 observation
- 优先做确定性 reducer
- 只把便宜 LLM reducer 当作 fallback
- 把 token 节省和 resolved rate 一起评估

这很可能是这个仓库下一阶段性价比最高的优化方向。
