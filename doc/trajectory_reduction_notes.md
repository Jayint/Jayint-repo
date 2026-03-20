#### 1. 对 Observation 做摘要

在每一次执行完操作后，对于 bundle install、pytest -v 这类会输出超长结果的指令，需要及时地做摘要。

#### 2. AgentDiet 式 reflection

每次压缩第 s - a 步（现在是第 s 步），a = 2

具体做法是把 [s - b - a, s]（b = 1） 这些步骤交给压缩 llm，让它进行压缩。如果压缩前后 token 差距小于 $\theta$  则不压缩。

#### 3. 把 “状态” 从 “历史” 剥出来

Agent 在执行中已经维护了不少结构化信息，但是没有回灌给 Planer

可以单独为维护一个 state summary，包括：当前base image/platform、最近一次成功的环境变更 、当前怀疑的缺依赖、最后一个有效的测试命令、当前验证块状态等

#### 4. Prompt 只放压缩视图

因为原先就会记录 setup_logs/image_selector_logs

所以发给 Planner 的可以只是压缩后的版本



#### 压缩时机：

- 每轮执行完命令后，先把 raw observation 存起来
- 如果不是长 observation，直接 observation_prompt = observation_raw
- 如果是长 observation，并且满足 a=2 的延迟策略，就对旧 step 的 <result> 做压缩
- 最新两步不压

## 压缩规则

#### 安装日志压缩规则

保留：

- 包管理器类型：apt / pip / npm / bundle / cargo / go
- 成功安装的包名列表
- 已存在/已满足依赖列表
- 关键版本信息
- warning
- 第一处真实错误和错误上下文

压缩掉：

- 下载进度条
- 重复编译输出
- 大段镜像源拉取日志
- 重复的 wheel/build 细节

```json
[pip install summary]
Successfully installed:
- pytest==9.0.2
- pluggy==1.6.0
Already satisfied:
- setuptools==68.2.2
Warnings:
- Running pip as root user
```



#### 测试日志压缩规则

保留测试命令、总测试数、失败数、失败用例名、关键 traceback

把长串通过用例替换成一句话占位说明，如下图：

![d752423b404966e380587a934c64e4f3](/Users/panjianying/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_x25htryviibz22_f7d3/temp/RWTemp/2026-03/d752423b404966e380587a934c64e4f3.png)

保留：test session header、平台/解释器/版本、collected N items、short test summary info、失败/错误/XFAIL 相关条目、最终统计行，如”73 passed、1 xfailed in 4.48s“

压缩掉：大段逐条 PASSED，将其替换成类似“ ... (individual test lines omitted; mostly PASSED)”的一句话占位说明。

也就是说，测试日志压缩不是“摘要成一句话”，而是“保留骨架 + 保留异常 + 用占位符替换冗长通过项”。



#### 回滚失败日志压缩规则

因为失败后环境已回滚，所以完整输出通常没必要长期保留

只保留“失败指令+失败原因摘要”即可。



## 注意

压缩的话，不压缩 Thought 和 Action，这两个不长，只压缩 Observation

测试日志的压缩示例如下：。。。

安装日志的压缩规则改一下，已安装成功包列表不应该被删掉，也要保留，避免LLM重复安装

Agent完成后统计一下总的token消耗、包括构建过程和压缩的时候用到的

step 数据结构应该长什么样？

quickstart消耗的token不要计入，有关quickstart的都先不要加了，后续可能要删掉quickstart这个模块。

每压缩一次都要重新build 一次 planner_history吗？那也太低效了吧。

不用计算任何 cost，你不用帮我算钱，只需要计算token数就好。

#### 压缩的核心目标

- 先把超长 observation 当场瘦身
- 再用单独 reflection module 逐步压旧 step
- 同时把关键环境事实抽成结构化 state memory
- 保留磁盘上的原始日志，只把压缩视图送进 Planner





