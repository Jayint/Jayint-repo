# Polyglot Execute Agent 实现改动总结

## 1. 改动目标

本轮改动将原先主要面向 Python 仓库的环境构建流程扩展为单容器多语言流程，目前覆盖：

- Python
- JavaScript / TypeScript
- Go
- Rust
- Java（Maven / Gradle）

整体方法保持不变：

1. 静态分析仓库并生成 DepGraph。
2. 对 DepGraph 进行拓扑排序，生成 Build Plan 和 Build Script。
3. Host 按 Block 增量执行构建计划。
4. 执行失败时，Execute Agent 围绕失败节点进行只读诊断并提出结构化修复。
5. 修复在 checkpoint 派生的候选容器中验证，成功后才提交图和容器状态。
6. 最后从干净基础镜像完整重放，验证最终脚本可复现。

本次实现针对的是一个仓库在同一个容器内需要多种语言运行时和工具链的情况，不包含前后端分别运行在多个容器中的服务编排。

## 2. 多语言检测与基础镜像选择

### 2.1 语言检测

语言检测由“返回单个语言”改为返回一组 `LanguageRequirement`。每项语言需求包含：

- 语言名称
- 版本约束
- 角色：主要运行时、次要运行时或构建工具
- 检测证据，例如 manifest、lockfile、CI 配置和源码文件

这样可以表达“Python 是测试入口，但 Node.js 负责前端构建”或“Java 项目需要 Node.js 编译静态资源”等组合。

### 2.2 Base image 选择

基础镜像选择遵循以下优先级：

1. 仓库存在可信 Dockerfile 或 CI image 时，优先沿用仓库声明。
2. 存在明确的主要运行时和版本时，优先选择对应语言官方镜像。
3. 多种语言同等重要或无法确定主要运行时时，选择通用 Ubuntu/Debian 镜像。
4. 次要语言仅作为编译工具时，保留主要运行时镜像，并在 Build Plan 中安装次要工具链。

相关实现主要位于：

- `src/language_handlers.py`
- `src/image_selector.py`
- `src/envstate/base_image_selection.py`

## 3. DepGraph 扩展

### 3.1 新增节点类型

在原有节点模型上增加了两类通用节点：

- `Requirement`：源码静态分析得到的依赖需求，例如 Python import、Node 模块引用、Go import、Rust crate 使用和 Java package/class 引用。
- `DependencySet`：由 manifest 和 lockfile 描述的一组可复现安装事务，例如 `npm ci`、`cargo fetch --locked`、`go mod download`、Maven 或 Gradle 依赖解析。

多语言 DepGraph 的主要抽象层次为：

1. Runtime / Toolchain
2. Import / Requirement
3. Package
4. DependencySet
5. Project / Build / Test

### 3.2 通用字段

节点中补充了多语言所需的结构化字段，包括：

- `ecosystem`
- `workspace`
- `manager`
- `constraint`
- `locator`
- lockfile digest
- language role
- runtime/toolchain version
- resolver 状态和错误

支持的生态标识包括 `pypi`、`npm`、`gomod`、`cargo`、`maven` 和 `gradle`。

### 3.3 新增关系

图中增加或统一使用以下关系：

- `maps_to`：源码 Requirement 映射到 Package。
- `resolves_to`：DependencySet 或声明约束解析到具体 Package。
- `describes`：manifest/lockfile 描述 DependencySet。
- 工具链、依赖安装、构建和测试之间的执行先后关系。

节点 ID 保持稳定，版本等易变化信息写入节点属性，避免解析前后生成两份语义相同的节点。

### 3.4 Python 兼容性

原有 Python `Import -> Package` 和 uv resolver 路径被保留，只为节点补充通用的多语言字段。仅在识别为 polyglot 仓库时，才引入跨语言 Runtime、Toolchain 和 Build 关系，避免改变原有纯 Python 行为。

## 4. 各语言 Provider

实现了统一的语言 Provider 接口和注册机制。每个 Provider 负责：

- 识别 manifest、lockfile、workspace 和版本约束
- 静态提取源码 Requirement
- 在 resolver sandbox 中执行原生解析器
- 将解析结果转换为统一 DepGraph fragment
- 提供依赖安装、构建和测试命令

### 4.1 JavaScript / TypeScript

- 解析 `package.json` 及 npm、pnpm、Yarn lockfile。
- 识别 package manager 版本和 monorepo workspace。
- 使用 TypeScript compiler API 提取 import；不可用时回退到 tree-sitter/静态扫描。
- 支持路径别名、动态 import 和 workspace 内部包过滤。
- 没有 lockfile 时，在 resolver sandbox 中生成临时锁定结果，不污染 Host 仓库。
- 通过 `DependencySet` 表达 `npm ci`、pnpm 或 Yarn 安装事务。

### 4.2 Go

- 解析 `go.mod`、`go.sum` 和 `go.work`。
- 使用 `go list -m -json`、`go mod graph` 和 `go list -json` 获取模块与源码依赖。
- 处理 build tags、GOOS/GOARCH 文件约束和本地 workspace module。
- 原生解析器不可用时回退到静态 import 分析。

### 4.3 Rust

- 解析 `Cargo.toml` 和 `Cargo.lock`。
- 使用 `cargo metadata` 保留同名 crate 的不同版本及依赖边。
- 支持 dependency rename、feature/cfg 条件和本地 module 过滤。
- 使用 tree-sitter Rust AST 提取源码 Requirement，并提供静态回退路径。

### 4.4 Java

