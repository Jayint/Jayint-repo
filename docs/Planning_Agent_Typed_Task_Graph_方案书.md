# Planning Agent 方案书：基于 Typed Task Graph 的仓库环境预规划模块

版本：v1.0  
目标读者：Codex / 开发者 / 论文方法设计  
目标系统：在 Build Agent 真正修改 sandbox 之前，用只读方式理解 repo，生成结构化环境任务图，并由程序编译成可执行 todo-list。

---

## 1. 背景与核心问题

自动构建 repository 可运行环境时，Build Agent 如果一开始就直接在 sandbox 中执行 `pip install .`、`npm install`、`pytest` 等命令，容易出现三个问题。

第一，探索盲目。Agent 不知道 repo 使用什么语言版本、包管理器、测试框架、服务依赖和配置变量，只能依赖报错进行试错。

第二，环境污染。错误的安装命令可能修改依赖版本、写入缓存、生成中间文件，后续 rollback 或 Dockerfile synthesis 都会变复杂。

第三，可解释性弱。最终失败时，很难判断失败发生在 runtime、system package、language dependency、service、env var、build step 还是 verification 阶段。

本方案引入一个 Planning Agent。它在 Build Agent 真正构建环境前，只做 read-only exploration，把 repo 运行需要的环境需求整理成 Typed Task Graph，然后由程序对 graph 做校验和拓扑排序，生成 ordered todo-list。

核心分工是：

```text
LLM / Planning Agent：理解 repo，抽取 typed nodes 和 typed edges
程序：校验 graph，检测环，对 hard edges 做拓扑排序
Build Agent：根据 ordered todo-list 在 sandbox 中执行，并根据失败动态修正
```

---

## 2. 设计目标与非目标

### 2.1 设计目标

1. 在不修改环境的前提下，尽量准确识别 repo 的环境需求。
2. 将环境需求组织为 typed graph，而不是普通自然语言计划。
3. 用程序保证执行顺序满足 graph 约束，避免 LLM 直接排序带来的幻觉。
4. 输出 Build Agent 可消费的 ordered todo-list、risk notes 和 fallback plan。
5. 让失败更可解释：失败可以定位到具体节点类型或具体 graph edge。

### 2.2 非目标

1. Planning Agent 不负责安装依赖。
2. Planning Agent 不负责运行测试。
3. Planning Agent 不负责启动服务。
4. Planning Agent 不保证第一次计划完全正确；它输出的是初始地图，可以被 Build Agent 动态修正。
5. 本 v1 版本不追求覆盖所有语言和所有复杂部署，优先支持 Python repo。

---

## 3. 系统总体架构

```text
Repository
   ↓
Read-only Exploration
   ↓
Planning Agent / LLM
   ↓
Typed Task Graph JSON
   ↓
Graph Validator
   ↓
Topological Sorter
   ↓
Ordered Todo-list + Risk Notes + Fallback Plan
   ↓
Build Agent
   ↓
Sandbox Execution
   ↓
Execution Feedback / Graph Update
```

模块说明：

- `RepoScanner`：扫描仓库结构，收集 README、CI、Dockerfile、manifest、lockfile、tests 等证据。
- `PlanningAgent`：调用 LLM，根据 repo evidence 生成 typed task graph。
- `GraphValidator`：检查 nodes、edges、edge strength、重复 node、无效 edge、hard-edge cycle。
- `TopologicalSorter`：对 hard edges 做确定性拓扑排序，并用 node type priority 处理并列节点。
- `TodoListGenerator`：把排序后的节点转换为 Build Agent 可执行的 todo items。
- `BuildAgent`：按 todo-list 在 sandbox 中执行，失败时读取 fallback_plan 或请求 Planning Agent 修正 graph。

---

## 4. Read-only Exploration 规则

Planning Agent 的探索阶段只允许读取，不允许修改环境。

### 4.1 允许命令

```bash
ls
find
cat
sed -n
head
tail
grep
rg
tree
python -c "只做文件解析，不安装包，不联网，不写文件"
```

### 4.2 禁止命令

```bash
pip install
npm install
apt-get install
poetry install
mvn install
cargo build
pytest
docker run
service start
python app.py
npm start
```

### 4.3 优先读取的文件

