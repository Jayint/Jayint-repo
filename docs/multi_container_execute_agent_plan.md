# GraphExecuteAgent 多容器执行环境改进规划

## 1. 文档目的

本文规划如何在当前 GraphExecuteAgent 的单容器执行路径上，增加对多容器仓库的支持。

这里的“多容器仓库”主要指：

- 前端、后端需要分别构建和运行；
- 后端测试依赖 PostgreSQL、Redis、MongoDB 等独立服务；
- 集成测试需要多个应用服务相互通信；
- 仓库已经提供 `compose.yaml`、多个 Dockerfile 或 CI service 声明；
- 不同服务使用不同语言和运行时，例如 React + Python + PostgreSQL。

本方案目前只作为后续实现依据，不直接修改现有执行逻辑。

## 2. 核心结论

多语言和多容器是两个不同问题：

- 多语言解决“一个仓库需要哪些 Runtime、Toolchain 和 Package”；
- 多容器解决“这些依赖和进程应当分布在哪些隔离的执行单元中，以及它们如何联网、启动、检查和回滚”。

因此，不应因为仓库包含 Python 和 JavaScript 就自动使用多个容器。只有当测试需要多个长期运行的进程、独立服务状态或服务间网络通信时，才进入多容器模式。

推荐的总体方案是：

1. 保留当前单容器路径作为默认路径。
2. 在 DepGraph 中增加 `ExecutionUnit`，明确每个节点属于哪个容器。
3. 将全局图编译为多个容器内 Build Plan，以及一个跨容器 `TopologyPlan`。
4. 将当前单个 `CandidateContainer` 提升为事务式 `CandidateEnvironment`。
5. 构建失败时只重建受影响容器的失效后缀；拓扑失败时复用镜像并重建隔离的候选环境。
6. 候选验证成功后再原子提交图、计划和整套候选环境，失败则全部丢弃。
7. 最后在全新的网络、容器和数据卷中进行 clean replay，验证整个环境可复现。

这个方案的关键不是“让 Agent 写一份 Docker Compose”，而是实现：

> 基于 DepGraph 的容器分区、基于 checkpoint 的局部重建、基于候选环境的事务式拓扑修复。

## 3. 当前实现边界

当前项目已经具备以下能力：

- 静态分析仓库并生成 DepGraph；
- 对 DepGraph 进行调度，生成一个容器内的 Build Plan 和 `setup.sh`；
- 在单个 working container 中增量执行 Build Block；
- 从最近有效 checkpoint 创建单个 candidate container；
- 候选修复失败时回滚，成功时提升 candidate container；
- 识别部分 `Service` 节点；
- 将 Redis 等少量服务作为容器内进程安装和启动；
- 最终从干净基础镜像执行 clean replay。

当前单容器假设主要体现在：

- `scripts/run_v3_e2e.py` 将整个图编译为一个 `setup.sh`；
- `src/sandbox.py` 使用一个 `self.container` 表示正式工作环境；
- `CandidateContainer` 只包含一个候选容器；
- checkpoint 是单个容器对应的镜像；
- Service check 默认检查当前容器的 loopback 地址；
- runtime handoff 导出的是当前容器内的服务启动命令。

所以，多容器支持不能只增加一个 Compose 文件生成器。以下语义都必须同步升级：

- 图中的容器归属；
- Build Plan 的分区；
- checkpoint 的归属；
- 服务发现、网络和健康检查；
- Agent 的故障定位范围；
- candidate transaction 的提交与回滚；
- clean replay；
- RAT harness 的测试执行方式。

## 4. 设计原则

### 4.1 单容器仍然是默认路径

多容器会增加镜像构建、网络、健康检查、日志聚合和清理成本。系统必须先判断是否真的需要多容器。

以下情况继续使用单容器：

- 多种语言只用于同一个构建流程；
- Node.js 只负责构建前端静态资源，最终测试只运行 Python 后端；
- Rust 或 Go 只用于编译 Python 扩展；
- 数据库可以被测试替身代替，而且仓库测试默认就是这样运行；
- 没有证据表明测试需要服务间网络通信。

### 4.2 优先复用仓库的可信声明

发现优先级建议为：

1. 测试或 CI 实际使用的 Compose 文件；
2. CI 中明确声明的 service containers；
3. 仓库 Dockerfile、devcontainer 和脚本共同给出的明确拓扑；
4. 静态分析得到的高置信服务关系；
5. Agent 提出的候选拓扑。

“仓库存在 `compose.yaml`”不等于它适合测试。系统需要结合 CI 命令、测试脚本和 profile 判断真正相关的服务。

### 4.3 Host 控制执行，Agent 只负责诊断和候选修改

Agent 不得直接执行以下操作：

- 创建或删除 Docker network；
- 启动、停止或提升正式容器；
- 选择 checkpoint；
- 直接修改正式 DepGraph；
- 直接覆盖仓库 Compose 文件；
- 提交或回滚 CandidateEnvironment；
- 将服务标记为健康或节点标记为 `SATISFIED`。

这些操作全部由 Host 根据结构化计划和验证结果确定性执行。

### 4.4 构建状态和运行状态分开管理

Docker 镜像 checkpoint 适合保存安装依赖后的文件系统状态，但不适合保存：

- 正在运行的服务进程；
- 已建立的网络连接；
- 数据库内存状态；
- 服务健康状态；
- 临时端口和 DNS 状态。

因此：

- 每个 ExecutionUnit 可以有自己的镜像 checkpoint；
- Topology 只保存“已经验证过的配置和镜像引用”，不保存运行中的进程快照；
- 每次候选拓扑验证都创建新的容器和网络；
- clean replay 必须使用新容器、新网络和新数据卷。

### 4.5 首期只支持可控子集

首期不应试图兼容完整 Compose 规范。推荐 MVP 限制为：

- 最多 4 个运行容器；
- 一个 test runner；
- 应用服务加 Redis/PostgreSQL 两类依赖服务；
- bridge network；
- 容器名 DNS；
- 显式或可生成的 healthcheck；
- 临时数据卷；
- 仓库目录只读或受控挂载；
- 禁止 privileged、host network、Docker socket 和外部网络。

