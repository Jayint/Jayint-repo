# Planning Agent 方案书：基于 Typed Task Graph 的仓库环境预规划模块

版本：v1.3  
目标读者：Codex / 开发者 / 论文方法设计  
目标系统：在 Build Agent 真正修改 sandbox 之前，用只读方式理解 repo，生成结构化环境任务图，并由程序编译成可执行 todo-list。

v1.3 修订重点：

1. 明确该模块在当前 Repo2Run 项目中的集成位置：它是 sandbox 执行前的只读预规划模块，不替换现有 `src/planner.py` 的 ReAct 单步动作生成职责。
2. 第一阶段先实现 deterministic graph core 和 Python heuristic extractor，再接入 LLM graph extraction。
3. 明确 `command_hint` 只是执行建议，不能直接进入 Dockerfile synthesis；只有 Build Agent 在 sandbox 中真实执行成功的命令才可进入 replay trajectory。
4. 统一 JSON edge 字段和 Python 内部字段的命名约定，避免 `from` 关键字导致实现歧义。
5. 补全示例中使用但原边类型表缺失的 edge type。
6. 明确原 `ImageSelector` 应融合为 Planning Agent 内部的 `BaseImagePlanner` 子模块，而不是在 Planning Agent 外部重复扫描 repo。
7. 明确 Planning Agent 需要先探测自身 host 环境，再在 sandbox 中探测目标 runtime 环境，二者都写入 plan。
8. 增加 sandbox 多轮 read-only probe loop、`install_strategy` 节点和策略边，用于避免高风险依赖安装路径。
9. 明确当前 plan 必须物化为 host/sandbox 双侧文件，Build Agent 可用 `Action: __VIEW_PLAN__` 随时查看。
10. 明确 Build Agent 执行成功或失败后，系统会更新 plan 执行状态，并在必要时让 Planning Agent 根据反馈动态改 plan。
11. 明确 Build Agent 的环境配置边界：禁止修改源码/测试语义文件和创建 stub，但允许修复依赖与环境配置文件，例如 `pyproject.toml`、lockfile、`requirements*.txt`、`setup.cfg`、`tox.ini`、`pytest.ini`。
12. 增加 manifest-driven dependency resolution plan：Planning Agent 从 manifest、lockfile、CI、pytest 配置和 platform marker 推导 Linux-compatible 直接依赖集合；pytest 报错只用于验证或补充 fallback，不作为主依赖求解器。

---

## 1. 背景与核心问题

自动构建 repository 可运行环境时，Build Agent 如果一开始就直接在 sandbox 中执行 `pip install .`、`npm install`、`pytest` 等命令，容易出现五类问题。

第一，探索盲目。Agent 不知道 repo 使用什么语言版本、包管理器、测试框架、服务依赖和配置变量，只能依赖报错进行试错。

第二，环境污染。错误的安装命令可能修改依赖版本、写入缓存、生成中间文件，后续 rollback 或 Dockerfile synthesis 都会变复杂。

第三，可解释性弱。最终失败时，很难判断失败发生在 runtime、system package、language dependency、service、env var、build step 还是 verification 阶段。

第四，环境问题和代码问题边界模糊。缺包、缺系统库、缺 `PYTHONPATH` 属于环境配置；但创建 stub、改源码、重写测试会改变被评测 repo 的语义，让 benchmark 结果不再是纯环境配置结果。

第五，依赖解法不稳定。如果 Build Agent 只根据 pytest import error 逐个安装包，容易堆叠随机 latest package、累积版本冲突，并忽略 `pyproject.toml`、lockfile、CI 和 platform marker 中已有的依赖约束。

本方案引入一个 Planning Agent。它在 Build Agent 真正构建环境前，只做 read-only exploration，把 repo 运行需要的环境需求整理成 Typed Task Graph，然后由程序对 graph 做校验和拓扑排序，生成 ordered todo-list。

核心分工是：

```text
Planning Agent：基于 heuristic 和可选 LLM 理解 repo，抽取 typed nodes 和 typed edges
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
5. 本 v1.x 版本不追求覆盖所有语言和所有复杂部署，优先支持 Python repo。

---

## 3. 系统总体架构

```text
Repository
   ↓
Read-only Exploration
   ↓
Planning Agent
   ├─ RepositoryEvidenceCollector
   ├─ BaseImagePlanner
   └─ TypedGraphPlanner
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

- `RepositoryEvidenceCollector`：扫描仓库结构，收集 README、CI、Dockerfile、manifest、lockfile、tests 等证据。它承接原 `ImageSelector` 中的结构扫描、相关文件定位和文件内容读取能力。
- `PlanningHostEnvironmentProbe`：在 sandbox 创建前探测 Planning Agent 自身运行的 host 环境，包括 macOS/Windows/Linux、CPU 架构、Python 版本、Docker 默认平台提示等。该信息用于区分“规划所在机器”和“目标 sandbox/runtime 环境”，避免把 macOS/Windows host 误当成最终执行环境。
- `BaseImagePlanner`：由原 `ImageSelector` 演进而来，负责根据 evidence 判断主语言、runtime 版本、候选 base image 和 platform override，并把结果写入 runtime 节点。
- `PlanningAgent`：优先用 deterministic heuristic 根据 repo evidence 生成 typed task graph；可选调用 LLM 补充启发式无法判断的 repo-specific 节点和边。
- `DependencyResolutionPlanner`：由 `SandboxPlanningExplorer` 的 manifest/lock probe 产出，负责把 `pyproject.toml`、lockfile、requirements、CI 和 pytest 配置整理成 `dependency_resolution_plan`，优先生成可在 Linux sandbox 中执行的直接依赖安装集合。
- `GraphValidator`：检查 nodes、edges、edge strength、重复 node、无效 edge、hard-edge cycle。
- `TopologicalSorter`：对 hard edges 做确定性拓扑排序，并用 node type priority 处理并列节点。
- `TodoListGenerator`：把排序后的节点转换为 Build Agent 可执行的 todo items。
- `PlanFileManager`：把当前 `EnvironmentBuildPlan`、执行状态和 next todo 写成 host/sandbox 均可查看的 JSON/Markdown 文件。
- `EnvironmentActionGuard`：在 sandbox 真正执行 Build Agent action 前做 preflight，拒绝源码/测试语义修改，允许依赖和环境配置修复，并确保可 replay 的配置修改进入 setup trajectory。
- `BuildAgent`：按 todo-list 在 sandbox 中执行，失败时读取 fallback_plan 或请求 Planning Agent 修正 graph；它可以通过 `Action: __VIEW_PLAN__` 随时查看最新 plan。