```text
README / docs
Dockerfile
docker-compose.yml
.github/workflows/*.yml
requirements.txt / requirements-test.txt
pyproject.toml / poetry.lock
setup.py / setup.cfg / tox.ini / pytest.ini
package.json / package-lock.json / pnpm-lock.yaml / yarn.lock
pom.xml / build.gradle
Cargo.toml / Cargo.lock
go.mod / go.sum
Makefile
tests/
.env.example
```

---

## 5. EnvironmentBuildPlan 输出格式

Planning Agent 的最终输出是 `EnvironmentBuildPlan`。

```json
{
  "repo_summary": {},
  "typed_task_graph": {
    "nodes": [],
    "edges": []
  },
  "ordered_todo_list": [],
  "risk_notes": [],
  "fallback_plan": [],
  "unresolved_questions": []
}
```

字段含义：

- `repo_summary`：仓库语言、包管理器、测试框架、候选 base image、运行入口。
- `typed_task_graph.nodes`：环境任务或资源节点。
- `typed_task_graph.edges`：任务之间的依赖关系。
- `ordered_todo_list`：程序拓扑排序后生成的执行计划。
- `risk_notes`：执行中需要注意的风险。
- `fallback_plan`：soft edges 或经验性依赖对应的失败修复建议。
- `unresolved_questions`：只读分析无法判断的问题。

---

## 6. Typed Task Graph 数据模型

### 6.1 Node schema

```json
{
  "id": "python:3.11-slim",
  "type": "runtime",
  "label": "Python runtime",
  "command_hint": "FROM python:3.11-slim",
  "evidence": [".github/workflows/test.yml"],
  "confidence": 0.9,
  "scope": "test",
  "metadata": {}
}
```

必填字段：

- `id`：唯一标识，不能重复。
- `type`：节点类型。
- `evidence`：证据来源。
- `confidence`：置信度，范围 0 到 1。

可选字段：

- `label`：人类可读名称。
- `command_hint`：建议命令，不要求 Build Agent 逐字执行。
- `scope`：例如 runtime、test、deploy、optional。
- `metadata`：额外信息。

### 6.2 Edge schema

```json
{
  "from": "requirements-test.txt",
  "to": "pytest --collect-only -q",
  "type": "test_dependency_before_verify",
  "strength": "hard",
  "evidence": [".github/workflows/test.yml"],
  "confidence": 0.95
}
```

必填字段：

- `from`：源节点 id。
- `to`：目标节点 id。
- `type`：边类型。
- `strength`：`hard` 或 `soft`。

---

## 7. 节点类型定义

| 节点类型 | 含义 | 例子 |
|---|---|---|
| `runtime` | 基础运行时、语言版本、base image、硬件环境 | `python:3.11-slim`, `node:20`, `openjdk:17`, `cuda:11.8` |
| `package_manager` | 依赖安装工具 | `pip`, `poetry`, `npm`, `maven`, `cargo` |
| `language_dependency` | 语言层面的依赖文件或依赖包 | `requirements.txt`, `pytest`, `Flask`, `package.json` |
| `system_package` | OS 层面的系统库、编译工具 | `build-essential`, `libpq-dev`, `libxml2-dev`, `ffmpeg` |
| `project_build` | 项目安装、编译或源码树执行方式 | `poetry install`, `pip install -e .`, `npm run build`, `source-tree execution` |
| `service` | 运行时或测试时需要启动的服务 | `Redis`, `Postgres`, `MongoDB`, `Selenium` |
| `env_var` | 环境变量、配置变量、secret | `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY` |
| `asset` | 数据、模型、浏览器、fixture 等资源 | `chromium`, `NLTK data`, `sample.db` |
| `runtime_command` | 项目运行命令 | `python app.py`, `npm start`, `python proxyPool.py server` |
| `verification` | 验证命令 | `pytest --collect-only -q`, `pytest -q`, `cargo test --no-run` |

### 7.1 Runtime 节点

Runtime 节点表示 repo 运行所依赖的基础执行环境。它不是具体的 Python 包，而是更底层的语言版本、系统版本或硬件环境。

例如：

```json
{
  "id": "python>=3.10",
  "type": "runtime",
  "evidence": ["pyproject.toml"],
  "confidence": 0.9
}
```