1. ObservationAnalyzer.analyze(step) 是干什么？怎么做的？
2. 搞那么麻烦干嘛？没必要对observation进行分类，直接统一的交给LLM压缩就好了，把几种observation的压缩规则统一写到一份prompt里即可

#### 统一 prompt 应该长什么样

我建议统一 prompt 明确写死这些规则：

##### 全局规则

- 你只能改写 <result> 内容
- 不得改 <think>
- 不得改 <call>
- 不得改 step 的 XML 结构
- 尽量保留原有顺序和格式骨架
- 删除的内容用短占位说明替代，不要直接掏空

##### 测试日志规则

- 保留：
    - test session header
    - 平台/版本信息
    - collected N items
    - short test summary info
    - failing/xfailed/error 用例
    - traceback 关键段
    - 最终统计行
- 压缩：
    - 大段连续 PASSED
- 替换示例：
    - ... (individual test lines omitted; mostly PASSED)

##### 安装日志规则

- 必须保留：
    - 成功安装的包列表
    - already satisfied / already installed 的包列表
    - 关键版本信息
    - warning
    - 第一处真实错误
- 可压缩：
    - 下载进度
    - 编译噪音
    - 重复拉取日志

##### 其他日志规则

- 保留影响下一步决策的事实
- 删掉长噪音
- 对删掉的部分写一句短 takeaway

这就够了，不需要先分类再分别走 prompt。



## Codex最终敲定实现

创建三个类：

1. AgentStep
2. ObservationCompresessor
3. RunTokenLedger

```C++
@dataclass
class CompressionRecord:
    eligible: bool = False
    applied: bool = False
    model: str | None = None
    reason: str | None = None

    original_chars: int = 0
    reduced_chars: int = 0
    original_tokens_est: int = 0
    reduced_tokens_est: int = 0
    saved_tokens_est: int = 0

    reflect_input_tokens: int = 0
    reflect_output_tokens: int = 0
    reflect_total_tokens: int = 0
    reflect_cost: float = 0.0


@dataclass
class StepTokenUsage:
    planner_input_tokens: int = 0
    planner_output_tokens: int = 0
    planner_cost: float = 0.0

    reflect_input_tokens: int = 0
    reflect_output_tokens: int = 0
    reflect_cost: float = 0.0


@dataclass
class AgentStep:
    step_id: int
    thought: str
    action: str

    success: bool
    exit_code: int | None
    mutates_environment: bool
    env_revision_before: int
    env_revision_after: int

    observation_raw: str
    observation_prompt: str

    metadata: dict[str, Any]
    compression: CompressionRecord
    token_usage: StepTokenUsage

```

#### 压缩流程

1. Planner.plan()
2. Sandbox.execute()
3. 创建 AgentStep
4. 保存 raw observation
5. 如果当前到达 s，检查 s-a 的 step 是否够长
6. 若够长，统一走一次 compress_observation(...)
7. 如果节省收益足够大，就替换 observation_prompt
8. 下一轮 Planner 只看到 observation_prompt

#### **压缩器接口**

我建议就长这样：

```python
class ObservationCompressor:
    def compress(
        self,
        target_step: AgentStep,
        context_steps: list[AgentStep],
        model: str,
    ) -> tuple[str, CompressionRecord]:
        ...

```

输入：

- 要压缩的旧 step
- 局部窗口上下文
- 当前和 setup 一样的 model

输出：

- 压缩后的 <result> 内容
- 压缩元数据

#### **局部窗口序列化**

保持 AgentDiet 风格，但我们只允许改 <result>：

```bash
<step id="17">
<think>...</think>
<call tool="bash">...</call>
<result>
...raw observation...
</result>
</step>

```

压缩时只给最近窗口，比如：

- s-3
- s-2 目标
- s-1
- s

#### **压缩条件**

```python
eligible = (
    step.step_id <= current_step_id - a
    and len(step.observation_raw) >= compress_threshold_chars
    and not step.compression.applied
)
```