## 5. 总体工作流

```mermaid
flowchart TD
    A["Repository static analysis"] --> B["DepGraph"]
    B --> C["Execution mode decision"]
    C -->|Single container| D["Current Build Plan"]
    C -->|Multi-container| E["Partition by ExecutionUnit"]
    E --> F["Per-unit Build Plans"]
    E --> G["TopologyPlan"]
    F --> H["Build unit images with checkpoints"]
    G --> I["Create isolated WorkingEnvironment"]
    H --> I
    I --> J["Start services and wait for health"]
    J --> K["Run tests in TestRunner"]
    K -->|Failure| L["Locate failed node, unit, and service"]
    L --> M["Agent probes and proposes structured patch"]
    M --> N["Create CandidateEnvironment"]
    N --> O["Rebuild affected suffix and validate topology"]
    O -->|Abort| L
    O -->|Commit| P["Promote candidate environment"]
    P --> K
    K -->|Pass| Q["Clean multi-container replay"]
```

整个流程分为两个层次：

- **Build 层**：负责每个容器镜像内部的 Runtime、Toolchain、Package 和 Project 安装；
- **Topology 层**：负责容器之间的网络、服务启动、连接配置、健康检查和测试入口。

## 6. 执行模式判定

建议增加 `ExecutionModeDecision`：

```python
class ExecutionMode(str, Enum):
    SINGLE_CONTAINER = "single_container"
    MULTI_CONTAINER = "multi_container"


@dataclass
class ExecutionModeDecision:
    mode: ExecutionMode
    evidence: list[EvidenceRef]
    confidence: str
    selected_compose_files: list[str]
    test_entrypoint: str | None
```

### 6.1 进入多容器模式的强证据

满足任意一项即可作为强证据：

- CI 明确执行 `docker compose up` 后再运行测试；
- CI 声明 PostgreSQL、Redis 等 service container，且测试连接对应 hostname；
- 测试命令依赖两个或更多长期运行的应用服务；
- 测试代码访问 `backend:port`、`db:port` 等服务名；
- 仓库提供专用 integration/e2e Compose profile；
- 前端 E2E 测试要求前端和 API 同时在线。

### 6.2 不足以触发多容器的证据

以下证据不能单独触发：

- 仓库包含多个编程语言；
- 存在一个未被测试或 CI 引用的 Compose 文件；
- README 提到可选数据库；
- 源代码中出现数据库客户端包；
- 开发环境包含多个服务，但单元测试不需要它们。

### 6.3 决策策略

Host 根据证据规则做最终决策。Agent 可以在证据冲突时给出建议，但不能直接切换执行模式。

如果证据不足，系统保持当前单容器路径。这样可以避免在普通 polyglot repository 上无意义地创建多个容器。

## 7. DepGraph 扩展

### 7.1 新增 ExecutionUnit 节点

建议新增：

```python
NodeType.EXECUTION_UNIT
Layer.TOPOLOGY
```

`ExecutionUnit` 表示一个独立的镜像构建和容器运行边界。

这里需要区分当前代码与规划状态：

- 当前 `schema.py` 已有 `Test`、`Project`、`Import`、`Package`、
  `SystemLib`、`Tool`、`Runtime`、`Platform`、`Service`、`Config` 和
  `DataAsset`；
- `ExecutionUnit` 是本规划需要新增的节点；
- `Requirement` 和 `DependencySet` 来自
  `docs/polyglot_execute_agent_plan.md`，当前代码中尚未实现；
- 多容器 MVP 不必等待 `Requirement` 和 `DependencySet` 完成，可以先让
  当前已有节点归属于 ExecutionUnit，后续再兼容 polyglot 新节点。

由于当前 `Node` 必须带 `Layer`，ExecutionUnit 建议使用新的
`Layer.TOPOLOGY`。它是结构节点，不是安装动作：

- `tier=0`；
- `setup_commands=()`；
- 不生成普通 Build Block；
- 不进入容器内安装拓扑排序；
- 其有效性由 Host 对镜像、容器和 topology 的认证结果决定；
- `Service` 继续使用现有 `Layer.SERVICES`。

建议字段：

```json
{
  "id": "unit:backend",
  "type": "ExecutionUnit",
  "role": "application",
  "language_set": ["python"],
  "base_image": "python:3.11-slim",
  "dockerfile": "backend/Dockerfile",
  "build_context": ".",
  "workdir": "/workspace/backend",
  "run_command": "python -m app",
  "test_runner": false,
  "source": "compose.yaml",
  "confidence": "confirmed"
}
```

`role` 首期允许：

- `application`：前端或后端应用；
- `dependency`：PostgreSQL、Redis 等第三方服务；
- `test_runner`：负责执行测试的容器；
- `init`：一次性执行迁移或初始化任务。

### 7.2 ExecutionUnit 与 Service 的区别

两者不能合并：

- `ExecutionUnit` 是容器、镜像和 checkpoint 的边界；
- `Service` 是可连接、可启动、可检查的长期运行进程。

例如：

- `unit:backend` 是 Python 容器；
- `service:backend-api` 是该容器中监听 8000 端口的进程；
- `unit:postgres` 是 PostgreSQL 容器；
- `service:postgres` 是其中监听 5432 端口的数据库服务；
- `unit:test-runner` 可以只执行测试，不提供 Service。

### 7.3 新增关系

建议新增两类非安装依赖边：

```python
EdgeType.RUNS_IN
EdgeType.CONNECTS_TO
```

含义如下：

| Edge | 含义 | 是否参与容器内拓扑排序 |
|---|---|---:|
| `RUNS_IN` | Runtime、Package、Project 或 Service 属于某个 ExecutionUnit | 否 |
| `CONNECTS_TO` | 一个应用 Service 或 TestRunner 需要连接另一个 Service | 否 |
| `REQUIRES` | 目标节点是源节点满足前必须满足的硬依赖 | 是 |

跨容器启动顺序不直接复用普通 Package 拓扑排序，而由 `TopologyPlan` 根据 `CONNECTS_TO`、`REQUIRES` 和 healthcheck 编译。

### 7.4 节点归属

