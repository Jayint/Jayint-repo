# 3 Method

本文研究的问题是：给定一个开源仓库及其目标提交，自动构建一个能够有效执行该仓库测试命令的 Docker 环境。方法整体采用“sandbox 探索、轨迹记录、Dockerfile 合成、fresh replay 修复”的闭环，而不是直接让 LLM 一次性生成 Dockerfile。

## 3.1 Problem Formulation

给定目标仓库实例：

$$
x = (R, c, m),
$$

其中 $R$ 是仓库，$c$ 是目标 commit，$m$ 是可选元信息。令 $R_c$ 表示 checkout 到 $c$ 后的仓库。环境状态记为 $S \in \mathcal{S}$，命令序列记为 $C \in \mathcal{C}$，执行命令产生状态转移：

$$
\delta: \mathcal{S} \times \mathcal{C} \rightarrow \mathcal{S},
\qquad S' = \delta(S, C).
$$

目标是在候选基础镜像集合 $\mathcal{B}$ 中选择基础镜像 $B^*$，并生成可重放 Dockerfile $D^*$，使 fresh Docker 环境能够通过验证函数 $\epsilon$：

$$
B^* = I(R_c, m), \qquad
\epsilon(R_c, \delta(B^*, P_{D^*}), V) = 1.
$$

其中 $P_{D^*}$ 是 Dockerfile 对应的构建命令序列，$V$ 是 sandbox 中真实执行并被证明有效的测试命令集合。对 Repo2Run 风格的 Python 仓库，$V$ 通常是 `pytest --collect-only -q --disable-warnings`，或 Poetry 项目中的 `poetry run pytest --collect-only -q --disable-warnings`。这里的成功不要求所有测试断言通过，而是要求测试框架能够真实启动并产生有效测试信号；参数错误、帮助文本、导入失败、collection error 或空测试运行都不算成功。

## 3.2 Workflow

系统包含五个阶段。首先，`ImageSelector` 根据仓库结构和配置文件选择基础镜像。随后，`Planner` 在 sandbox 中逐步执行环境配置命令，并把每一步的 `Thought`、`Action` 和 `Observation` 记录为轨迹。第三，系统从轨迹中抽取有效测试命令和成功的环境变更。第四，`Synthesizer` 将这些证据合成为 Dockerfile。最后，系统在 fresh Docker 环境中重新 build 并执行验证命令；如果失败，则由 repair agent 根据失败日志修复 Dockerfile 并再次验证。

## 3.3 Base Image Selection

`ImageSelector` 先读取仓库目录树和少量关键配置文件，例如 `pyproject.toml`、`setup.py`、`requirements.txt`、`package.json`、`Cargo.toml`、`go.mod`、`pom.xml`、CI 配置和版本文件。系统不会把完整仓库直接输入模型，而是先定位可能影响环境构建的文件，再让模型判断这些文件是否真正相关。

每种语言由对应的 handler 提供候选镜像集合和默认 setup hint，LLM 必须在候选集合中选择。如果发现平台兼容性风险，系统会记录 `linux/amd64` 等 platform override，并在后续 sandbox、build 和 run 阶段保持一致。

## 3.4 Sandbox Planning

仓库被复制到 sandbox 容器的 `/app` 后，`Planner` 以 ReAct 形式逐步探索环境构建过程：

```text
Thought: <reasoning>
Action: <one shell command>
```

每个 Action 都由 host executor 真实执行，Observation 只能来自 sandbox 返回，模型不能伪造命令输出。系统还会约束命令形态，避免把多个会改变环境的操作用 `&&` 或 `;` 混在一起，也禁止在 setup 或 test 命令后追加 `tail/head/grep` 这类会隐藏错误的输出截断器。

当某条会改变环境的命令失败时，系统不会自动 rollback，而是在 Observation 中提示 agent：该失败命令可能已经部分改变环境。如果其中某个前缀确实有用，agent 应该把成功前缀拆成新的独立 Action 重新执行；如果环境被污染，agent 可以显式请求 rollback。这使 agent 对当前环境状态保持一致认知，也为后续 Dockerfile 合成留下可重放证据。

## 3.5 Trajectory Recording and Compression

系统记录 sandbox 中的完整执行轨迹，包括成功命令、失败命令、命令输出、是否改变环境、是否为测试命令、测试是否有效执行等信息。对于过长的 Observation，系统会压缩日志，但压缩目标不是简单截断，而是保留 replay 相关信息，例如安装的包、版本约束、文件修改、服务启动、pytest collection 结果和第一个真实失败原因。