#### Prompt

```markdown
[SYSTEM]

You are a trajectory compression module for an environment-setup coding agent.

Your job is to compress a single old step in the agent trajectory by rewriting ONLY the text inside the <result>...</result> block of the target step.

The agent is trying to set up a runnable/testable software environment inside Docker.
The compressed result will be shown to the agent in later turns, so you must preserve any information that could affect later decisions.

You must follow these rules strictly:

1. You may only rewrite the content inside the <result> block of the TARGET step.
2. You must NOT change:
- <think> ... </think>
- <call ...> ... </call>
- XML tags
- step ids
- command text
3. Keep the overall structure unchanged.
4. Do not invent facts that do not appear in the original result.
5. If compression is unsafe, return the original target step unchanged.
6. Prefer replacing low-value repetitive text with short placeholders rather than deleting content silently.
7. Preserve exact package names, test names, file paths, versions, and error messages whenever they may matter later.

The trajectory may contain three common kinds of waste:

- Useless information:
  very long repetitive output that does not change later decisions
  examples: long download progress, many passed test lines, repeated build progress lines

- Redundant information:
  information repeated many times in the same result
  examples: repeated package download logs, repeated “PASSED” lines, repeated compiler progress

- Expired information:
  local details that are no longer useful except for their takeaway
  examples: a long search result where only one candidate mattered, a huge file dump where only one conclusion matters

Important preservation rules:

A. If the result is a TEST LOG:
You should preserve:
- the test session header if present
- platform/runtime/version info if present
- collected test counts
- short test summary info
- failing/error/xfail test cases
- traceback or assertion message that explains failure
- the final summary line such as “73 passed, 1 failed in 4.48s”
You may compress:
- long runs of individual PASSED lines
Use placeholders like:
- ... (individual test lines omitted; mostly PASSED)

B. If the result is an INSTALL LOG:
You must preserve:
- package manager identity if clear from the output
- successfully installed package names and versions
- already satisfied / already installed package names and versions
- key warnings
- the first real error and its nearby context
You may compress:
- download progress bars
- repeated fetch/build lines
- verbose wheel/build noise
Do NOT remove successful or already-present package lists, because the agent may otherwise reinstall them later.

C. If the result is a BUILD / GENERAL COMMAND LOG:
You should preserve:
- whether the command succeeded or failed
- key discovered files/paths if relevant
- key build artifacts if relevant
- first real error and the most informative nearby lines
You may compress:
- repetitive build progress
- repeated informational lines
- large irrelevant blocks that can be replaced by a short takeaway

Compression style:
- Be conservative.
- Preserve important lines verbatim when needed.
- Replace long repetitive spans with one short bracketed or parenthetical note.
- Keep the compressed result readable by the next agent step.
- Do not turn everything into a vague summary.
- The result should still look like a command output, just shorter.

Output format:
Return ONLY the full rewritten TARGET step, with the same <step>, <think>, <call>, and <result> tags.
Do not return explanations outside the XML.

[USER]

You are given a sliding window of agent steps in XML.
Compress ONLY the TARGET step by rewriting ONLY its <result> block.

TARGET_STEP_ID: {target_step_id}

Window context:
{serialized_window}

Return only the rewritten TARGET step.


```

Serialized_window 形式：

```xml
<trajectory>
  <step id="15">
    <think>...</think>
    <call tool="bash">...</call>
    <result>...</result>
  </step>

  <step id="16" target="true">
    <think>...</think>
    <call tool="bash">...</call>
    <result>
      ... very long raw observation ...
    </result>
  </step>

  <step id="17">
    <think>...</think>
    <call tool="bash">...</call>
    <result>...</result>
  </step>

  <step id="18">
    <think>...</think>
    <call tool="bash">...</call>
    <result>...</result>
  </step>
</trajectory>

```

目前，$\theta = 1500$，当压缩后差别 > 300 时才压缩，A = 2， B = 2。