每个会产生安装命令或检查命令的节点必须属于一个 ExecutionUnit：

- Runtime；
- Toolchain；
- Package；
- Requirement；
- DependencySet；
- Project；
- Config；
- Service；
- Init task。

共享静态证据节点可以不绑定 ExecutionUnit，但进入 Build Plan 前必须完成归属。

如果一个依赖被两个容器使用，应在两个容器中分别存在可追踪的安装实例，不能让一个 Package 节点同时隐式属于两个镜像。可以保留统一 requirement，再派生：

```text
requirement:npm:typescript
  -> package-instance:frontend:typescript
  -> unit:frontend
```

多容器模式建议使用 unit-scoped executable node ID：

```text
pkg:backend:pypi:requests
pkg:test-runner:pypi:requests
depset:frontend:npm
project:backend:install
```

这样 backend 和 test runner 可以使用同名包的不同版本。为了保护当前 RAT
Python 基线，单容器模式继续保留现有 `pkg:<normalized-name>` ID；只有进入
多容器图编译后才创建 unit-scoped 实例节点。

### 7.5 示例

一个 React + FastAPI + PostgreSQL 仓库可以表示为：

```text
unit:frontend
  Runtime node: Node.js 20
  Package nodes: npm dependencies
  Project node: frontend build
  Service node: frontend

unit:backend
  Runtime node: Python 3.11
  Package nodes: Python dependencies
  Project node: backend install
  Service node: backend-api

unit:postgres
  Image provider: postgres:16
  Service node: postgres

unit:test-runner
  Runtime/Package nodes: Playwright test environment
  Test node: e2e suite

service:frontend CONNECTS_TO service:backend-api
service:backend-api CONNECTS_TO service:postgres
unit:test-runner CONNECTS_TO service:frontend
unit:test-runner CONNECTS_TO service:backend-api
```

## 8. 多容器计划编译

建议新增顶层产物：

```python
@dataclass
class MultiContainerBuildPlan:
    mode: ExecutionMode
    unit_plans: dict[str, BuildPlan]
    topology_plan: TopologyPlan
    test_plan: TestPlan
    graph_revision: str
```

### 8.1 每个 ExecutionUnit 一个 Build Plan

编译步骤：

1. 根据 `RUNS_IN` 将 DepGraph 分区；
2. 对每个分区复用当前 Build Block 生成逻辑；
3. 在每个分区内部执行 topological sort；
4. 为每个 block 计算独立 signature；
5. 为每个 ExecutionUnit 保存 checkpoint 前缀；
6. 将跨容器关系交给 TopologyPlan 编译器。

这样，修改前端 npm 依赖时不需要重新构建 PostgreSQL 或 Python 后端镜像。

### 8.2 TopologyPlan

`TopologyPlan` 不直接保存任意 Compose 文本，而是保存经过 Host 解析和验证的结构化模型：

```json
{
  "project_name": "jayint-run-123",
  "network": {
    "driver": "bridge",
    "internal": false
  },
  "units": {
    "backend": {
      "image_ref": "jayint/backend@sha256:...",
      "command": ["python", "-m", "app"],
      "aliases": ["backend"],
      "environment": {
        "DATABASE_URL": "postgresql://user:pass@postgres:5432/app"
      }
    },
    "postgres": {
      "image_ref": "postgres:16@sha256:...",
      "aliases": ["postgres"]
    }
  },
  "startup_stages": [
    ["postgres"],
    ["backend"]
  ],
  "health_checks": {
    "postgres": {
      "kind": "exec",
      "command": ["pg_isready", "-U", "user"],
      "timeout_seconds": 60
    },
    "backend": {
      "kind": "http",
      "url": "http://backend:8000/health",
      "timeout_seconds": 60
    }
  },
  "init_tasks": [],
  "test_runner": "test-runner"
}
```

### 8.3 Compose 的使用方式

如果仓库已有 Compose：

1. 保留原文件，不直接覆盖；
2. 使用 `docker compose config --format json` 完成变量展开、文件合并和规范化；
3. 将规范化结果转成 `TopologyPlan`；
4. 执行安全策略验证；
5. 生成受控的 `compose.jayint.override.yml`；
6. 使用独立 project name 启动候选环境。

Docker 官方文档说明，`docker compose config` 会解析、合并并规范化 Compose 模型，也支持 JSON 输出：