- 支持 Maven 和 Gradle 项目。
- 解析 `pom.xml`、Gradle 配置及 wrapper 信息。
- Maven 使用 dependency tree 等原生能力解析依赖。
- Gradle 通过临时 init script 获取结构化依赖信息，不修改仓库脚本。
- 通过 JAR 索引将源码引用的 class/package 映射到实际依赖。
- 优先使用仓库自带的 Maven/Gradle wrapper。

## 5. Resolver Sandbox

新增独立的 resolver container 生命周期：

1. 从选定的基础镜像或 checkpoint 创建临时容器。
2. 只安装当前解析器必需的临时工具。
3. 在隔离目录中执行 npm、Go、Cargo、Maven 或 Gradle 原生 resolver。
4. 解析 resolver 输出，只把结构化节点、边、状态和摘要写入正式 DepGraph。
5. 无论成功或失败都关闭临时容器。

resolver container 不复用 working container，也不把 Host 工作目录作为共享可写卷。解析失败时保留 manifest 根节点和错误状态，然后回退到静态结果，而不是让依赖从图中消失。

相关实现集中在 `src/envstate/sandbox.py` 及各语言 Provider。

## 6. Build Plan 与 Build Script

多语言 Build Plan 的总体顺序为：

1. 安装 Runtime
2. 安装 Toolchain
3. 执行各生态 DependencySet
4. 执行项目 Build
5. 应用配置或启动必要服务
6. 执行测试

主要变化包括：

- 非 Python Package 节点主要用于表达依赖和诊断，不再为每个包生成一条零散安装命令。
- 实际安装由 `DependencySet` 作为一个可复现事务执行。
- 不同语言 Provider 生成对应的 install/build/test command。
- 跨语言构建依赖通过图边约束执行顺序，例如先安装 Node.js 依赖并构建前端，再执行 Python 测试。
- 测试命令根据仓库中的语言和构建系统动态选择。

## 7. Execute Agent 集成

原有 checkpoint 事务式修复流程得到保留，并补充了多语言上下文。

### 7.1 RepairScope

失败范围现在可携带：

- 语言和生态
- Runtime / Toolchain 角色及版本
- manifest、lockfile 和 workspace
- package manager 及其版本
- 原始依赖约束
- resolver 状态和错误
- 当前 base image

Agent 因此可以围绕失败节点诊断，而不需要从完整日志中猜测仓库结构。

### 7.2 只读诊断

Host 继续通过统一验证器约束 probe。新增的多语言诊断包括：

- npm/pnpm/Yarn 查询
- `go list` 和只读 module 查询
- Cargo locked/offline 查询
- Maven/Gradle offline 依赖查询

安装、删除、写文件、修改权限、启动服务和重定向写入仍会在进入 Sandbox 前被拒绝。

### 7.3 修复与回滚

当失败被识别为缺少运行时、工具链或生态依赖时，修复会优先写入现有 Runtime、Toolchain、Package 或 DependencySet 节点，并重新打开受影响的已满足节点。

候选修复仍遵循：

1. 生成独立 `candidate_graph` 和 `candidate_blocks`。
2. 根据新旧 Build Plan 的最长公共前缀选择最近有效 checkpoint。
3. 从 checkpoint 创建独立 candidate container。
4. 只执行失效后缀、失败 Block 和受影响检查。
5. 失败则删除候选容器并丢弃候选图。
6. 成功则原子提交候选图，并把已验证容器提升为 working container。

因此，多语言扩展没有破坏“失败不污染正式图和工作容器”的事务边界。

## 8. Clean Replay

增量执行和修复完成后，系统仍会从干净基础镜像执行最终 Build Script：

- 不复用 resolver container。
- 不复用 candidate container。
- 不依赖未写入 DepGraph/Build Script 的临时修改。
- 完整重跑 Runtime、Toolchain、DependencySet、Build 和 Test Block。

只有 clean replay 通过，最终 Build Script 才被视为可复现。

## 9. 验证结果

当前代码已完成以下验证：

- Python 编译检查通过。
- CLI 参数与帮助信息检查通过。
- 生成脚本的 shell 语法检查通过。
- 项目测试结果：`1748 passed, 35 skipped, 2 warnings`。

该测试命令排除了仓库中依赖缺失文档 `docs/repo2run.pdf` 的历史测试文件；它与本轮 polyglot 改动无关。

当前环境没有可用的 Docker CLI/daemon，因此尚未在本机完成真实 Docker 多语言仓库 smoke test。相关 Docker 路径已有单元测试覆盖，但仍需要在具备 Docker 的环境中分别选择 Python+Node、Go、Rust、Maven 和 Gradle 样例做端到端验证。

## 10. 当前边界

- 已支持五类语言在单一容器内共同构建。
- 已支持静态分析、原生 resolver、统一 DepGraph、拓扑 Build Plan、事务式修复和 clean replay。
- 原生 resolver 的完整效果依赖真实环境中的网络、包仓库和工具链可用性。
- 多服务、多网络和多容器编排暂未实现，相关方案与本次单容器 polyglot 实现相互独立。

## 11. 运行入口

自动选择基础镜像并使用增量执行模式：

```bash
python scripts/run_v3_e2e.py /path/to/repository \
  --base-image auto \
  --execution-mode incremental \
  --out setup.sh \
  --trace-out run_trace.json
```

运行后应重点检查：

- 输出 DepGraph 是否包含所有检测到的语言及工具链节点。
- Build Plan 是否按 Runtime、Toolchain、DependencySet、Build、Test 排序。
- 失败修复是否记录 candidate transaction 和 checkpoint。
- 最终 clean replay 是否通过。