### 3.1 在当前 Repo2Run 项目中的集成位置

当前项目里已经存在 `src/planner.py`，但它的职责是 ReAct loop 中的下一步动作生成：读取上一步 Observation，输出一个 `Thought` 和一个可执行 `Action`。本方案中的 Planning Agent 不是这个模块的替代品，而是它的前置上下文生成器。

推荐接入顺序如下：

```text
DockerAgent clone / checkout repository
   ↓
RepositoryEvidence / RepoScanner 只读扫描
   ↓
PlanningHostEnvironmentProbe 探测 host OS/arch/Python
   ↓
EnvironmentPlanningAgent 调用 BaseImagePlanner 选择 runtime/base image
   ↓
EnvironmentPlanningAgent 生成 EnvironmentBuildPlan
   ↓
Sandbox 初始化
   ↓
SandboxPlanningExplorer 多轮只读 probe loop refine plan
   ↓
PlanFileManager 写入 current_environment_plan.{json,md}
   ↓
现有 Planner(ReAct) 将 EnvironmentBuildPlan 作为 Initial Environment Plan
   ↓
Sandbox 执行真实 setup 命令
   ↓
Synthesizer 只从真实成功 trajectory 生成 Dockerfile
```

关键约束：

1. Planning Agent 不执行 setup/install/build/test 命令，也不修改 sandbox；它可以在 sandbox 初始化后通过受控的 `sandbox.inspect()` 执行只读诊断 probe。
2. `EnvironmentBuildPlan` 只能作为 Build Agent 的初始地图和失败诊断上下文。
3. 最终 Dockerfile 仍然必须来自 sandbox 中已经成功执行并被记录的 setup trajectory。
4. 最终成功仍然必须由当前项目的 Verification Bundle gate 判断，不能由 plan 本身证明。
5. `agent_run_summary.json` 应记录 `environment_build_plan`、`environment_plan_files`、`planning_execution_state`、`planning_warnings`、`planning_source`，方便后续分析 plan 是否减少失败命令和 rollback。
6. 当前 plan 必须物化为文件，供 Build Agent 查看和调试：host 侧写入 `logs/planning/current_environment_plan.json` 与 `logs/planning/current_environment_plan.md`，sandbox 侧写入 `/tmp/repo2run_environment_plan.json` 与 `/tmp/repo2run_environment_plan.md`。
7. Build Agent 必须保持 environment-only 边界：允许安装依赖、设置环境变量、修复依赖/测试配置文件，但禁止创建 stub、改源码实现或重写测试语义。
8. 配置文件写入属于环境 setup mutation，必须被 sandbox snapshot、successful action log 和 Dockerfile synthesis 机制记录，不能只停留在 plan 文本中。

### 3.2 Image Selector 融合方式

原 `ImageSelector` 不应作为 Planning Agent 之后的独立模块继续运行。更合理的融合方式是把它拆进 Planning Agent：

```text
原 ImageSelector
  ├─ repo structure / relevant files / docs collection
  ├─ language detection
  ├─ base image selection
  └─ platform override detection

融合后
  ├─ RepositoryEvidenceCollector：负责扫描和读取 evidence
  └─ BaseImagePlanner：负责 language/runtime/base image/platform decision
```

融合后的数据流：

```text
evidence = RepositoryEvidenceCollector.collect(repo_path)
host_env = PlanningHostEnvironmentProbe.collect(target_platform="linux")
base_image_decision = BaseImagePlanner.select(evidence)
typed_graph = TypedGraphPlanner.build(evidence, base_image_decision)
initial_plan = validate + topo_sort + todo_generate
sandbox = DockerAgent._create_sandbox(initial_plan.recommended_base_image)
probe_findings = SandboxPlanningExplorer.inspect_loop(sandbox, initial_plan)
plan = refine(initial_plan, probe_findings) + topo_sort + todo_generate
PlanFileManager.write(plan, execution_state)
```

`BaseImagePlanner` 的输出必须进入 graph，而不是只作为 `DockerAgent` 的局部变量。推荐表示为一个 `runtime` 节点：

```json
{
  "id": "python:3.11-slim",
  "type": "runtime",
  "label": "Python base image",
  "command_hint": "FROM python:3.11-slim",
  "evidence": ["pyproject.toml", ".github/workflows/test.yml"],
  "confidence": 0.9,
  "scope": "test",
  "metadata": {
    "selected_image": "python:3.11-slim",
    "detected_language": "python",
    "platform_override": "linux/amd64",
    "target_platform": "linux",
    "planning_host_environment": {
      "normalized_os": "macos",
      "normalized_arch": "arm64"
    },
    "selection_method": "heuristic+llm"
  }
}
```

`DockerAgent` 在 `base_image == "auto"` 时读取 `EnvironmentBuildPlan.repo_summary.recommended_base_image` 或 runtime 节点的 `metadata.selected_image` 初始化 sandbox；如果用户显式传入 `base_image`，则用户配置优先，并在 plan 的 `validator_warnings` 中记录 override。

---

## 4. Read-only Exploration 规则

Planning Agent 的探索阶段只允许读取，不允许修改环境。当前实现采用两层探索：

1. `PlanningHostEnvironmentProbe` 在 sandbox 创建前探测 Planning Agent 自身运行的 host 环境，例如 macOS/Windows/Linux、arm64/amd64、Python 版本、`DOCKER_DEFAULT_PLATFORM` 等。该信息写入 `repo_summary.planning_host_environment` 和 runtime node metadata。
2. `RepositoryEvidenceCollector` 在宿主 workspace 上静态扫描 repo 结构、manifest、lockfile、测试文件和配置文件。
3. `SandboxPlanningExplorer` 在 sandbox 初始化后执行多轮白名单 probe loop，通过 `sandbox.inspect()` 收集容器内 runtime/tool availability、pyproject 摘要、Poetry lock 平台 marker、潜在 undeclared imports、pytest 收集范围和安装策略信息。

`sandbox.inspect()` 的输出不是 setup trajectory：它不会进入 `successful_actions`，不会创建 Dockerfile replay 命令，也不能进入 Verification Bundle。它只用于 refine `EnvironmentBuildPlan`，让后续 Build Agent 少做盲目试错。

host 环境和 sandbox 环境必须分开记录：

```json
{
  "planning_host_environment": {
    "role": "planning_host",
    "normalized_os": "macos",
    "normalized_arch": "arm64",
    "python_version": "3.12.0",
    "target_platform": "linux",
    "host_is_apple_silicon": true
  },
  "sandbox_runtime_environment": {
    "python": "3.10.20",
    "platform": "Linux aarch64 6.10.14-linuxkit"
  }
}
```

