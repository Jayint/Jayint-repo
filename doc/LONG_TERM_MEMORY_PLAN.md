# 长期记忆

## 目标

给环境配置 agent 增加一个长期记忆机制，用来保存“多次失败后最终被解决的问题”，并在后续失败时按需检索这些经验。

## 总体思路

在 run 结束后生成长期记忆。

只在命令失败后，由 agent 显式决定是否检索长期记忆。

## 记忆生成

### 输入

本次 setup 过程的最后一个 `setup_logs/*.md`

本次 setup 的 `agent_run_summary.json`

只在 run 最终成功时尝试生成记忆。未成功的 run 不写入长期记忆。

### 生成方式

用 LLM 分析整份 setup log

识别：
- 过程中出现了哪些主要问题
- 哪些问题反复出现，或经过了多次无效尝试
- 哪些问题最终解决
- 每个问题是如何解决的
- 哪些做法属于错误的做法

### 写入条件

只把满足下面条件的问题写入长期记忆：

问题必须经历过反复失败或多次无效尝试，最后才被解决。

只失败一次、且可直接从失败 observation 判断修复方式的问题不写入长期记忆。

例如 `composer install` 报错缺少 `git`，随后直接安装 `git` 并成功，这类问题通常不需要查记忆库，不作为长期记忆。

能明确总结出根因

能明确总结出有效修复方式

有基本验证证据，主要用于人工审核这条记忆是否可靠。

写入目标是记录“普通 observation 不足以直接解决，需要探索后才得到的可复用经验”，而不是记录所有成功修复。

### 记忆生成 Prompt

System Prompt：

```text
你是一个环境配置长期记忆提取器。

你的任务不是总结整个 setup 过程，而是从一次已经成功结束的环境配置轨迹中，提取值得写入长期记忆的“困难问题解决经验”。

长期记忆只记录这类问题：
- 问题经历过反复失败，或经历过多次无效/误导性的尝试。
- 最终找到明确有效的解决方法。
- 有可审计的验证证据。
- 经验对未来相似环境配置任务有复用价值。

不要记录这类问题：
- 只失败一次，且从失败 observation 就能直接判断修复方式的问题。
- 常规依赖缺失后直接安装成功的问题。
- 只靠简单错误信息即可解决的问题。
- 未最终解决的问题。
- 没有明确根因的问题。
- 没有验证证据的问题。
- 只是网络瞬时失败后重试成功，且没有形成稳定解决策略的问题。
- 来自模型臆测、计划或自我生成的 Observation，而不是系统真实执行结果的问题。

重要判断标准：
如果不查长期记忆，agent 也能直接根据最近一次失败 observation 纠正，那么不要写入长期记忆。
只有当失败过程显示 agent 经过了明显探索、误判、无效 workaround，最后才形成有效解决方案时，才写入长期记忆。

verification 字段主要用于人工审核，记录这条记忆为什么可信。它不是运行时成功证明。
anti_patterns 字段很重要，必须记录探索过程中已经证明无效、误导或不应重复的做法。

输出必须是严格 JSON 数组。
如果没有值得写入长期记忆的问题，输出 []。
不要输出 Markdown。
不要输出解释文字。
```

User Prompt 模板：

```text
请从下面这次环境配置 run 中提取长期记忆候选。

仓库：
{repo_name}

本次 run 是否成功：
{configuration_success}

最终验证命令：
{verified_test_commands}

已知本地服务依赖：
{required_local_services}

agent_run_summary.json：
{agent_run_summary_json}

最后一个 setup log：
{final_setup_log_md}

请只输出严格 JSON 数组。每个元素必须符合下面结构：

[
  {
    "scope": "global | ecosystem | repo",
    "repo": "repo_name 或 null",
    "problem_signature": "一句话概括可复用的问题模式",
    "symptoms": [
      "真实 observation 中出现过的关键错误信号或失败现象"
    ],
    "root_cause": "最终确认或高度可信的根因",
    "successful_fix": [
      "最终有效的修复动作，尽量写成可迁移的步骤"
    ],
    "verification": [
      "用于人工审核的证据，例如哪个命令成功、哪个测试通过"
    ],
    "anti_patterns": [
      "本次探索中证明无效、误导或不应重复的做法"
    ],
    "embedding_text": "用于向量检索的一段紧凑文本，包含问题、症状、根因、修复方式和生态信息",
    "linked_memories": [],
    "created_at": "{created_at}"
  }
]
```