- [docker compose config](https://docs.docker.com/reference/cli/docker/compose/config/)

多个 Compose 文件可以按顺序合并，`-p` 可以隔离不同项目：

- [docker compose](https://docs.docker.com/reference/cli/docker/compose/)

如果仓库没有 Compose，MVP 不让 LLM 自由生成完整 Compose。只允许 Host 根据高置信 Service 节点和内置模板合成最小拓扑。首期模板可只支持：

- Redis；
- PostgreSQL；
- 一个应用服务；
- 一个 test runner。

## 9. Base Image 选择

进入多容器模式后，不再需要为整个仓库选择一个能容纳所有语言的通用镜像。应当为每个 ExecutionUnit 独立选镜像：

| ExecutionUnit | 推荐策略 |
|---|---|
| Python backend | 仓库声明的 Python 镜像，或匹配版本的官方 Python 镜像 |
| Node frontend | 仓库声明的 Node 镜像，或匹配版本的官方 Node 镜像 |
| Go service | 匹配 Go 版本的 builder，必要时输出到精简 runtime image |
| Java service | 匹配 JDK 的构建镜像和运行镜像 |
| Rust service | Rust builder，必要时输出到精简 runtime image |
| PostgreSQL/Redis | 固定版本并尽量固定 digest 的官方镜像 |
| 混合语言单服务 | 仍按该服务的主要运行时选镜像，再安装次要 toolchain |

这比给所有服务统一使用 Ubuntu 更合理：

- 每个服务只安装自己需要的运行时；
- 容器边界与仓库原有部署边界一致；
- checkpoint 和修复范围更小；
- 前端依赖变化不会使后端环境失效；
- clean replay 可以分别证明各个镜像可复现。

### 9.1 与 polyglot 规划的关系

`docs/polyglot_execute_agent_plan.md` 当前规划的是在同一个 Docker 容器中
识别和安装五种语言的运行时与依赖。本规划不是推翻该方案，而是在它之上增加
一个可选的容器分区层：

```text
Repository
  -> detect workspaces and languages
  -> decide execution mode
  -> assign each workspace/node to an ExecutionUnit
  -> run EcosystemProvider inside each ExecutionUnit
  -> compile one Build Plan per ExecutionUnit
  -> compile one cross-container TopologyPlan
```

因此：

- 单容器模式下，polyglot 规划保持原样；
- 多容器模式下，每个 ExecutionUnit 内部都可以是单语言或多语言；
- `EcosystemProvider` 负责解析和安装该 unit 内的依赖；
- 多容器层只负责 unit 边界、镜像、网络、服务和事务；
- 同一个仓库的 frontend 可以使用 Node Provider，backend 可以使用 Python
  Provider，而不必把 Node 和 Python 都安装进一个通用镜像；
- 如果 backend 自身还需要 Rust 编译 Python 扩展，Python 和 Rust 仍属于
  同一个 backend ExecutionUnit。

两份规划的实施顺序可以独立。多容器 MVP 可以先使用当前 Python 图节点和可信
Compose；polyglot Provider 完成后，再把其 `Requirement`、`DependencySet`
和多语言 Package 节点纳入同一套 ExecutionUnit 归属规则。

## 10. MultiContainerSandbox

不建议直接把当前 `Sandbox.self.container` 改成一个字典，因为这会使成熟的单容器路径承担大量回归风险。

建议新增：

```text
src/envstate/multi_container_sandbox.py
```

由它复用底层 Docker 操作，同时保留当前 `Sandbox` 作为单容器实现。

### 10.1 EnvironmentHandle

```python
@dataclass
class EnvironmentHandle:
    environment_id: str
    transaction_id: str | None
    project_name: str
    network_id: str
    containers: dict[str, ContainerHandle]
    image_refs: dict[str, str]
    unit_checkpoints: dict[str, CheckpointRef]
    volume_ids: list[str]
    topology_hash: str
    status: str
```

### 10.2 两类正式状态

系统维护：

```python
working_environment: EnvironmentHandle
candidate_environments: dict[str, EnvironmentHandle]
```

正式 DepGraph、manual blocks、Build Plans、TopologyPlan 和 working environment 必须属于同一个 revision。

### 10.3 建议接口

```python
class MultiContainerSandbox:
    def build_unit_from_checkpoint(...): ...
    def create_working_environment(...): ...
    def create_candidate_environment(...): ...
    def exec_in_unit(...): ...
    def exec_readonly_in_unit(...): ...
    def inspect_service(...): ...
    def collect_logs(...): ...
    def wait_for_health(...): ...
    def run_test_plan(...): ...
    def promote_candidate_environment(...): ...
    def abort_candidate_environment(...): ...
    def clean_replay(...): ...
```

所有 Docker 对象都必须带标签：

```text
jayint.run_id
jayint.environment_id
jayint.transaction_id
jayint.execution_unit
jayint.lifecycle=working|candidate|replay
```

这样可以在异常退出后进行确定性清理。

## 11. Checkpoint 设计

### 11.1 UnitCheckpoint

每个 ExecutionUnit 独立维护：

```python
@dataclass
class UnitCheckpoint:
    unit_id: str
    image_ref: str
    valid_prefix_signatures: tuple[str, ...]
    graph_revision: str
    plan_revision: str
```

checkpoint 选择继续使用新旧 Build Plan block signature 的最长公共前缀，但只比较受影响 ExecutionUnit 内的 block。

### 11.2 TopologyCertificate

Topology 不使用进程 checkpoint，而保存验证证书：

```python
@dataclass
class TopologyCertificate:
    topology_hash: str
    image_refs: dict[str, str]
    health_results: dict[str, CheckResult]
    connection_results: list[CheckResult]
    test_smoke_result: CheckResult
```

该证书表示“这些镜像和配置曾在一个干净隔离环境中验证成功”，而不是允许复用当时的运行状态。

### 11.3 回退规则

当 Build Plan 改变：

1. 对每个受影响 unit 计算最长公共前缀；
2. 从对应 unit 最近有效 checkpoint 创建候选镜像；
3. 如果 checkpoint 缺失或其基础镜像不匹配，回退更早 checkpoint；
4. 如果仍不匹配，从该 unit 的 base image 开始；
5. 不受影响 unit 直接复用已经验证的 immutable image；
6. 新拓扑始终使用新容器和新网络启动。

## 12. 事务式候选环境

### 12.1 为什么事务单位必须是 Environment

在多容器场景中，只验证一个修复后的 backend container 不足以证明修复成立。例如：

- backend 可以启动，但不能连接 PostgreSQL；
- 修改端口后 backend 健康，但 frontend 仍访问旧端口；
- 数据库 migration 成功，但 test runner 缺少新的环境变量；
- 单个服务 check 通过，但完整测试暴露跨服务不兼容。

所以候选修复必须在隔离的完整拓扑中验证。

### 12.2 CandidateTransaction

```python
@dataclass
class CandidateEnvironmentTransaction:
    transaction_id: str
    base_graph_revision: str
    candidate_graph: DepGraph
    candidate_blocks: list[ManualBlock]
    candidate_plan: MultiContainerBuildPlan
    affected_units: set[str]
    affected_services: set[str]
    base_checkpoints: dict[str, CheckpointRef]
    candidate_environment: EnvironmentHandle | None
    validation_results: list[CheckResult]
    status: str
```

### 12.3 事务流程

```text
1. PatchGate 接受 PatchProposal
2. 创建 candidate_graph 和 candidate_blocks
3. 编译 candidate unit plans 与 candidate topology plan
4. 计算 affected units 和 affected service closure
5. 为受影响 unit 选择最近有效 checkpoint
6. 只执行失效 Build Plan 后缀
7. 复用未受影响 unit 的 immutable images
8. 创建独立 candidate network、containers 和 volumes
9. 按 startup stages 启动并等待 healthcheck
10. 执行目标节点 check
11. 执行受影响的已满足节点 check
12. 执行跨服务 connection checks
13. 执行最小 smoke test 或原失败测试
14. 成功则提交，失败则回滚
```

### 12.4 提交

候选成功后，Host 在一个临界区内：

1. 写入 transaction `PREPARED` 记录；
2. 提交 candidate graph、manual blocks 和 plans；
3. 将 candidate environment handle 设置为新的 working environment；
4. 将新镜像记录为各 unit 的有效 checkpoint；
5. 写入 transaction `COMMITTED`；
6. 停止并清理旧 working environment；
7. 从新的 working environment 继续执行后续测试。

这里的“原子”是控制面原子性：任何时刻正式 revision 只指向一套完整且已验证的图、计划和环境。它不要求像数据库一样瞬间切换外部生产流量，因为该环境只服务于当前构建和测试任务。

### 12.5 回滚

候选失败时：

1. 收集失败 block、service logs、healthcheck 和 connection evidence；
2. 删除 candidate containers；
3. 删除 candidate network；
4. 删除 candidate 临时 volumes；
5. 删除未提交的候选镜像或按标签等待垃圾回收；
6. 丢弃 candidate graph、blocks 和 plans；
7. 保持正式图、正式镜像和 working environment 不变；
8. 将结构化 Observation 返回 Agent。

Compose 清理时应等价于：

```bash
docker compose -p <candidate-project> down -v --remove-orphans
```

相关语义见：

- [docker compose down](https://docs.docker.com/reference/cli/docker/compose/down/)

### 12.6 异常恢复

`run_trace` 应记录事务阶段：

```text
CREATED
PLANNED
BUILDING
STARTING
VALIDATING
PREPARED
COMMITTED
ABORTED
```

进程重启后：

- `PREPARED` 但未 `COMMITTED` 的候选环境默认回滚；
- `COMMITTED` 的 revision 以 durable state 中的 working handle 为准；
- 所有无主 candidate 对象按 Docker label 清理；
- 不根据“某容器还在运行”反推正式状态。

## 13. 三类失败及修复范围

### 13.1 容器内 Build Block 失败

示例：backend 的 `pip install` 失败。

处理：

- 定位到 backend ExecutionUnit；
- 只修改 backend 子图；
- 从 backend 最近 checkpoint 重建失效后缀；
- frontend、PostgreSQL 镜像不重建；
- 使用全部必要镜像创建 CandidateEnvironment；
- 运行 backend check、连接检查和原失败测试。

### 13.2 Topology 或 Service 失败

示例：backend 使用 `localhost:5432`，但 PostgreSQL 在另一个容器。

处理：

- 镜像内容可能完全不变；
- Agent 提议连接配置或 Topology patch；
- Host 创建新候选网络和候选容器；
- 将连接地址改为 Compose service DNS 名；
- 验证 PostgreSQL 健康、backend 连接和受影响测试；
- 不重新执行任何镜像内安装 block。

Compose 默认网络允许服务通过服务名互相发现；依赖关系还可结合 healthcheck 和 `service_healthy` 条件：

- [Compose services](https://docs.docker.com/reference/compose-file/services/)

### 13.3 测试发现新的跨容器依赖

示例：E2E 测试发现 backend 还依赖 Redis。

处理：

- 将 Redis Requirement、Provider、ExecutionUnit、Service 和连接关系写入 candidate graph；
- 构建或拉取 Redis 固定版本镜像；
- 更新 TopologyPlan；
- 创建 CandidateEnvironment；
- 验证 Redis healthcheck、backend 连接、已满足服务 check 和原失败测试；
- 成功后提交完整候选图和环境。

## 14. Service 启动与健康认证

“容器处于 running”不能等价于 Service 已满足。

Service 达到 `SATISFIED` 至少需要：

1. 容器未退出；
2. healthcheck 通过；
3. 预期端口在容器网络内可达；
4. 必要时完成协议级检查；
5. 所有 Init task 成功；
6. 依赖该服务的最小连接检查通过。

首期 healthcheck 模板可以包括：

| Service | 推荐检查 |
|---|---|
| PostgreSQL | `pg_isready` |
| Redis | `redis-cli ping` |
| HTTP API | 容器网络内 HTTP GET |
| Frontend dev server | HTTP GET 根路径或明确 health path |
| Generic TCP | 从 consumer/test runner 发起 TCP connect |

启动过程采用阶段式调度：

```text
Stage 1: dependency services
Stage 2: migrations and init tasks
Stage 3: backend/application APIs
Stage 4: frontend
Stage 5: test runner
```

阶段不是固定写死的，而是由服务依赖图编译。存在依赖环时，Host 应报告 topology cycle，不能让 Agent 通过随意 sleep 掩盖问题。

## 15. 配置与连接绑定

多容器最常见的问题不是缺包，而是连接配置错误。

建议 `TopologyPlan` 显式保存 `ConnectionBinding`：

```python
@dataclass
class ConnectionBinding:
    consumer_unit: str
    provider_service: str
    protocol: str
    host_alias: str
    container_port: int
    environment_keys: list[str]
    source: EvidenceRef
```

例如：

```json
{
  "consumer_unit": "backend",
  "provider_service": "postgres",
  "protocol": "postgresql",
  "host_alias": "postgres",
  "container_port": 5432,
  "environment_keys": ["DATABASE_URL"],
  "source": "compose.yaml"
}
```

Host 负责把 provider 的服务名、端口和测试凭据映射到 consumer。Agent 可以提出结构化 binding patch，但不能注入任意宿主机地址或秘密。

## 16. Execute Agent 协议扩展

继续保留当前三类 JSON action：

- `probe`；
- `propose_patch`；
- `abstain`。

### 16.1 Probe

Probe 需要明确目标容器：

```json
{
  "type": "probe",
  "target_node": "service:backend-api",
  "target_execution_unit": "unit:backend",
  "purpose": "inspect database connection configuration",
  "command": "python -c \"import os; print(os.getenv('DATABASE_URL'))\""
}
```

Host 根据 `target_node` 验证 ExecutionUnit，不能允许 Agent 任意选择宿主机或其他任务的容器。

允许的只读证据还应包括：

- 指定服务日志的只读快照；
- `docker inspect` 中经过筛选的状态字段；
- healthcheck 输出；
- 容器内 DNS 解析；
- 从指定 consumer 到 provider 的连接探测；
- 配置文件只读查看。

Agent 仍不能直接执行 `docker` 或 `docker compose` 命令。

### 16.2 PatchProposal

现有四类修改能力必须保留：

- `add_requirements`；
- `add_providers`；
- `add_edges`；
- `script_patches`。

现有只请求 Host 执行认证检查的 `request_checks` 也应保持兼容，它不是图或
脚本修改，因此不计入上述四类修改能力。

为多容器增加可选的受约束字段：

```json
{
  "add_execution_units": [],
  "execution_unit_patches": [],
  "service_patches": [],
  "connection_patches": [],
  "topology_patches": []
}
```

这些字段必须使用 dataclass/schema 校验，不接受原始 Compose YAML 作为补丁。

### 16.3 RepairScope

返回给 Agent 的 RepairScope 应增加：

- failing execution unit；
- failing service；
- unit Build Plan 前后 block；
- service dependency neighborhood；
- healthcheck 结果；
- container exit status；
- 相关服务日志摘要；
- connection binding；
- topology revision；
- 可修改字段和禁止字段。

Agent 只看失败局部及其跨服务影响闭包，不需要看到所有容器的完整日志。

## 17. PatchGate 扩展

PatchGate 除现有图约束外，需要验证：

- ExecutionUnit ID 唯一；
- 每个可执行节点只有一个容器归属；
- Service 必须属于一个 ExecutionUnit；
- test runner 唯一；
- service alias 不冲突；
- container port 合法；
- 不允许未声明的 host port；
- 不允许 topology dependency cycle；
- 镜像来源满足策略；
- 环境变量 patch 不能覆盖受保护键；
- 新增服务不能超过资源上限；
- Compose 能力在支持白名单内；
- patch 影响闭包能够确定。

PatchGate 通过只代表补丁“结构合法且允许验证”，不代表可以提交正式状态。

## 18. 安全与资源策略

多容器执行扩大了攻击面。建议默认拒绝：

- `privileged: true`；
- Docker socket 挂载；
- `network_mode: host`；
- host PID、IPC 或 user namespace；
- `devices`；
- 任意宿主机 bind mount；
- 仓库根目录之外的 build context；
- external network；
- external volume；
- 固定 `container_name`；
- 访问宿主机 SSH agent；
- 从仓库读取真实云凭据；
- 将服务端口绑定到 `0.0.0.0`；
- 启动超过数量上限的服务；
- 未受限的 CPU、内存和磁盘使用。

默认策略：

- 候选环境使用独立 bridge network；
- 端口不发布到宿主机，测试通过容器网络执行；
- 必须发布时只绑定 `127.0.0.1` 和随机端口；
- 数据卷默认临时创建；
- 仓库源代码按最小需要挂载；
- secret 使用任务级临时值；
- 镜像尽量固定版本和 digest；
- 设置每个 service 的启动超时、总事务超时和日志上限；
- 清理使用 project name 加 label 双重定位。

## 19. Clean Replay

多容器 clean replay 的目标是证明：

> 最终 DepGraph、各 ExecutionUnit Build Plan、TopologyPlan 和 TestPlan 可以从声明的基础镜像重新构造出通过测试的完整环境。

步骤：

1. 删除或停止当前 working environment；
2. 不使用本轮 candidate/working container；
3. 不使用 UnitCheckpoint 镜像作为起点；
4. 从每个 unit 声明的 base image 重新执行完整 Build Plan；
5. 创建全新 project name、network 和 volumes；
6. 启动依赖服务；
7. 执行 init tasks；
8. 等待所有必要 healthcheck；
9. 执行 connection checks；
10. 在 test runner 中运行完整测试；
11. 收集测试结果和镜像 digest；
12. 关闭并清理全部 replay 资源。

“clean”要求不复用会影响语义的容器状态、数据卷和 checkpoint 镜像。内容寻址的下载缓存可以作为性能优化保留，但必须在 trace 中披露；论文实验中还应单独报告禁用缓存的结果。

## 20. 最终输出物

建议最终不再只输出一个 `setup.sh`，而是输出 Environment Bundle：

```text
environment_bundle/
  depgraph.json
  execution_mode.json
  graph_revision.json
  build_plans/
    frontend.json
    backend.json
    test-runner.json
  setup/
    frontend.sh
    backend.sh
    test-runner.sh
  topology_plan.json
  test_plan.json
  compose.resolved.json
  compose.jayint.override.yml
  runtime_handoff.json
  run_trace.json
  replay_certificate.json
```

其中：

- `compose.resolved.json` 是仓库 Compose 经规范化和安全裁剪后的模型；
- `compose.jayint.override.yml` 是 Host 生成的运行覆盖层；
- 原仓库 Compose 文件不被修改；
- `replay_certificate.json` 记录基础镜像、计划 hash、最终镜像 digest、健康结果和测试结果。

## 21. RAT Harness 改动

当前 RAT harness 主要假设一个构建结果对应一个测试容器。多容器模式需要新增 Environment Bundle 执行路径：

1. Adapter 返回 `single_image` 或 `environment_bundle`；
2. harness 构建所有 unit images；
3. 使用隔离 project name 启动 topology；
4. 在 test runner 中执行 pytest、npm test 或其他测试命令；
5. 从 test runner 收集结构化测试结果；
6. 同时收集每个 service 的退出状态和日志；
7. 无论成功、失败或超时都执行 teardown；
8. 将 topology failure 与 test failure 分开计数。

建议新增指标：

- `environment_build_success`；
- `topology_start_success`；
- `all_required_services_healthy`；
- `test_collection_success`；
- `pytest_pass_rate` 或跨语言 test pass rate；
- `clean_replay_success`；
- `containers_rebuilt`；
- `unit_prefix_reuse_ratio`；
- `candidate_transactions_committed`；
- `candidate_transactions_aborted`；
- `resource_leak_count`。

在 harness 尚未支持 Environment Bundle 前，多容器结果不能被伪装成普通 `setup.sh` 结果，否则测试可能只进入其中一个容器并错误得到 `total=0`。

## 22. Run Trace

每个事务至少记录：

```json
{
  "transaction_id": "txn-...",
  "base_graph_revision": "...",
  "candidate_graph_revision": "...",
  "base_topology_hash": "...",
  "candidate_topology_hash": "...",
  "affected_units": ["backend"],
  "affected_services": ["backend-api", "postgres"],
  "base_checkpoints": {
    "backend": "checkpoint:backend:4"
  },
  "reused_images": ["frontend", "postgres"],
  "executed_blocks": [],
  "service_start_results": [],
  "health_results": [],
  "connection_results": [],
  "test_results": [],
  "status": "committed"
}
```

还要记录：

- mode decision 及证据；
- Compose 文件选择理由；
- 所有安全策略 rejection；
- 每个 Docker 对象的 label；
- candidate cleanup 结果；
- clean replay 是否使用下载缓存；
- 最终是否存在资源泄漏。

## 23. 推荐代码结构

建议以增量方式增加模块，避免直接重写当前单容器实现：

```text
src/envstate/
  execution_mode.py
  execution_unit.py
  topology_plan.py
  topology_compiler.py
  topology_policy.py
  multi_container_sandbox.py
  environment_transaction.py
  environment_replay.py
```

需要扩展的现有模块：

| 文件 | 主要改动 |
|---|---|
| `src/python_deps/depgraph/schema.py` | 增加 ExecutionUnit 和跨容器关系 |
| `src/python_deps/depgraph/service_scan.py` | 提取 Compose、CI service 和连接证据 |
| `src/python_deps/depgraph/build.py` | 建立节点归属和 ExecutionUnit |
| `src/envstate/graph_scheduler.py` | 支持按 unit 分区编译 |
| `src/python_deps/depgraph/patch_gate.py` | 校验 topology patch 和影响闭包 |
| `src/envstate/repair_scope.py` | 携带 unit、service、health 和连接证据 |
| `src/envstate/incremental_executor.py` | 调度 unit build 与环境验证 |
| `src/envstate/orchestrator.py` | 管理 WorkingEnvironment 和事务提交 |
| `src/envstate/run_trace.py` | 记录多容器事务 |
| `src/sandbox.py` | 仅抽取可复用的低层 Docker 操作，保留单容器 API |
| `scripts/run_v3_e2e.py` | 增加 feature flag 和 Environment Bundle 输出 |
| RAT adapter/harness | 增加多容器启动、测试和清理 |

## 24. 分阶段实施

### Phase 0：冻结语义和建立开关

目标：

- 增加 `--execution-mode auto|single|multi`；
- 默认仍为 `single`；
- 建立多容器 trace schema；
- 保证现有单容器测试完全不变。

完成标准：

- 功能关闭时生成的 DepGraph、Build Plan 和 setup.sh 与当前版本一致。

### Phase 1：只发现、不执行

目标：

- 解析 Compose、CI services、Dockerfile 和测试入口；
- 生成 ExecutionModeDecision；
- 在 DepGraph 中构建 ExecutionUnit、Service 和连接关系；
- 输出 TopologyPlan 草稿；
- 不启动多个容器。

完成标准：

- 在人工构造的前后端仓库上正确识别容器边界；
- 普通 polyglot repo 不被误判为多容器。

### Phase 2：可信 Compose 执行

目标：

- 只支持仓库已有且通过策略检查的 Compose；
- 生成受控 override；
- 创建独立 project、network 和 volumes；
- 启动服务并执行 healthcheck；
- 在一个 test runner 中运行测试；
- 确定性 teardown。

完成标准：

- backend + Redis fixture 可稳定运行；
- backend + PostgreSQL migration fixture 可稳定运行；
- 超时和失败后无遗留容器、网络或数据卷。

### Phase 3：按 ExecutionUnit 增量构建

目标：

- 为每个 unit 生成 Build Plan；
- 建立 per-unit checkpoint；
- 改变一个 unit 时只重建该 unit 的失效后缀；
- 未受影响镜像直接复用。

完成标准：

- 修改 frontend 依赖时 backend 和 PostgreSQL 不重建；
- 前序 block 改变时对应 unit 回退更早 checkpoint。

### Phase 4：CandidateEnvironment 事务修复

目标：

- 候选图和正式图隔离；
- 完整候选环境验证；
- commit/rollback；
- crash recovery；
- Agent JSON action 与多容器 RepairScope 集成。

完成标准：

- 候选失败不污染正式图、镜像、网络、数据卷和 working environment；
- 候选成功后直接提升已经验证的环境；
- 不在旧 working environment 中重复执行修复。

### Phase 5：Clean Replay 与 Environment Bundle

目标：

- 从所有 base images 完整重建；
- 使用新网络和数据卷；
- 输出 replay certificate；
- 形成可被外部 harness 执行的 Environment Bundle。

完成标准：

- 不依赖 candidate checkpoint 仍可完成全量测试；
- replay 结束后资源清理为零泄漏。

### Phase 6：RAT Harness 与实验

目标：

- RAT adapter 识别 Environment Bundle；
- 区分 build、topology、health、collection 和 test failure；
- 收集效率与修复指标；
- 执行 baseline 和 ablation。

## 25. 测试计划

### 25.1 单元测试

- Compose 规范化结果解析；
- ExecutionModeDecision；
- ExecutionUnit 分区；
- `RUNS_IN` 唯一性；
- TopologyPlan 编译；
- topology cycle 检测；
- service alias 冲突检测；
- healthcheck 编译；
- unit block signature LCP；
- affected unit/service closure；
- topology hash 稳定性；
- 安全策略拒绝；
- transaction 状态转换；
- 异常恢复和垃圾回收。

### 25.2 集成 fixture

至少准备：

1. FastAPI + Redis；
2. FastAPI + PostgreSQL + migration；
3. React + FastAPI；
4. React + FastAPI + PostgreSQL；
5. test runner + 两个应用服务；
6. service healthcheck 永久失败；
7. 错误 hostname；
8. 错误端口；
9. 一个 unit 安装失败；
10. 候选修复成功和失败各一例。

### 25.3 必须覆盖的事务场景

- 候选失败不污染正式 DepGraph；
- 候选失败不替换 working environment；
- 候选失败不保留 network 和 volume；
- 成功后图、计划、镜像和环境使用同一 revision；
- checkpoint 前命令不重跑；
- 只重建受影响 ExecutionUnit；
- topology-only patch 不重建镜像；
- 上游服务接口改变时重新验证下游 closure；
- candidate 验证使用新网络；
- clean replay 不复用运行状态；
- 进程在 `PREPARED` 阶段崩溃后能够回滚；
- 单容器回归测试全部通过。

## 26. 评估方案

建议比较四种方法：

| 方法 | 说明 |
|---|---|
| Single-container baseline | 所有依赖和服务放在一个容器 |
| Compose replay baseline | 直接使用仓库 Compose，不使用图修复 |
| Multi-container without checkpoints | 每次修复重建全部镜像和容器 |
| Proposed method | 图分区 + per-unit checkpoint + CandidateEnvironment |

主要效果指标：

- 环境构建成功率；
- 服务健康成功率；
- 测试通过率；
- clean replay 成功率；
- 平均修复轮数；
- 平均总时间；
- 修复后的重建 block 数；
- 重建容器数；
- checkpoint 前缀复用率；
- 候选失败后的状态污染率；
- 资源泄漏率。

建议消融：

- 去掉 ExecutionUnit 图分区；
- 去掉 per-unit checkpoint；
- 去掉 CandidateEnvironment，直接修改 working environment；
- 去掉 health-aware startup；
- 去掉跨服务影响闭包验证；
- 去掉 Host topology policy，仅让 Agent 生成 Compose。

论文创新点不能表述为“支持 Docker Compose”，因为这只是工程能力。更合理的技术贡献是：

> 将依赖图扩展为包含容器执行边界和服务连接关系的可执行环境图，并通过图分区 checkpoint 与事务式候选环境，实现多服务环境的局部修复、无污染回滚和可复现重放。

## 27. 风险与控制

| 风险 | 控制方法 |
|---|---|
| Compose 特性过多 | MVP 只接受白名单子集 |
| 多容器启动不稳定 | healthcheck、阶段启动和协议级检查 |
| 修复成本反而增大 | per-unit checkpoint 和 immutable image 复用 |
| 候选环境资源泄漏 | project name、Docker label、finally cleanup、启动时 GC |
| Agent 生成危险配置 | 结构化 patch 和 Host policy |
| 错把 polyglot 当 multi-container | 强证据触发，默认单容器 |
| 数据库状态污染结果 | 候选和 replay 使用独立临时 volume |
| 随机 host port 冲突 | 尽量不发布端口，只使用容器网络 |
| 测试工具必须从宿主机访问 | 仅绑定 loopback 随机端口 |
| 构建资源消耗过大 | 服务数、并发、CPU、内存和磁盘上限 |
| RAT harness 无法表达拓扑 | 新增 Environment Bundle 协议，不伪装成单镜像 |

## 28. 非目标

首期明确不做：

- Kubernetes；
- 多宿主机部署；
- 生产流量切换；
- 服务自动扩缩容；
- 持久化生产数据；
- 任意 Docker Compose 特性；
- privileged service；
- Docker-in-Docker；
- GPU、USB 或特殊设备直通；
- 自动发现所有微服务架构；
- 证明应用业务逻辑正确；
- 将所有 polyglot repository 强制拆成多个容器。

## 29. 推荐 MVP

为了保证方案可实现、可评估，第一版只做：

1. 使用 feature flag，默认关闭；
2. 只处理仓库已有且被 CI/test 引用的 Compose；
3. 最多 4 个容器；
4. 一个 test runner；
5. 支持应用服务、Redis 和 PostgreSQL；
6. 每个应用 ExecutionUnit 独立 Build Plan；
7. 每个 unit 独立 checkpoint；
8. 使用独立 project name、bridge network 和临时 volume；
9. 服务必须有可验证 healthcheck；
10. 支持 build failure 和 topology configuration failure 两类 repair；
11. 使用 CandidateEnvironment 完整提交或回滚；
12. 最终执行全新多容器 clean replay。

等 MVP 稳定后，再考虑：

- 没有 Compose 时从高置信图合成拓扑；
- MongoDB、RabbitMQ、Elasticsearch 等模板；
- 多个 test runner；
- 多个同语言或异语言应用服务；
- 更精细的跨服务影响分析；
- 分布式 build cache；
- 更丰富的 Compose profile 和 init job。

## 30. 验收标准

只有同时满足以下条件，才能认为多容器改进完成：

- 单容器现有路径没有行为回归；
- 系统不会因为多语言而错误触发多容器；
- DepGraph 能明确表示容器边界和服务连接；
- 每个 ExecutionUnit 都能独立生成和执行 Build Plan；
- 修改一个 unit 不会无条件重建全部镜像；
- topology-only patch 不会重新执行安装命令；
- 候选修复失败不会污染任何正式状态；
- 候选成功后提升的是已经验证过的整套环境；
- Service 必须通过健康与连接认证，不能只看容器 running；
- clean replay 使用新容器、新网络和新数据卷；
- 最终 Environment Bundle 能被 RAT harness 独立执行；
- 失败后没有遗留 container、network 或 volume；
- run trace 可以还原每次构建、修复、提交和回滚过程。

## 31. 最终建议

多容器支持值得做，但应当作为当前 GraphExecuteAgent 上的可选执行层，而不是替换现有单容器方法。

最合理的实施顺序是：

1. 先识别 ExecutionUnit 和可信拓扑；
2. 再做到可信 Compose 的确定性执行；
3. 然后实现 per-unit checkpoint；
4. 最后引入 CandidateEnvironment 事务修复和 RAT 评估。

这样既能支持真实的前后端和服务依赖仓库，也能保留当前方法最重要的性质：

- DepGraph 是正式知识状态；
- Build Plan 由 Host 确定性编译；
- Agent 只负责开放式诊断和受约束候选修复；
- checkpoint 避免重复执行；
- 候选事务保证失败不污染正式环境；
- clean replay 证明最终结果可复现。