Runtime 节点解决的问题是：Build Agent 一开始应该选择什么 base image 或 toolchain。如果这个底座选错，后续依赖安装会出现大量无意义失败。

### 7.2 Package Manager 节点

Package Manager 节点表示依赖应该通过什么工具安装。

例如看到 `poetry.lock` 时，系统应该优先使用 Poetry，而不是直接绕过 lockfile 用 pip 安装。	

### 7.3 Language Dependency 节点

Language Dependency 节点表示语言层面的依赖，既可以表示一个依赖文件，也可以表示关键依赖包。

建议优先把依赖文件作为节点，例如 `requirements.txt`、`requirements-test.txt`、`pyproject.toml`，而不是一开始把每个包都展开为节点。只有当某个包会影响系统依赖、服务依赖或失败诊断时，再单独建节点。

### 7.4 System Package 节点

System Package 节点表示操作系统层面的依赖。许多语言依赖安装失败不是因为缺 Python 包，而是缺系统库或编译器。

例如：

```text
pip install psycopg2 可能需要 libpq-dev
pip install lxml 可能需要 libxml2-dev / libxslt1-dev
pip install opencv-python 可能需要 libgl1
```

### 7.5 Project Build 节点

Project Build 节点表示项目自身如何安装、编译或准备。如果 CI 或 tox 表明项目不需要安装成 package，可以用 `source-tree execution` 表示源码树直接执行。

### 7.6 Service 节点

Service 节点表示实际运行或集成测试所需的外部服务。注意区分客户端库和服务本身。例如 `redis` Python package 是 language dependency，而 Redis server 是 service。

### 7.7 Env Var / Secret 节点

Env Var 节点表示环境变量或 secret。如果需要真实 secret，应标记到 `unresolved_questions`，不要让 Build Agent 盲目尝试。

### 7.8 Data / Asset 节点

Asset 节点表示数据、模型、浏览器 binary、fixture 等非 package 资源。

### 7.9 Runtime Command 节点

Runtime Command 节点表示项目实际运行命令。这类节点不一定用于 unit test，但用于 smoke test 或部署验证。

### 7.10 Verification 节点

Verification 节点表示验证方式。建议分层验证：先轻量验证，再完整验证。

---

## 8. 边类型定义

| 边类型 | 含义 | 常见强度 |
|---|---|---|
| `requires_runtime` | 某节点依赖特定 runtime | hard |
| `uses_package_manager` | 某安装步骤需要特定包管理器 | hard |
| `dependency_before_build` | 依赖安装必须在项目构建前完成 | hard |
| `test_dependency_before_verify` | 测试依赖必须在验证前完成 | hard |
| `build_before_verify` | 构建或安装必须在验证前完成 | hard |
| `service_required_by` | 服务必须在运行或集成测试前启动 | hard 或 soft |
| `env_required_by` | 环境变量必须在运行或测试前设置 | hard 或 soft |
| `system_required_by` | 系统库是某语言包或构建步骤的底层依赖 | soft 默认 |
| `verify_after_runtime` | runtime command 启动后再 smoke test | hard |

### 8.1 hard edge

`hard edge` 表示严格前后依赖，必须参与拓扑排序。

```json
{
  "from": "requirements-test.txt",
  "to": "pytest --collect-only -q",
  "type": "test_dependency_before_verify",
  "strength": "hard"
}
```

### 8.2 soft edge

`soft edge` 表示经验性依赖或 fallback 依赖，不一定默认执行。

```json
{
  "from": "libxml2-dev",
  "to": "lxml",
  "type": "system_required_by",
  "strength": "soft"
}
```

soft edge 不直接参与主路径拓扑排序，而是进入 `risk_notes` 或 `fallback_plan`。如果 Build Agent 实际失败并验证该依赖必要，可以把 soft edge 升级为 hard edge。

---

## 9. Planning Agent 工作流程

### Step 1：扫描仓库结构

扫描并记录：README、docs、Dockerfile、docker-compose、CI、manifest、lockfile、tox、pytest、tests、Makefile 等。

输出：语言、构建工具、测试框架、配置文件、运行入口。

### Step 2：识别 Runtime 和 Package Manager

根据配置文件和 CI 判断语言版本、base image 和包管理器。