Build Agent 在判断依赖和执行命令时应以 `sandbox_runtime_environment` 为准；`planning_host_environment` 只用于解释 Docker 平台、路径、架构和本地开发机差异。

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

### 4.1.1 多轮 sandbox probe loop

当前 `SandboxPlanningExplorer` 不是只跑一次固定 probe，而是最多执行三轮条件 probe：

1. 第一轮：基础事实探测，包括 runtime、tool availability、pyproject、poetry.lock、全局 import scan。
2. 第二轮：根据第一轮 findings 追加 targeted probe，例如：
   - `focused_import_scan`：结合 pytest `testpaths`、`addopts --ignore` 和本地 package root，优先扫描 pytest 会收集的范围。
   - `dependency_strategy`：当 Poetry 项目出现 PyObjC、EventKit、Foundation、objc、pywin32、darwin/win32 marker 等平台风险时，产出安装策略。
3. 第三轮：保留给后续扩展，例如服务、浏览器、数据资源或 native build tool 的细化探测。

probe loop 必须有预算上限，例如 `MAX_PROBE_ROUNDS = 3` 和单个 probe 输出截断限制。probe 只能读文件、解析配置或运行不会改变环境的 Python 脚本。会写 `.pytest_cache`、生成 report、安装包、联网下载或启动服务的命令不能作为 planning probe，除非 sandbox 提供 snapshot-restore 语义。

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

### 4.4 Build Agent 执行边界：源码保护与配置修复

Planning Agent 的 probe loop 仍然必须只读；但 Build Agent 进入真实 sandbox setup 后，允许做必要的环境配置修复。这里的关键是把“环境问题”和“代码问题”分开：

允许的环境配置动作：

```text
安装 Python/npm/system dependencies
设置 PYTHONPATH、PATH、环境变量或测试工作目录
执行 editable install 或 source-tree execution
修复依赖/环境配置文件，例如 pyproject.toml、poetry.lock、requirements*.txt、constraints*.txt、setup.cfg、tox.ini、pytest.ini、.python-version、environment.yml
```

禁止的源码/测试语义动作：

```text
在 src/、lib/、package 目录下创建缺失模块或 stub
修改源码实现来绕过 import、runtime 或测试失败
重写 tests/ 下的测试文件或改变测试断言语义
用 sed -i、tee、重定向、Python open/write_text 等方式改写源码或测试文件
```

本设计要求 sandbox 在执行 action 前做 preflight guard：如果命令目标落在源码/测试语义文件上，应直接拒绝并返回 observation；如果目标是依赖或环境配置文件，则允许执行，并把成功的配置写入纳入 setup trajectory。这样配置修复可以被 Dockerfile synthesis replay，而源码改动不会污染 benchmark。

本地模块 import 问题的推荐处理顺序是：

```text
1. 检查 package root、src layout、pytest pythonpath、CI working-directory。
2. 优先使用 PYTHONPATH、editable install、package discovery 配置修复。
3. 如果 repo 本身缺少源码模块或需要改源码才能通过，应分类为 code issue / unsupported repo state，而不是创建 stub 伪装成环境成功。
```

---

## 5. EnvironmentBuildPlan 输出格式

Planning Agent 的最终输出是 `EnvironmentBuildPlan`。

```json
{
  "plan_source": "heuristic | llm | hybrid",
  "repo_summary": {},
  "typed_task_graph": {
    "nodes": [],
    "edges": []
  },
  "ordered_todo_list": [],
  "risk_notes": [],
  "fallback_plan": [],
  "unresolved_questions": [],
  "validator_warnings": []
}
```

字段含义：

- `plan_source`：计划来源，第一版推荐使用 `heuristic` 或 `hybrid`，不要一开始依赖纯 LLM。
- `repo_summary`：仓库语言、包管理器、测试框架、推荐 base image、target platform、planning host environment、sandbox runtime environment、platform override、运行入口、`dependency_resolution_plan`。
- `typed_task_graph.nodes`：环境任务或资源节点。
- `typed_task_graph.edges`：任务之间的依赖关系。
- `ordered_todo_list`：程序拓扑排序后生成的执行计划。
- `risk_notes`：执行中需要注意的风险。
- `fallback_plan`：soft edges 或经验性依赖对应的失败修复建议。
- `unresolved_questions`：只读分析无法判断的问题。
- `validator_warnings`：未知 node type、未知 edge type、低置信度 evidence 等非致命问题。

注意：`EnvironmentBuildPlan` 是预规划产物，不是执行证据。它不能替代 sandbox observation、successful action log、Verification Bundle 或 Dockerfile synthesis input。

`repo_summary` 推荐包含以下 base-image 字段：

```json
{
  "primary_language": "python",
  "recommended_base_image": "python:3.11-slim",
  "detected_runtime": "python>=3.10",
  "target_platform": "linux",
  "platform_override": "linux/amd64",
  "planning_host_environment": {
    "role": "planning_host",
    "normalized_os": "macos",
    "normalized_arch": "arm64",
    "python_version": "3.12.0",
    "host_is_apple_silicon": true
  },
  "sandbox_runtime_environment": {
    "python": "3.10.20",
    "platform": "Linux aarch64 6.10.14-linuxkit"
  },
  "dependency_resolution_plan": {
    "source": "pyproject.toml+poetry.lock+pytest_config",
    "strategy": "manifest_driven_linux_direct_deps_before_pytest_feedback",
    "runtime_dependency_specs": ["click==8.1.7"],
    "test_dependency_specs": ["pytest==8.3.2"],
    "excluded_platform_dependencies": ["pyobjc"],
    "steps": [
      {
        "name": "install_build_basics",
        "command": "python -m pip install --upgrade pip setuptools wheel"
      },
      {
        "name": "install_manifest_runtime_deps",
        "command": "pip install click==8.1.7"
      },
      {
        "name": "install_manifest_test_deps",
        "command": "pip install pytest==8.3.2"
      },
      {
        "name": "editable_no_deps",
        "command": "pip install -e . --no-deps"
      }
    ],
    "notes": [
      "install manifest/lock-derived direct dependencies before pytest-error-derived installs",
      "if resolver conflicts appear, rollback/re-solve or repair dependency config instead of stacking arbitrary packages"
    ]
  },
  "base_image_evidence": ["pyproject.toml", ".github/workflows/test.yml"],
  "base_image_selection_method": "heuristic+llm"
}
```