## 记忆结构

每条长期记忆保存这些字段：

```json
{
  "scope": "global | ecosystem | repo",
  "repo": "repo_name",
  "problem_signature": "...",
  "symptoms": [],
  "root_cause": "...",
  "successful_fix": [],
  "verification": [],
  "anti_patterns": [],
  "embedding_text": "...",
  "linked_memories": [],
  "created_at": "..."
}
```

说明：

- `embedding_text` 只用于向量检索
- `linked_memories` 只记录相似记忆的内部引用
- `verification` 主要用于人工审核，记录该修复后来被什么测试或命令验证过
- `anti_patterns` 记录探索中证明无效、误导或不应重复的做法

`scope` 的含义：

- `global`：跨语言、跨生态通用的问题
- `ecosystem`：语言或工具生态相关的问题
- `repo`：只对特定仓库或特定配置有效的问题

## 存储方式

第一版不引入 SQLite、Chroma、Qdrant、Faiss 等向量数据库。

记忆先保存在一个本地 JSONL 文件中：

```text
memory/
  long_term_memories.jsonl
```

每行是一条长期记忆。LLM 只负责生成上面的记忆结构，不生成 embedding。

系统写入记忆时，根据 `embedding_text` 计算 embedding，并把它作为内部字段一起写入 JSONL：

```json
{
  "scope": "ecosystem",
  "repo": "pallets-eco/flask-wtf",
  "problem_signature": "...",
  "symptoms": [],
  "root_cause": "...",
  "successful_fix": [],
  "verification": [],
  "anti_patterns": [],
  "embedding_text": "...",
  "linked_memories": [],
  "created_at": "...",
  "embedding": [0.012, -0.034]
}
```

`embedding` 是系统内部字段，不暴露给 LLM 生成，也不作为论文里的记忆语义结构。

检索时直接全量读取 `long_term_memories.jsonl`，用当前失败命令和失败 observation 构造 query，计算 query embedding，然后与每条记忆的 `embedding` 做 cosine similarity，返回 top-k 结果。

这样做的原因是第一版记忆规模预计较小，JSONL 全量检索足够快，而且最容易调试、人工审核和复现实验。等记忆库扩大到几千条以上、检索成为瓶颈时，再把 `memory_manager.py` 的存储层替换成 SQLite/Faiss/Qdrant，不改变 agent 的调用接口。

## Embedding 模型选择

第一版默认使用 `BAAI/bge-base-en-v1.5` 计算长期记忆和检索 query 的 embedding。

选择原因：

- 要做 embedding 的 `embedding_text` 本身会写成英文技术摘要，主要包含命令、报错、依赖、服务名、根因和修复动作，因此不需要优先选择多语言模型。
- 记忆检索失败的代价较高，错误检索可能误导 agent 继续尝试无效方案；相比 `bge-small-en-v1.5`，`bge-base-en-v1.5` 更适合作为默认主实现。
- 长期记忆检索只在命令失败后由 agent 主动触发，调用频率低，不是主要性能瓶颈，因此没必要为了极致速度牺牲默认检索质量。
- 不直接采用 A-Mem 中的 `all-MiniLM-L6-v2` 作为默认模型；它可以作为论文实验中的轻量 baseline，用来说明不同 embedding 模型对记忆检索效果的影响。

实现参数：

```text
embedding_model = BAAI/bge-base-en-v1.5
query_prefix = "Represent this sentence for searching relevant passages: "
passage_prefix = ""
normalize_embeddings = true
retrieval_top_k = 5
```

候选消融设置：

- `sentence-transformers/all-MiniLM-L6-v2`：对齐 A-Mem 的轻量 baseline
- `BAAI/bge-small-en-v1.5`：更快、更轻的英文检索模型
- `BAAI/bge-large-en-v1.5`：更强但更重的英文检索模型
- `BAAI/bge-m3`：支持多语言和更长文本的强模型，适合作为上限对照，但第一版不作为默认实现

#### 示例