示例规则：

```text
poetry.lock → package_manager = poetry
requirements.txt → package_manager = pip
package-lock.json → package_manager = npm
pnpm-lock.yaml → package_manager = pnpm
pom.xml → package_manager = maven
build.gradle → package_manager = gradle
Cargo.toml → package_manager = cargo
go.mod → package_manager = go mod
```

### Step 3：抽取语言依赖和测试依赖

从 manifest、lockfile、CI 和测试目录中抽取 runtime dependency 和 test dependency。

### Step 4：识别系统依赖、服务、环境变量和数据资源

通过 README、CI、docker-compose 和代码搜索识别非语言包依赖。

重点搜索：

```text
os.environ
process.env
getenv
DATABASE_URL
REDIS_URL
POSTGRES
MYSQL
MONGODB
KAFKA
RABBITMQ
S3
API_KEY
TOKEN
CUDA
ffmpeg
opencv
selenium
playwright
```

### Step 5：构建 Typed Task Graph

把识别出的环境任务和资源组织成 nodes 和 edges。

一个典型顺序是：

```text
Runtime / Base image
    ↓
System packages
    ↓
Package manager
    ↓
Language dependencies
    ↓
Project install/build
    ↓
Runtime preparation
    ↓
Verification
```

### Step 6：程序化生成 ordered todo-list

这一步不交给 LLM 纯推理完成。

正确做法是：

1. LLM 生成 typed nodes 和 typed edges。
2. 程序校验 graph。
3. 程序只用 hard edges 做拓扑排序。
4. 程序用 node type priority 处理并列节点。
5. soft edges 转换为 risk notes 或 fallback plan。
6. 输出 ordered todo-list。

---

## 10. Graph Validator 设计

Graph Validator 至少检查：

1. 每个 node 必须有 `id` 和 `type`。
2. node id 不能重复。
3. 每条 edge 必须有 `from`、`to`、`type`、`strength`。
4. 每条 edge 的 `from` 和 `to` 都必须存在于 nodes。
5. `strength` 只能是 `hard` 或 `soft`。
6. hard edges 组成的图不能有环。
7. 不认识的 node type 或 edge type 应发出 warning。

如果 graph 不合法，程序应该把错误反馈给 Planning Agent 重新生成或修正 graph。

---

## 11. Topological Sorter 设计

### 11.1 类型优先级

多个节点都可以执行时，用类型优先级做稳定排序。

```python
TYPE_PRIORITY = {
    "runtime": 0,
    "system_package": 1,
    "package_manager": 2,
    "language_dependency": 3,
    "project_build": 4,
    "service": 5,
    "env_var": 6,
    "asset": 7,
    "runtime_command": 8,
    "verification": 9,
}
```

注意：类型优先级只是 tie-breaker，真正决定先后关系的是 hard edges。

### 11.2 Kahn 拓扑排序算法

```python
from collections import defaultdict

TYPE_PRIORITY = {
    "runtime": 0,
    "system_package": 1,
    "package_manager": 2,
    "language_dependency": 3,
    "project_build": 4,
    "service": 5,
    "env_var": 6,
    "asset": 7,
    "runtime_command": 8,
    "verification": 9,
}

def topo_sort_typed_graph(nodes, edges):
    node_map = {node["id"]: node for node in nodes}

    if len(node_map) != len(nodes):
        raise ValueError("Duplicate node id found.")

    graph = defaultdict(list)
    indegree = {node["id"]: 0 for node in nodes}

    for edge in edges:
        if edge.get("strength") != "hard":
            continue

        src = edge["from"]
        dst = edge["to"]

        if src not in node_map:
            raise ValueError(f"Edge source does not exist: {src}")
        if dst not in node_map:
            raise ValueError(f"Edge target does not exist: {dst}")

        graph[src].append(dst)
        indegree[dst] += 1

    ready = [node_id for node_id, deg in indegree.items() if deg == 0]
    result = []

    while ready:
        ready.sort(key=lambda node_id: TYPE_PRIORITY.get(node_map[node_id].get("type"), 99))
        current = ready.pop(0)
        result.append(current)

        for nxt in graph[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)

    if len(result) != len(nodes):
        raise ValueError("Hard-edge graph has a cycle.")

    return result
```