这些字段由 `BaseImagePlanner` 产生，并同步写入 runtime node 的 metadata。`DockerAgent` 初始化 sandbox 时可以读取这些字段；但它们不代表任何 setup 命令已经执行成功。

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

`command_hint` 只能作为 Build Agent 生成下一步 action 的提示，不能直接进入 `Synthesizer`、Dockerfile 或最终 build recipe。只有当 Build Agent 在 sandbox 中真实执行某条命令并成功记录后，该命令才具备 replay 资格。

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

实现约定：JSON 对外格式使用 `from` / `to`，因为它更接近 graph 语义；Python dataclass 内部不要直接使用 `from` 字段名，应映射为 `from_id` / `to_id` 或 `source_id` / `target_id`。解析和序列化必须显式处理这层映射，避免 Python 关键字和 schema 不一致。

---

## 7. 节点类型定义

| 节点类型 | 含义 | 例子 |
|---|---|---|
| `runtime` | 基础运行时、语言版本、base image、硬件环境 | `python:3.11-slim`, `node:20`, `openjdk:17`, `cuda:11.8` |
| `package_manager` | 依赖安装工具 | `pip`, `poetry`, `npm`, `maven`, `cargo` |
| `install_strategy` | 高风险依赖安装前的策略选择 | `avoid full poetry install`, `targeted linux deps before poetry` |
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

### 7.3 Install Strategy 节点

Install Strategy 节点表示在执行高风险依赖安装前，Build Agent 应先采用哪一种安装策略。它不是一个已经成功执行过的命令，也不是替代真实 sandbox 执行的证据；它是把 planning loop 从 repo 文件和 sandbox probe 中发现的风险显式写入 graph。

典型触发条件包括：

```text
poetry.lock 存在，但依赖树包含 PyObjC / EventKit / Foundation / objc / appscript / pywin32 等平台敏感包
sandbox_runtime_environment 显示目标环境是 Linux，而依赖中存在 macOS / Windows 专属包
pyproject/poetry 配置较复杂，直接 full poetry install 可能先安装大量无关或不可用依赖
pytest collection 只需要部分 Linux/test dependencies，完整 runtime dependency install 风险更高
```

当 manifest、lockfile 或 CI 信息足够时，`install_strategy` 不应退化成“pytest 报错后缺什么装什么”。它应引用 `repo_summary.dependency_resolution_plan`，先安装 manifest/lock 中推导出的 Linux-compatible 直接依赖和测试依赖，再做 editable no-deps 或 source-tree execution。pytest import error 只能用于验证该计划是否缺项，或在有证据时补充 fallback。

示例节点：

```json
{
  "id": "install strategy: manifest linux deps before poetry",
  "type": "install_strategy",
  "label": "Avoid high-risk full Poetry install on Linux",
  "command_hint": "follow dependency_resolution_plan: install manifest/lock-derived Linux/test deps, then pip install -e . --no-deps; avoid `poetry install` until proven safe",
  "evidence": ["sandbox dependency strategy probe", "sandbox pyproject/poetry.lock probe"],
  "confidence": 0.82,
  "scope": "test",
  "metadata": {
    "strategy": "manifest_driven_linux_direct_deps_before_poetry",
    "recommended_steps": [
      {
        "name": "install_build_basics",
        "action": "python -m pip install --upgrade pip setuptools wheel"
      },
      {
        "name": "install_manifest_runtime_deps",
        "action": "pip install <runtime specs pinned from pyproject/lock when available>"
      },
      {
        "name": "install_manifest_test_deps",
        "action": "pip install <test specs pinned from pyproject/lock when available>"
      },
      {
        "name": "editable_no_deps",
        "action": "pip install -e . --no-deps"
      }
    ],
    "avoid_commands": ["poetry install"],
    "avoid_packages": ["EventKit", "Foundation", "objc", "pyobjc"],
    "dependency_resolution_plan_ref": "repo_summary.dependency_resolution_plan"
  }
}
```

该节点通常位于 `package_manager` 之后、`language_dependency` 和 `project_build` 之前。这样 Build Agent 不是只知道“项目用 Poetry”，而是知道“当前 Linux sandbox 下不要一上来 full Poetry install，应先执行 manifest-driven dependency resolution plan”。

### 7.4 Language Dependency 节点

Language Dependency 节点表示语言层面的依赖，既可以表示一个依赖文件，也可以表示关键依赖包。

建议优先把依赖文件作为节点，例如 `requirements.txt`、`requirements-test.txt`、`pyproject.toml`，而不是一开始把每个包都展开为节点。只有当某个包会影响系统依赖、服务依赖或失败诊断时，再单独建节点。

### 7.5 System Package 节点

System Package 节点表示操作系统层面的依赖。许多语言依赖安装失败不是因为缺 Python 包，而是缺系统库或编译器。

例如：

```text
pip install psycopg2 可能需要 libpq-dev
pip install lxml 可能需要 libxml2-dev / libxslt1-dev
pip install opencv-python 可能需要 libgl1
```

### 7.6 Project Build 节点

Project Build 节点表示项目自身如何安装、编译或准备。如果 CI 或 tox 表明项目不需要安装成 package，可以用 `source-tree execution` 表示源码树直接执行。

### 7.7 Service 节点

Service 节点表示实际运行或集成测试所需的外部服务。注意区分客户端库和服务本身。例如 `redis` Python package 是 language dependency，而 Redis server 是 service。

### 7.8 Env Var / Secret 节点

Env Var 节点表示环境变量或 secret。如果需要真实 secret，应标记到 `unresolved_questions`，不要让 Build Agent 盲目尝试。

### 7.9 Data / Asset 节点

Asset 节点表示数据、模型、浏览器 binary、fixture 等非 package 资源。

### 7.10 Runtime Command 节点

Runtime Command 节点表示项目实际运行命令。这类节点不一定用于 unit test，但用于 smoke test 或部署验证。

在当前 Repo2Run 项目中，`runtime_command` 默认应标记为 `scope: "runtime"` 或 `scope: "optional"`。除非 benchmark 明确要求服务级 smoke test，否则它不应替代 Repo2Run-style pytest collection 作为最终成功标准。

### 7.11 Verification 节点

Verification 节点表示验证方式。建议分层验证：先轻量验证，再完整验证。

规划阶段生成的 `verification` 节点只说明“应该如何验证”，不说明“已经验证成功”。最终成功仍然必须依赖 Build Agent 在 sandbox 中真实执行成功的验证命令，并由 Verification Bundle gate 接受。

---

## 8. 边类型定义