任务成功时，系统不会只相信 agent 的最终声明，而是检查最终回答前是否存在真实执行过的有效验证命令。只有测试命令在当前环境状态下产生了有效测试信号，才会被写入最终 verification bundle，并作为后续 Dockerfile replay 的验证目标。

## 3.6 Dockerfile Synthesis

`Synthesizer` 的输入包括两部分：一是压缩后的 setup trajectory，二是结构化的 `agent_run_summary.json`。它的目标是把 sandbox 中已经验证过的环境变更按原始顺序编译为 Dockerfile。

合成阶段采用 conservative replay 策略：成功的、可能持久改变环境的命令默认保留；只有能明确证明某条命令是只读探索、测试命令、runtime-only 服务命令或不可在 Docker build 中重放时，才将其排除。这样可以减少关键安装命令被错误删掉的风险。筛选出改变环境的命令的过程，如果简单地把setup trajectory和`agent_run_summary.json` 交给 LLM 使其生成最终 dockerfile，则当输入很长时，LLM 经常会漏掉一些关键指令，所以采用硬编码的方法。筛选指令的代码部分采用排除的方法，这里只排除掉只读指令、对环境不造成影响的指令，对于不确定是否会影响环境的成功指令，系统会保留，这可以弥补硬编码无法涵盖所有可能指令的缺点，做到真正还原agent在sandbox中的执行过程。

生成 build recipe 时，系统要求保持 agent trajectory 中 setup 命令的执行顺序，不擅自合并、重排或“优化”等价写法。对于混合命令，系统尽量提取已成功的 setup 前缀；对于输出截断器，系统会去除 `tail/head/grep` 等不影响环境但会误导日志的后缀。最终 recipe 被转换为 Dockerfile，并与验证命令一起进入 fresh replay 阶段。

## 3.7 Fresh Replay and Repair

Sandbox 成功不等于最终成功，因为交互式环境和 fresh Docker build 之间可能存在差别，例如命令遗漏、顺序变化、文件 patch 被覆盖、runtime service 被写入 build layer，或 Dockerfile 语法不合法。

如果 build 或验证失败，repair agent 会接收当前 Dockerfile、sandbox 轨迹摘要、结构化 recipe、测试命令以及 build/test 日志，然后输出修复后的完整 Dockerfile。Repair agent 只能修改 Dockerfile，不能修改目标仓库源码；它优先恢复轨迹中被遗漏的成功 setup 命令，并保持原始执行顺序。这个阶段属于方法本身，用于把 sandbox 中的成功状态校正为可复现的 Dockerfile，而不是实验调试步骤。

如果 Docker build 成功，则认为 Dockerfile generation 成功。若容器内测试命令能够有效执行，则认为 executable environment 构建成功。

当 build 或测试执行失败时，系统调用一个受限 Dockerfile repair agent。Repair agent 接收完整 Dockerfile、`agent_run_summary`、build stderr/stdout、test stdout/stderr、测试命令和仓库元信息，并输出一个完整替换版 Dockerfile。它受到以下约束：

1. 只能修复 Dockerfile，不能在 Dockerfile 之外修改目标仓库源码；
2. 不允许发明全新的 setup 策略，除非轨迹证据不足；
3. 优先恢复被遗漏的成功 setup 命令；
4. 必须保持 `agent_run_summary.build_recipe.build_commands` 中的原始顺序；
5. 不得为了通过测试而在 Dockerfile 末尾加入最终测试命令；
6. 若 Dockerfile 中需要保留文件 patch 或 stub，必须来自 sandbox 轨迹中的成功证据，不得用“等价但未观察过”的自定义实现替换原始成功 patch；
7. 保持 base image 和 COPY 语义，除非失败直接证明必须修改。

即使 sandbox 中已经找到可运行环境，将交互式成功轨迹编译成 Dockerfile 时仍可能产生信息丢失或顺序偏移。Repair agent 利用 fresh replay 的失败反馈，把 Dockerfile 拉回到 sandbox 证据支持的轨迹上。

## 3.8 Output

系统最终输出：

$$
y = (B^*, D^*, V, \Sigma),
$$

其中 $B^*$ 是基础镜像，$D^*$ 是通过 fresh replay 验证的 Dockerfile，$V$ 是有效测试命令集合，$\Sigma$ 是结构化运行摘要。若 replay 成功，说明生成的 Dockerfile 能在干净环境中复现 sandbox 中发现的可执行环境；若失败，日志会记录失败发生在 clone、sandbox setup、Docker build、测试执行或 repair 阶段，便于后续分析。