---

## 12. Todo-list 生成规则

拓扑排序得到的是 node id 顺序，程序需要把 node 转成 Build Agent 可执行的 todo item。

Todo item schema：

```json
{
  "step": 1,
  "node_id": "python:3.11-slim",
  "task_type": "runtime",
  "command_hint": "FROM python:3.11-slim",
  "evidence": [".github/workflows/test.yml"],
  "confidence": 0.9,
  "notes": []
}
```

Build Agent 不需要逐字执行 `command_hint`，但必须尊重 todo-list 的依赖顺序。

---

## 13. fallback_plan 生成规则

soft edges 转换为 fallback plan。

示例 soft edge：

```json
{
  "from": "libxml2-dev",
  "to": "lxml",
  "type": "system_required_by",
  "strength": "soft"
}
```

转换结果：

```json
{
  "trigger": "pip install lxml fails with missing libxml2 or xslt headers",
  "suggested_action": "apt-get update && apt-get install -y libxml2-dev libxslt1-dev",
  "evidence": ["Dockerfile", "lxml dependency"],
  "confidence": 0.7
}
```

这样 Build Agent 不会一开始就安装所有可能有用的系统包，而是在失败后有针对性地 fallback。

---

## 14. Build Agent 使用规则

Build Agent 执行原则：

1. 优先执行高置信度 todo item。
2. 遇到失败时，先检查对应节点的 risk notes 和 fallback plan。
3. 不要轻易绕过 lockfile。
4. 不要直接安装 random latest package。
5. 如果实际错误和 plan 不一致，允许请求 Planning Agent 修正 graph。
6. 每次修改 plan，都记录原因和证据。
7. 如果某个 soft edge 被实际验证为必要依赖，可以升级为 hard edge。

Planning Agent 输出的是初始地图，不是不可修改的死规则。

---

## 15. LLM Prompt 模板

下面是一个可直接给 Codex 实现的 prompt 模板。

```text
You are a repository environment planning agent.
Your job is to analyze repository files using read-only evidence and produce a Typed Task Graph.

You must not propose executing install/build/test commands during planning.
You must only infer environment requirements from files, code, CI, docs, manifests, lockfiles, and tests.

Return strict JSON with this schema:
{
  "repo_summary": {
    "primary_language": string,
    "package_manager": string | null,
    "test_framework": string | null,
    "recommended_base_image": string | null,
    "entrypoints": string[]
  },
  "typed_task_graph": {
    "nodes": [
      {
        "id": string,
        "type": string,
        "label": string,
        "command_hint": string | null,
        "evidence": string[],
        "confidence": number,
        "scope": string | null,
        "metadata": object
      }
    ],
    "edges": [
      {
        "from": string,
        "to": string,
        "type": string,
        "strength": "hard" | "soft",
        "evidence": string[],
        "confidence": number
      }
    ]
  },
  "risk_notes": string[],
  "fallback_plan": [
    {
      "trigger": string,
      "suggested_action": string,
      "evidence": string[],
      "confidence": number
    }
  ],
  "unresolved_questions": string[]
}

Important rules:
1. Prefer CI and lockfiles over vague README instructions.
2. Do not invent files that are not present.
3. Every node and edge must include evidence.
4. Use hard edges only for strict ordering constraints.
5. Use soft edges for possible system dependencies or fallback dependencies.
6. Do not output the final ordered todo-list. The program will topologically sort the graph.
```

---

## 16. 推荐代码结构

建议新增以下文件：

```text
src/planning/
  __init__.py
  repo_scanner.py
  evidence_collector.py
  planning_agent.py
  schemas.py
  graph_validator.py
  topo_sorter.py
  todo_generator.py
  fallback_generator.py
  prompts.py
  errors.py
```

### 16.1 `schemas.py`

定义数据结构：