| 边类型 | 含义 | 常见强度 |
|---|---|---|
| `requires_runtime` | 某节点依赖特定 runtime | hard |
| `uses_package_manager` | 某安装步骤需要特定包管理器 | hard |
| `strategy_after_package_manager` | 安装策略应在识别包管理器之后确定 | hard |
| `strategy_before_dependency` | 安装策略应在语言依赖安装前生效 | hard |
| `strategy_before_build` | 安装策略应在项目 build/install 前生效 | hard |
| `dependency_before_build` | 依赖安装必须在项目构建前完成 | hard |
| `dependency_before_test_dependency` | runtime dependency 应在 test dependency 前安装 | hard 或 soft |
| `test_dependency_before_verify` | 测试依赖必须在验证前完成 | hard |
| `build_before_verify` | 构建或安装必须在验证前完成 | hard |
| `light_verify_before_full_verify` | 轻量验证应在完整验证前完成 | hard |
| `service_provides_endpoint` | 服务节点提供 env var 或 runtime command 所需 endpoint | hard 或 soft |
| `service_required_by` | 服务必须在运行或集成测试前启动 | hard 或 soft |
| `env_required_by` | 环境变量必须在运行或测试前设置 | hard 或 soft |
| `runtime_preparation_before_verify` | ephemeral runtime preparation 应在最终验证前执行 | hard |
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

### 8.3 strategy edge

`install_strategy` 节点通常使用 hard strategy edge 固定在包管理器之后、依赖和 build 之前：

```json
[
  {
    "from": "package manager: poetry",
    "to": "install strategy: targeted linux deps before poetry",
    "type": "strategy_after_package_manager",
    "strength": "hard"
  },
  {
    "from": "install strategy: targeted linux deps before poetry",
    "to": "language dependencies from pyproject.toml",
    "type": "strategy_before_dependency",
    "strength": "hard"
  },
  {
    "from": "install strategy: targeted linux deps before poetry",
    "to": "install project",
    "type": "strategy_before_build",
    "strength": "hard"
  }
]
```

这些边的作用不是证明某个安装命令一定正确，而是防止 Build Agent 在没有读 plan 的情况下回到“先 full install、失败后再试错”的路径。

---

## 9. Planning Agent 工作流程

### Step 0：探测 Planning Host Environment

在创建 sandbox 之前，先记录 Planning Agent 自身所在环境：

```text
normalized_os: macos / linux / windows
normalized_arch: arm64 / amd64 / ...
python_version
docker_default_platform
target_platform
```

该信息写入 `repo_summary.planning_host_environment`。它只用于解释本地机器、Docker platform 和路径差异，不应用来推断最终依赖安装策略；依赖安装策略必须以 sandbox 中探测到的 `sandbox_runtime_environment` 为准。

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

### Step 3：抽取语言依赖、测试依赖和 dependency resolution plan

从 manifest、lockfile、CI、pytest 配置和测试目录中抽取 runtime dependency 与 test dependency，并生成 `repo_summary.dependency_resolution_plan`。

生成规则：

```text
优先使用 pyproject.toml / requirements*.txt / setup.cfg / tox.ini / CI 中的直接依赖声明。
如果 lockfile 中存在对应包版本，则把直接依赖 pin 成 name==version。
根据 sandbox_runtime_environment 排除 macOS/Windows 专属依赖，例如 pyobjc、EventKit、Foundation、objc、appscript、pywin32、darwin/win32 marker。
如果 repo 有 pytest 配置或 tests/，确保 pytest 等测试依赖进入 test_dependency_specs。
pytest collection/import error 只能用于验证 dependency_resolution_plan 或补充有证据的 fallback，不作为主依赖发现方式。
```

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
Install strategy
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

### Step 7：sandbox 多轮只读 refine

初始计划生成后，Planning Agent 可以在 sandbox 初始化之后执行受控 read-only probe。该 loop 不是 Build Agent 的 setup 执行，不应安装依赖、修改源码、运行完整测试或写入长期状态。

当前 v1.3 设计至少包含以下 probe round：

```text
round 1: runtime_environment
  读取 uname、Python 版本、pip 可用性、当前用户和工作目录，生成 sandbox_runtime_environment。

round 2: focused_import_scan
  根据 repo 文件和测试入口识别关键 import、平台敏感 import、可能的 test dependency。

round 3: dependency_strategy
  结合 pyproject/poetry.lock/requirements 与 sandbox_runtime_environment，识别是否需要 install_strategy。
```

如果发现 Linux sandbox 中存在 macOS/Windows 专属依赖风险，例如 PyObjC、EventKit、Foundation、objc、appscript、pywin32 等，Planning Agent 应把风险写入 `risk_notes`，并生成 `install_strategy` 节点和对应 strategy hard edges。

`dependency_strategy` probe 还应输出 lockfile package 摘要和依赖求解计划，例如：

```text
LOCK_PACKAGE pytest 8.3.2
PYPROJECT_DEP pytest >=8
PYPROJECT_PLATFORM_DEP pyobjc markers=sys_platform == "darwin"
INSTALL_STRATEGY_STEP install_manifest_test_deps pip install pytest==8.3.2
```

Build Agent 在执行前应先尝试这些 manifest/lock 推导出的步骤；只有当该计划与实际 sandbox observation 不一致时，才进入 fallback 或请求 Planning Agent 更新 plan。

### Step 8：写入 plan 文件并进入 Build Agent ReAct

refined plan 需要同时写入 host 和 sandbox：

```text
host:
  logs/planning/current_environment_plan.json
  logs/planning/current_environment_plan.md

sandbox:
  /tmp/repo2run_environment_plan.json
  /tmp/repo2run_environment_plan.md
```

Build Agent 的 prompt 中只放压缩版 `Initial Environment Plan`，完整 JSON/Markdown 通过文件查看。Build Agent 可以在任意一轮输出：

```text
Action: __VIEW_PLAN__
```

系统返回最新 plan markdown，包括 completed/failed todo、next todo、risk notes、fallback plan、host/sandbox environment 和 plan 文件路径。

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

校验策略建议分级：

```text
error：重复 node id、edge endpoint 不存在、非法 strength、hard-edge cycle、缺少必填字段
warning：未知 node type、未知 edge type、空 evidence、低 confidence、optional runtime path 连接到 required verification path
```

第一版实现可以允许 warning 通过，但必须把 warning 写入 `validator_warnings` 和 `agent_run_summary.json`。

---

## 11. Topological Sorter 设计

### 11.1 类型优先级

多个节点都可以执行时，用类型优先级做稳定排序。