```json
[
  {
    "scope": "ecosystem",
    "repo": "pallets-eco/flask-wtf",
    "problem_signature": "旧 Python setup.py 项目在较新 setuptools 下安装失败，报 canonicalize_version() got an unexpected keyword argument 'strip_trailing_zero'，切换 editable/non-editable 安装方式仍无效时，应优先考虑 setuptools 版本兼容问题。",
    "symptoms": [
      "pip install -e . -r requirements/tests.txt 失败，python setup.py develop did not run successfully",
      "错误栈中出现 TypeError: canonicalize_version() got an unexpected keyword argument 'strip_trailing_zero'",
      "改用 pip install . -r requirements/tests.txt 后仍然 metadata-generation-failed",
      "失败集中在 setuptools/_core_metadata.py 和 setup.py metadata/develop 阶段"
    ],
    "root_cause": "旧式 setup.py / setup.cfg 项目的元数据生成流程与当前较新的 setuptools/packaging 组合不兼容；问题不在依赖包缺失，也不是 editable 模式本身。",
    "successful_fix": [
      "先安装测试依赖时显式降级 setuptools，例如 pip install 'setuptools<67' -r requirements/tests.txt",
      "确认 setuptools 已降级后，再运行 pip install -e . 安装当前项目",
      "最后运行项目原生 pytest 命令验证环境"
    ],
    "verification": [
      "pip install 'setuptools<67' -r requirements/tests.txt 成功，并将 setuptools 79.0.1 降到 66.1.1",
      "pip install -e . 随后成功安装 Flask-WTF-1.0.0",
      "pytest -v --tb=short 输出 48 passed, 3 skipped"
    ],
    "anti_patterns": [
      "不要只在 pip install -e . 和 pip install . 之间反复切换；如果错误仍在 metadata/develop 阶段，应判断为构建工具链兼容性问题",
      "不要把 setuptools 的 deprecation warning 当成根因，真正关键错误是 canonicalize_version 的参数不兼容",
      "不要盲目升级 pip/setuptools；对旧项目有时需要降级 setuptools"
    ],
    "embedding_text": "Python packaging old setup.py project setuptools compatibility canonicalize_version strip_trailing_zero TypeError metadata-generation-failed setup.py develop failed. If editable and non-editable pip install both fail in setuptools core_metadata, downgrade setuptools such as setuptools<67, install test requirements, then pip install -e . and run pytest.",
    "linked_memories": [],
    "created_at": "2026-04-14T01:34:40+08:00"
  }
]

```



## 连边

- 新记忆写入时，系统根据 `embedding_text` 计算 embedding
- 用新记忆的 embedding 与 JSONL 中已有记忆做相似度检索
- 取 top-k 相似记忆
- 相似度超过阈值时写入 `linked_memories`

## 检索

### 触发方式

检索只在命令失败后发生，并且由 agent 显式触发：

```text
Action: __RETRIEVE_MEMORY__
```

### 检索输入

系统自动从最近一次失败构造 query，包含：

- 失败命令
- 失败 observation
- 错误签名
- 仓库名
- 当前已知服务依赖

### 检索输出

返回 top 几条相关 lesson 的短摘要，例如：

```text
Retrieved Lessons:
1. 问题：...
   信号：...
   根因：...
   修复：...
   避免：...
```

检索结果只是建议，不能替代实际命令执行和测试验证。

## 接入位置

### `agent.py`

负责：

- 识别 `Action: __RETRIEVE_MEMORY__`
- 调用 memory manager 检索长期记忆
- 把检索结果注入下一轮 planner
- 在 run 结束后读取最后一个 `setup_log`
- 调用 memory manager 生成并写入长期记忆

### `src/memory_manager.py`

负责：

- 长期记忆读写
- embedding 计算
- 相似度检索
- 连边
- 基于最终 setup log 生成 lesson

### `src/planner.py`

只保留一条稳定机制：

- 最近一次命令失败后，agent 可以显式请求 `__RETRIEVE_MEMORY__`

## 第一版实现顺序

1. 定义长期记忆的数据结构
2. 新增 `memory_manager.py`
3. 实现 `memory/long_term_memories.jsonl` 的读写
4. 增加 `Action: __RETRIEVE_MEMORY__`
5. 实现基于 `embedding_text` 的 embedding 计算和 JSONL 全量 top-k 检索
6. 把检索结果注入下一轮 planner
7. 在 run 结束后基于最后一个 `setup_log` 生成长期记忆
8. 为新记忆建立 embedding 连边