```python
from dataclasses import dataclass, field
from typing import Any, Literal

NodeType = Literal[
    "runtime",
    "system_package",
    "package_manager",
    "language_dependency",
    "project_build",
    "service",
    "env_var",
    "asset",
    "runtime_command",
    "verification",
]

EdgeStrength = Literal["hard", "soft"]

@dataclass
class TaskNode:
    id: str
    type: NodeType
    evidence: list[str]
    confidence: float
    label: str | None = None
    command_hint: str | None = None
    scope: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class TaskEdge:
    source: str
    target: str
    type: str
    strength: EdgeStrength
    evidence: list[str]
    confidence: float

@dataclass
class EnvironmentBuildPlan:
    repo_summary: dict[str, Any]
    nodes: list[TaskNode]
    edges: list[TaskEdge]
    ordered_todo_list: list[dict[str, Any]]
    risk_notes: list[str]
    fallback_plan: list[dict[str, Any]]
    unresolved_questions: list[str]
```

### 16.2 `graph_validator.py`

职责：

- 检查重复 node id。
- 检查 edge endpoint 是否存在。
- 检查 node type 和 edge strength 是否有效。
- 检查 hard-edge graph 是否有环。
- 输出 warning 或 exception。

### 16.3 `topo_sorter.py`

职责：

- 只读取 hard edges。
- 使用 Kahn algorithm 排序。
- 用 TYPE_PRIORITY 处理并列节点。
- 返回 node id 顺序。

### 16.4 `todo_generator.py`

职责：

- 将排序后的 node id 转成 todo items。
- 保留 evidence、confidence、command_hint。
- 根据 node type 生成默认 action description。

### 16.5 `planning_agent.py`

职责：

- 调用 RepoScanner 收集 evidence。
- 调用 LLM 生成 graph JSON。
- 解析 JSON。
- 调用 GraphValidator。
- 调用 TopologicalSorter。
- 调用 TodoListGenerator。
- 输出完整 EnvironmentBuildPlan。

---

## 17. Python repo 的最小可行实现规则

第一版只支持 Python repo。规则如下：

### 17.1 Runtime 识别

优先级：

1. CI matrix 中的 Python 版本；
2. `pyproject.toml` 的 `requires-python`；
3. `setup.py` / `setup.cfg` 的 `python_requires`；
4. Dockerfile 中的 base image；
5. 默认 `python:3.11-slim`。

### 17.2 Package Manager 识别

优先级：

1. `poetry.lock` → Poetry；
2. `Pipfile.lock` → Pipenv；
3. `environment.yml` → Conda；
4. `requirements.txt` → pip；
5. `pyproject.toml` only → pip / build backend。

### 17.3 Test Framework 识别

规则：

```text
pytest.ini / conftest.py / tests/test_*.py → pytest
tox.ini → tox may be available
unittest imports → unittest
noxfile.py → nox
```

### 17.4 Project Build 识别

规则：

```text
pyproject.toml + poetry.lock → poetry install
setup.py / setup.cfg → pip install -e .
tox.ini skip_install=true → source-tree execution
CI directly runs pytest after requirements install → source-tree execution likely enough
```

### 17.5 System Package 识别

第一版只做简单 heuristic：

```text
psycopg2 → libpq-dev, build-essential
mysqlclient → default-libmysqlclient-dev, build-essential
lxml → libxml2-dev, libxslt1-dev, build-essential
opencv / cv2 → libgl1, libglib2.0-0
cryptography → libssl-dev, libffi-dev
Pillow source build → libjpeg-dev, zlib1g-dev
```

这些默认作为 soft edges，除非 Dockerfile/CI 明确安装它们。

---

## 18. 示例：jhao104/proxy_pool

### 18.1 Repo Summary

```json
{
  "repo": "jhao104/proxy_pool",
  "primary_language": "Python",
  "package_manager": "pip",
  "dependency_files": ["requirements.txt", "requirements-test.txt"],
  "test_framework": "pytest",
  "ci_python_versions": ["3.8", "3.9", "3.10", "3.11"],
  "recommended_base_image": "python:3.11-slim",
  "runtime_service": "Redis",
  "entrypoints": [
    "python proxyPool.py schedule",
    "python proxyPool.py server"
  ],
  "verification_strategy": [
    "pytest --collect-only -q",
    "pytest --cov=."
  ]
}
```

### 18.2 Typed Task Graph