```python
TYPE_PRIORITY = {
    "runtime": 0,
    "system_package": 1,
    "package_manager": 2,
    "install_strategy": 3,
    "language_dependency": 4,
    "project_build": 5,
    "service": 6,
    "env_var": 7,
    "asset": 8,
    "runtime_command": 9,
    "verification": 10,
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
    "install_strategy": 3,
    "language_dependency": 4,
    "project_build": 5,
    "service": 6,
    "env_var": 7,
    "asset": 8,
    "runtime_command": 9,
    "verification": 10,
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

在当前 Repo2Run 项目中，todo-list 的语义是“行动建议”，不是“已确认 build recipe”。因此：

1. `command_hint` 不可直接写入 Dockerfile。
2. `command_hint` 不可直接作为 Verification Bundle 的命令。
3. `command_hint` 只有被 sandbox 实际执行成功后，才可进入 successful action log。
4. `Synthesizer` 仍然只消费真实 trajectory 和最终验证证据。

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
2. 每完成一个 planned setup step 后，优先查看最新 next todo，再进入新的探索分支。
3. 可以随时通过 `Action: __VIEW_PLAN__` 查看 host-managed 最新 plan 文件摘要。
4. 遇到失败时，先检查对应节点的 risk notes 和 fallback plan。
5. 必须遵守 environment-only boundary：不能创建 stub、不能修改源码实现、不能重写测试语义。
6. 可以修复依赖和环境配置文件，例如 `pyproject.toml`、lockfile、`requirements*.txt`、`setup.cfg`、`tox.ini`、`pytest.ini`；成功的配置修改必须进入 setup trajectory，供 replay 和 synthesis 使用。
7. 本地 import 路径问题应优先用 `PYTHONPATH`、editable install、package discovery 配置或测试工作目录修复；不能用创建缺失模块的方式伪装通过。
8. 不要轻易绕过 lockfile，除非 plan 中有明确 `install_strategy` 或 sandbox 失败证据支持。
9. 如果存在 `repo_summary.dependency_resolution_plan`，应先执行 manifest/lock/CI-derived runtime/test dependency steps，再根据 pytest 报错补充 fallback。
10. 不要直接安装 random latest package，也不要在 resolver 冲突后连续叠加包；应 rollback、重新求解依赖集合，或修复 manifest/config 中的冲突。
11. 如果实际错误和 plan 不一致，允许请求 Planning Agent 修正 graph。
12. 每次修改 plan，都记录原因和证据。
13. 如果某个 soft edge 被实际验证为必要依赖，可以升级为 hard edge。

Planning Agent 输出的是初始地图，不是不可修改的死规则。

当前实现中，plan 不是只存在于 prompt 里的静态文本，而是一个可更新文件状态：

```text
logs/planning/current_environment_plan.json
logs/planning/current_environment_plan.md
/tmp/repo2run_environment_plan.json
/tmp/repo2run_environment_plan.md
```

Build Agent 每次真实执行命令后，系统会把执行结果反馈给 plan state：

```text
success:
  标记 matching todo 为 completed
  刷新 next todo
  重写 current_environment_plan.{json,md}

failure:
  记录 failed action / observation
  调用 Planning Agent execution feedback update
  必要时更新 risk_notes、fallback_plan、node metadata 或 todo notes
  重写 current_environment_plan.{json,md}
```

因此 `__VIEW_PLAN__` 应被视为一个轻量控制动作：它不修改 sandbox，不进入 setup trajectory，也不能作为 Verification Bundle 证据；它只让 Build Agent 在 ReAct loop 内重新对齐当前 plan。

当前 v1.3 仍采用 plan-informed ReAct：系统强化 prompt 和可查看 plan 文件，但不在本层设计中加入“每 N 步强制自动查看 plan”的调度器。该调度器可作为后续第三层增强，用于进一步提高 Plan Consultation Rate。

当前 Repo2Run 实现建议采用“plan-informed ReAct”，而不是“scripted execution”：

```text
Initial EnvironmentBuildPlan
   ↓
SandboxPlanningExplorer 只读 probe loop
   ↓
Refined EnvironmentBuildPlan
   ↓
写入 current_environment_plan.{json,md}
   ↓
格式化为 Initial Environment Plan prompt section
   ↓
现有 Planner 根据 plan + observation 输出单步 Action / __VIEW_PLAN__
   ↓
Sandbox 执行 Action
   ↓
成功/失败 observation 更新 execution state
   ↓
Planning Agent 按执行反馈修正后续 graph/todo
```

这样可以保留当前系统的 rollback、observation compression、successful action recording、Verification Bundle gate 和 Dockerfile synthesis 机制。

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
  "plan_source": "llm",
  "repo_summary": {
    "primary_language": string,
    "package_manager": string | null,
    "test_framework": string | null,
    "recommended_base_image": string | null,
    "detected_runtime": string | null,
    "platform_override": string | null,
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
  "unresolved_questions": string[],
  "validator_warnings": []
}

Important rules:
1. Prefer CI and lockfiles over vague README instructions.
2. Do not invent files that are not present.
3. Every node and edge must include evidence.
4. Use hard edges only for strict ordering constraints.
5. Use soft edges for possible system dependencies or fallback dependencies.
6. Do not output the final ordered todo-list. The program will topologically sort the graph.
7. command_hint is advisory only. Do not imply that the command has already succeeded.
8. Runtime service smoke tests are optional unless repository evidence shows they are required for test collection.
9. Treat source/test code edits and stub creation as out of scope for environment setup; local import failures should be solved with PYTHONPATH, editable install, package discovery config, or classified as code issues.
10. When manifest and lock evidence exist, produce a manifest-driven dependency_resolution_plan before suggesting pytest-error-derived package installation.
```

---

## 16. 推荐代码结构

当前规划模块建议保持以下文件结构；其中多数已经按 v1.3 方向落地：

```text
src/planning/
  __init__.py
  repository_evidence.py
  repo_scanner.py
  base_image_planner.py
  host_environment.py
  python_heuristics.py
  generic_heuristics.py
  planning_agent.py
  dependency_resolution.py
  sandbox_explorer.py
  graph_update.py
  schemas.py
  graph_validator.py
  topo_sorter.py
  todo_generator.py
  fallback_generator.py
  llm_graph_parser.py
  prompts.py
  errors.py
```

其中：