```json
{
  "nodes": [
    {
      "id": "python:3.11-slim",
      "type": "runtime",
      "command_hint": "FROM python:3.11-slim",
      "evidence": ["GitHub Actions Python 3.8-3.11 matrix"],
      "confidence": 0.9
    },
    {
      "id": "pip",
      "type": "package_manager",
      "command_hint": "python -m pip install --upgrade pip",
      "evidence": ["requirements.txt", "GitHub Actions installs with pip"],
      "confidence": 0.95
    },
    {
      "id": "requirements.txt",
      "type": "language_dependency",
      "command_hint": "pip install -r requirements.txt",
      "evidence": ["requirements.txt", "README"],
      "confidence": 0.95
    },
    {
      "id": "requirements-test.txt",
      "type": "language_dependency",
      "scope": "test",
      "command_hint": "pip install -r requirements-test.txt",
      "evidence": ["requirements-test.txt", "GitHub Actions"],
      "confidence": 0.95
    },
    {
      "id": "source-tree execution",
      "type": "project_build",
      "command_hint": null,
      "evidence": ["tox.ini skip_install=true", "CI runs pytest directly"],
      "confidence": 0.85
    },
    {
      "id": "pytest --collect-only -q",
      "type": "verification",
      "command_hint": "pytest --collect-only -q",
      "evidence": ["tests/", "pyproject.toml testpaths"],
      "confidence": 0.9
    },
    {
      "id": "pytest --cov=.",
      "type": "verification",
      "command_hint": "pytest --cov=.",
      "evidence": ["GitHub Actions"],
      "confidence": 0.9
    },
    {
      "id": "build-essential/libxml2-dev/libxslt1-dev",
      "type": "system_package",
      "command_hint": "apt-get update && apt-get install -y build-essential libxml2-dev libxslt1-dev",
      "evidence": ["Dockerfile installs native deps", "lxml dependency"],
      "confidence": 0.55
    },
    {
      "id": "redis",
      "type": "service",
      "command_hint": "redis-server --daemonize yes",
      "evidence": ["docker-compose.yml", "DB_CONN config"],
      "confidence": 0.9
    },
    {
      "id": "DB_CONN",
      "type": "env_var",
      "command_hint": "export DB_CONN=redis://@127.0.0.1:6379/0",
      "evidence": ["setting.py", "docker-compose.yml"],
      "confidence": 0.9
    },
    {
      "id": "python proxyPool.py server",
      "type": "runtime_command",
      "command_hint": "python proxyPool.py server",
      "evidence": ["README"],
      "confidence": 0.9
    },
    {
      "id": "python proxyPool.py schedule",
      "type": "runtime_command",
      "command_hint": "python proxyPool.py schedule",
      "evidence": ["README"],
      "confidence": 0.9
    },
    {
      "id": "GET /count",
      "type": "verification",
      "command_hint": "curl http://127.0.0.1:5010/count",
      "evidence": ["README API docs"],
      "confidence": 0.8
    }
  ],
  "edges": [
    {
      "from": "python:3.11-slim",
      "to": "pip",
      "type": "requires_runtime",
      "strength": "hard"
    },
    {
      "from": "pip",
      "to": "requirements.txt",
      "type": "uses_package_manager",
      "strength": "hard"
    },
    {
      "from": "requirements.txt",
      "to": "requirements-test.txt",
      "type": "dependency_before_test_dependency",
      "strength": "hard"
    },
    {
      "from": "requirements-test.txt",
      "to": "pytest --collect-only -q",
      "type": "test_dependency_before_verify",
      "strength": "hard"
    },
    {
      "from": "source-tree execution",
      "to": "pytest --collect-only -q",
      "type": "build_before_verify",
      "strength": "hard"
    },
    {
      "from": "pytest --collect-only -q",
      "to": "pytest --cov=.",
      "type": "light_verify_before_full_verify",
      "strength": "hard"
    },
    {
      "from": "build-essential/libxml2-dev/libxslt1-dev",
      "to": "requirements.txt",
      "type": "system_required_by",
      "strength": "soft"
    },
    {
      "from": "redis",
      "to": "DB_CONN",
      "type": "service_provides_endpoint",
      "strength": "hard"
    },
    {
      "from": "DB_CONN",
      "to": "python proxyPool.py server",
      "type": "env_required_by",
      "strength": "hard"
    },
    {
      "from": "DB_CONN",
      "to": "python proxyPool.py schedule",
      "type": "env_required_by",
      "strength": "hard"
    },
    {
      "from": "python proxyPool.py server",
      "to": "GET /count",
      "type": "verify_after_runtime",
      "strength": "hard"
    }
  ]
}
```

### 18.3 Ordered Todo-list：测试路径

```text
1. Use base image python:3.11-slim.
2. Upgrade pip.
3. Install runtime dependencies: pip install -r requirements.txt.
4. Install test dependencies: pip install -r requirements-test.txt.
5. Use source-tree execution; do not default to pip install -e .
6. Run test discovery: pytest --collect-only -q.
7. Run CI-level test: pytest --cov=.
```

### 18.4 Ordered Todo-list：运行路径

```text
1. Start Redis service.
2. Set DB_CONN.
3. Start API server: python proxyPool.py server.
4. Start scheduler: python proxyPool.py schedule.
5. Smoke test API: curl http://127.0.0.1:5010/count.
```

### 18.5 Risk Notes

```text
1. CI suggests Python 3.8-3.11; prefer python:3.11-slim for current test setup.
2. Dockerfile uses python:3.6-alpine; treat it as older deployment signal, not primary test signal.
3. Redis is required for actual runtime, but not necessarily for initial test collection.
4. Do not default to pip install -e . because tox.ini uses skip_install=true.
5. If lxml wheel/build fails, install build-essential libxml2-dev libxslt1-dev.
```

---

## 19. 测试计划

### 19.1 Unit Tests

测试 `GraphValidator`：

```text
valid graph passes
edge source missing raises error
edge target missing raises error
duplicate node id raises error
invalid strength raises error
hard-edge cycle raises error
soft-edge cycle does not block sorting
```

测试 `TopologicalSorter`：

```text
simple chain sorted correctly
parallel nodes sorted by TYPE_PRIORITY
soft edges ignored in primary order
unknown type sorted last
cycle detected
```

测试 `TodoListGenerator`：

```text
node converted to todo item
command_hint preserved
evidence preserved
confidence preserved
step numbers assigned
```

### 19.2 Integration Tests

准备 3 到 5 个小型 repo fixtures：

```text
simple-pip-pytest-repo
poetry-pytest-repo
pip-lxml-fallback-repo
redis-runtime-repo
source-tree-skip-install-repo
```

每个 fixture 检查：

```text
Planning Agent 输出 valid graph
Graph Validator 通过
Topological Sorter 输出稳定顺序
Todo-list 包含预期步骤
fallback_plan 包含预期 soft dependency
```

---

## 20. 评价指标

Planning Agent 可以用以下指标评估：

```text
Plan Validity：输出 graph 是否通过 validator
Ordering Correctness：todo-list 是否满足 hard-edge constraints
Evidence Coverage：nodes/edges 是否都有 evidence
Command Reduction：Build Agent 执行失败命令数是否减少
Rollback Reduction：sandbox rollback 次数是否减少
Success Rate：最终环境构建成功率
Fallback Precision：fallback 是否真正解决失败
Plan Stability：同一 repo 多次 planning 输出是否稳定
```

---

## 21. 实现优先级

建议按以下顺序实现：

```text
P0：schemas.py
P0：graph_validator.py
P0：topo_sorter.py
P0：todo_generator.py
P1：repo_scanner.py
P1：Python repo heuristic extractor
P1：LLM prompt + JSON parser
P2：fallback_generator.py
P2：Build Agent integration
P3：JS / Rust / Java / Go support
P3：service/env/data/asset advanced detection
```

先把 deterministic graph pipeline 做稳定，再接入 LLM。

---

## 22. 关键设计原则总结

1. 不让 LLM 直接输出最终命令顺序。
2. LLM 只负责根据 repo evidence 抽取 typed graph。
3. 程序负责 graph validation 和 topological sort。
4. hard edge 表示强约束，必须排序。
5. soft edge 表示可能依赖，进入 fallback。
6. dependency graph 只是 typed task graph 的一部分。
7. 环境搭建还必须考虑 runtime、system package、service、env var、asset、runtime command 和 verification。
8. Planning Agent 输出的是初始地图，不是不可修改的死规则。
9. Build Agent 可以根据实际执行反馈修正 graph。
10. 所有节点和边都必须带 evidence，否则不应进入高置信度计划。