- `repository_evidence.py` 应承接当前 `ImageSelector` 已经具备的结构扫描、相关文件定位和文件内容摘要能力，避免 `ImageSelector` 与 Planning Agent 各扫一遍 repo。
- `base_image_planner.py` 应承接当前 `ImageSelector` 的语言检测、候选镜像选择、platform override 判断能力，并把结果转换成 `runtime` 节点和 `repo_summary` 字段。
- `dependency_resolution.py` 可以作为后续拆分点；当前实现可先放在 `sandbox_explorer.py` 中，由 manifest/lock probe 生成 `repo_summary.dependency_resolution_plan`。
- 原 `src/image_selector.py` 可以先保留为兼容 wrapper，内部委托 `src/planning/base_image_planner.py`；稳定后再逐步迁移调用点。
- 源码/测试语义修改的 preflight guard 属于 sandbox execution 层，可落在 `src/sandbox.py`，不应放在 Planning Agent 里；Planning Agent 只负责给出边界和计划。

### 16.1 `schemas.py`

定义数据结构：

```python
from dataclasses import dataclass, field
from typing import Any, Literal

NodeType = Literal[
    "runtime",
    "system_package",
    "package_manager",
    "install_strategy",
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
    from_id: str
    to_id: str
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
    validator_warnings: list[str] = field(default_factory=list)
    plan_source: str = "heuristic"
```

JSON 输入输出仍使用 `from` / `to`。`TaskEdge.from_id` 和 `TaskEdge.to_id` 只是 Python 内部字段，`from_json()` / `to_json()` 需要负责转换。

`schemas.py` 还应集中维护 `VALID_NODE_TYPES` 和 `VALID_EDGE_TYPES`。v1.3 需要包含以下新增 edge type：

```text
strategy_after_package_manager
strategy_before_dependency
strategy_before_build
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
- `install_strategy` 优先级应位于 `package_manager` 之后、`language_dependency` 之前。

### 16.4 `todo_generator.py`

职责：

- 将排序后的 node id 转成 todo items。
- 保留 evidence、confidence、command_hint。
- 根据 node type 生成默认 action description。

### 16.5 `planning_agent.py`

职责：

- 调用 RepoScanner 收集 evidence。
- 调用 `PlanningHostEnvironmentProbe` 记录 planning host OS/arch/Python/Docker platform。
- 调用 BaseImagePlanner 生成 runtime/base-image decision。
- 第一版优先调用 Python heuristic extractor 生成 graph JSON。
- 可选调用 LLM 补充 heuristic 无法判断的节点和边。
- 解析 JSON。
- 调用 GraphValidator。
- 调用 TopologicalSorter。
- 调用 TodoListGenerator。
- 输出完整 EnvironmentBuildPlan。

### 16.6 `sandbox_explorer.py`

职责：

- 在 sandbox 初始化后执行受控 read-only probe loop。
- 记录 `sandbox_runtime_environment`，并与 `planning_host_environment` 分开。
- 执行 `focused_import_scan`，识别关键 import、平台敏感 import 和测试入口。
- 执行 `dependency_strategy` probe，读取 manifest、lockfile、pytest 配置和 platform marker，生成 `dependency_resolution_plan`。
- 必要时生成或修正 `install_strategy` 节点，并让该节点引用 `repo_summary.dependency_resolution_plan`。
- 只修改 `EnvironmentBuildPlan` 数据结构，不执行真实 setup/install/build/test。

### 16.7 `graph_update.py`

职责：

- 根据 Build Agent 的真实执行反馈更新 plan。
- 成功时标记对应 todo completed，失败时记录 failed action 和 observation。
- 根据失败输出更新 risk notes、fallback plan、todo notes 或 node metadata。
- 保持 plan 文件与执行状态一致。

### 16.8 当前 Repo2Run 集成点

建议在 `DockerAgent.__init__` 中加入以下阶段：

```text
_prepare_workplace()
_checkout_commit()
PlanningHostEnvironmentProbe.collect()
collect_repository_evidence()
planning_agent.create_initial_plan()
base_image = environment_build_plan.repo_summary.recommended_base_image
_create_sandbox()
SandboxPlanningExplorer.inspect_loop()
write_current_environment_plan_files()
Sandbox action preflight guard rejects source/test semantic mutations and permits dependency/environment config repairs
Planner(..., environment_build_plan=formatted_plan_context)
```

如果 CLI 或 benchmark 显式传入 `base_image`，则不要覆盖用户输入；应把用户 override 记录进 `repo_summary.base_image_override` 和 `validator_warnings`。

其中 `formatted_plan_context` 应作为 `src/planner.py` system prompt 或 seed user message 的一个独立 section，标题建议为 `Initial Environment Plan`。它应包含：

```text
repo_summary
ordered_todo_list
risk_notes
fallback_plan
unresolved_questions
validator_warnings
plan_file_paths
planning_execution_state
```

不要把完整 graph JSON 原样塞进每一轮 prompt；graph JSON 可以写入 plan JSON、日志和 `agent_run_summary.json`，prompt 中保留压缩后的 todo-list 和风险即可。Build Agent 如需完整最新上下文，应使用 `Action: __VIEW_PLAN__` 查看 plan markdown。

---

## 17. Python repo 的最小可行实现规则

第一版只支持 Python repo。规则如下：

### 17.1 Runtime 识别

Runtime 识别由 `BaseImagePlanner` 负责。它继承原 `ImageSelector` 的策略，但输出不再只是 `selected_image`，而是：

```text
repo_summary.recommended_base_image
repo_summary.detected_runtime
repo_summary.platform_override
typed_task_graph.nodes[type=runtime]
```

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
pyproject.toml + poetry.lock → package manager evidence + dependency_resolution_plan source; poetry install is candidate/fallback, not default first action
setup.py / setup.cfg → pip install -e .
tox.ini skip_install=true → source-tree execution
CI directly runs pytest after requirements install → source-tree execution likely enough
```

如果 `pyproject.toml + poetry.lock` 同时触发 `install_strategy` 风险，`poetry install` 只能作为候选命令或 fallback，不应作为 Build Agent 的第一步强制动作。

### 17.4.1 Dependency Resolution Plan 识别

Python repo 的第一版依赖求解不追求完整 lock resolver，而是做 conservative direct-dependency plan：

```text
从 pyproject.toml、requirements*.txt、setup.cfg、tox.ini、CI 中抽取直接 runtime/test dependencies
从 poetry.lock 或其他 lockfile 中为这些直接依赖查找已解析版本
在 Linux sandbox 下排除 macOS/Windows 专属依赖和 marker 不匹配依赖
如果存在 pytest 配置或 tests/，把 pytest 加入 test_dependency_specs
生成 install_build_basics → install_manifest_runtime_deps → install_manifest_test_deps → editable_no_deps 的推荐步骤
```

这个 plan 不能直接进入 Dockerfile；只有 Build Agent 真正执行成功的命令才进入 replay。但它应作为 Build Agent 的第一依赖安装路径，避免把 pytest import error 当成主求解器。

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

### 17.6 Install Strategy 识别

第一版重点处理 Python/Poetry 在 Linux sandbox 中的高风险 full install：

```text
poetry.lock + PyObjC/EventKit/Foundation/objc/appscript → avoid_full_poetry_install
poetry.lock + pywin32/win32-only packages + Linux sandbox → avoid_full_poetry_install
tests import only a subset of package modules → prefer dependency_resolution_plan + editable_no_deps before full runtime deps
pytest collection fails on one missing import → verify dependency_resolution_plan first; only add fallback when manifest/CI/code evidence supports the missing dependency
```

生成的 `install_strategy` 节点应写明：

```text
strategy
dependency_resolution_plan_ref
recommended_steps
avoid_commands
avoid_packages
sandbox_runtime_environment
evidence
```

这个节点不直接证明最终环境可用；它只让 Build Agent 在真实执行前避开已知高风险路径。

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
  "detected_runtime": "python>=3.8",
  "platform_override": null,
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

运行路径用于服务级 smoke test 或部署理解，默认不作为 Repo2Run pytest collection 的最终成功标准。

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
unknown edge type emits warning
empty evidence emits warning
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
command_hint marked advisory
```

测试 `schemas.py`：

```text
TaskEdge parses JSON from/to into from_id/to_id
TaskEdge serializes from_id/to_id back to JSON from/to
EnvironmentBuildPlan preserves validator_warnings
```

### 19.2 Integration Tests

准备 3 到 5 个小型 repo fixtures：

```text
simple-pip-pytest-repo
poetry-pytest-repo
poetry-platform-sensitive-repo
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
formatted Initial Environment Plan 不包含完整冗长 graph JSON
command_hint 不会直接进入 Synthesizer 或 Dockerfile
planning_host_environment 和 sandbox_runtime_environment 被分别记录
plan JSON/Markdown 写入 host 与 sandbox 预期路径
__VIEW_PLAN__ 返回最新 plan markdown，且不进入 successful setup trajectory
失败反馈会更新 planning_execution_state 和 current_environment_plan
Poetry + Linux 平台敏感依赖时生成 install_strategy，并排在 language_dependency/project_build 前
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
Prompt Utility：Initial Environment Plan 是否减少无关 read-only 探索
Replay Safety：未执行的 command_hint 是否没有进入 Dockerfile/build_recipe
Plan Consultation Rate：Build Agent 在完成 planned step 后是否查看最新 next todo
Plan Update Utility：失败反馈更新 plan 后，下一步动作是否更接近有效修复
Strategy Avoidance：高风险 full install 命令是否被 install_strategy 成功拦截
Environment Separation：host 环境与 sandbox runtime 是否被正确区分
```

---

## 21. 实现优先级

建议按以下顺序实现：

```text
P0：schemas.py
P0：graph_validator.py
P0：topo_sorter.py
P0：todo_generator.py
P0：TaskEdge JSON from/to ↔ Python from_id/to_id mapping
P0：PlanningHostEnvironmentProbe
P1：repository_evidence.py / repo_scanner.py
P1：base_image_planner.py
P1：src/image_selector.py 兼容 wrapper / 调用迁移
P1：Python repo heuristic extractor
P1：Initial Environment Plan formatter
P1：agent_run_summary.json planning fields
P1：current_environment_plan.{json,md} host/sandbox 文件写入
P1：__VIEW_PLAN__ action integration
P2：sandbox_explorer.py / sandbox.inspect() read-only probe loop
P2：focused_import_scan / dependency_strategy 多轮 refine
P2：install_strategy node + strategy edge integration
P2：execution feedback graph_update.py
P2：fallback_generator.py
P2：Build Agent integration
P2：LLM prompt + JSON parser
P3：JS / Rust / Java / Go support
P3：service/env/data/asset advanced detection
```

先把 deterministic graph pipeline 做稳定，再把 plan 注入当前 ReAct planner，最后接入 LLM graph extraction。不要让第一版实现依赖纯 LLM 才能产出有效 graph。

---

## 22. 关键设计原则总结

1. 不让 LLM 直接输出最终命令顺序。
2. LLM 只作为可选补充，根据 repo evidence 修正或补充 typed graph；第一版主路径应由 deterministic heuristic 产生。
3. 程序负责 graph validation 和 topological sort。
4. hard edge 表示强约束，必须排序。
5. soft edge 表示可能依赖，进入 fallback。
6. dependency graph 只是 typed task graph 的一部分。
7. 环境搭建还必须考虑 runtime、system package、service、env var、asset、runtime command 和 verification。
8. Planning Agent 输出的是初始地图，不是不可修改的死规则。
9. Build Agent 可以根据实际执行反馈修正 graph。
10. 所有节点和边都必须带 evidence，否则不应进入高置信度计划。
11. `command_hint` 不是执行证据，不能直接进入 Dockerfile、build recipe 或 Verification Bundle。
12. 当前 Repo2Run 的最终成功仍然由真实执行过的验证命令和 Verification Bundle gate 决定。
13. 第一版实现应优先保证 deterministic graph core 可测、可复现，再使用 LLM 补充 repo-specific 判断。
14. 原 `ImageSelector` 应收敛为 Planning Agent 内部的 `BaseImagePlanner`，其输出必须进入 `repo_summary` 和 `runtime` 节点。
15. Planning Agent 必须区分 planning host environment 和 sandbox runtime environment。
16. plan 必须物化为可查看文件；Build Agent 通过 `__VIEW_PLAN__` 读取最新 plan，而不是只依赖初始 prompt。
17. Build Agent 的真实成功/失败反馈必须反向更新 plan 状态。
18. 对高风险依赖安装路径，应显式生成 `install_strategy` 节点，而不是让 Build Agent 在 sandbox 中盲目试错。
19. Build Agent 必须保持 environment-only boundary：禁止源码/测试语义修改和 stub creation，允许依赖/环境配置文件修复。
20. 依赖安装应优先执行 manifest/lock/CI/platform marker 推导出的 `dependency_resolution_plan`，pytest 报错只作为验证和 fallback 信号。
21. 每 N 步强制查看 plan 或每个 planned step 后自动注入 plan digest 属于后续第三层增强；v1.3 先通过 plan 文件、prompt 约束和执行反馈更新提升 plan usage。
