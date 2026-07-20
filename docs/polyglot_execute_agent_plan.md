# Polyglot Repository 支持改造规划书

## 1. 改造目标

本次改造的目标是在不改变 GraphExecuteAgent 核心架构的前提下，让系统能够
识别仓库中的多种语言，并在同一个 Docker 容器中安装相应的运行时、工具链和
项目依赖。

整体流程保持为：

```text
Repository
  -> 检测全部语言及版本
  -> 选择一个合适的 Base Image
  -> 构建包含多语言节点的 DepGraph
  -> Topological Sort 生成 Build Script
  -> 增量执行 Build Script
  -> Execute Agent 修复失败节点
  -> 运行测试
  -> Clean Replay
```

改造范围主要集中在流程的前半部分：多语言检测、Base Image 选择、DepGraph
构建和 Build Script 生成。后半部分继续复用现有的增量执行、候选修复和
clean replay。

本方案不引入统一的跨语言依赖求解算法。每种语言仍使用其原生 Resolver，
**系统只负责将各 Resolver 的结果规范化并合并到同一张 DepGraph 中**。

## 2. 多语言检测

首先将当前只能返回一种语言的接口：

```python
detect_language(...) -> str
```

扩展为：

```python
detect_languages(...) -> list[LanguageRequirement]
```

其中：

```python
@dataclass
class LanguageRequirement:
    language: str
    version_constraint: str | None
    role: str
    evidence: tuple[str, ...]
```

`role` 只需要区分三种情况：

- `primary_runtime`：执行主要测试的语言；
- `secondary_runtime`：测试或构建过程中需要的其他语言；
- `build_tool`：只用于编译扩展或生成产物的语言。

语言检测应综合 Manifest、lockfile、CI、Dockerfile 和源码目录，而不能只根据
文件后缀计数。版本无法确定时保留为 `None`，不要让 LLM 随意猜测。

常见版本证据包括：

| 语言 | 版本来源 |
|---|---|
| Python | `requires-python`、CI、Dockerfile |
| Node.js | `package.json#engines`、`.nvmrc`、CI |
| Rust | `rust-toolchain.toml`、CI |
| Java | `pom.xml`、Gradle toolchain、CI |
| Go | `go.mod`、CI |

下面以一个名为 `fast-search` 的 **Python 后端 + Node 前端 + Rust 原生扩展**
仓库为例，说明三种语言角色如何判断：

```
fast-search/
├── pyproject.toml
├── src/fast_search/
│   ├── api.py
│   └── index.py
├── tests/
│   └── test_search.py
│
├── frontend/
│   ├── package.json
│   ├── src/
│   └── tests/
│
└── native/
    ├── Cargo.toml
    └── src/lib.rs
```

它的 CI 流程是：

```
# 编译 Rust 原生扩展
maturin build --manifest-path native/Cargo.toml

# 安装 Python 项目
pip install -e .

# 运行前端测试和构建
cd frontend
npm ci
npm test
npm run build

# 运行主要测试
cd ..
pytest -q
```

根据仓库结构和 CI 命令，三种语言角色可以表示为：

```
[
  {
    "language": "python",
    "version_constraint": ">=3.10,<3.13",
    "role": "primary_runtime",
    "evidence": ["pyproject.toml", "pytest.ini"]
  },
  {
    "language": "javascript",
    "version_constraint": ">=20",
    "role": "secondary_runtime",
    "evidence": ["frontend/package.json"]
  },
  {
    "language": "rust",
    "version_constraint": "1.82",
    "role": "build_tool",
    "evidence": ["native/Cargo.toml", "pyproject.toml:maturin"]
  }
]
```

**Python：`primary_runtime`**

Python 是主要运行时，因为：

- 仓库的主要应用是 Python 后端；
- 最终主要测试命令是 `pytest`；
- Rust 和前端准备完成后，最终仍由 Python 测试判断仓库是否可执行；
- Python 代码会在最终运行阶段持续执行。

因此基础镜像可以优先选择：

```
python:3.11-slim
```

**JavaScript：`secondary_runtime`**

Node.js 是次要运行时，因为：

- `frontend/` 是一个独立的 JavaScript 项目；
- 需要执行 `npm test`；
- JavaScript 源码会被 Node.js 直接执行；
- 它有自己的依赖、测试和构建流程。

它不是主要测试入口，但也不只是编译器，因此属于 `secondary_runtime`。

**Rust：`build_tool`**

Rust 在这个仓库中只用于生成 Python 原生扩展：

```
native/src/lib.rs
→ cargo/rustc 编译
→ fast_search_native.so
→ Python import fast_search_native
```

最终测试执行的是：

```
import fast_search_native
```

并不会直接执行：

```
cargo run
```

也不需要启动 Rust 服务。Rust 的作用在环境构建阶段结束：

```
Rust source
→ rustc/cargo
→ 编译出 .so
→ Python 加载 .so
```

因此这里真正需要的是：

- `rustc` 编译器；
- `cargo` 包管理和构建工具；
- Cargo dependencies；
- 可能还有 `maturin`。

Rust 不需要作为最终应用运行时存在，所以它属于 `build_tool`。

将上述角色写入图后，可以得到如下简化 DepGraph：

```
Test:pytest
  └─requires─> Project:python
                   ├─requires─> Runtime:python-3.11
                   ├─requires─> Python packages
                   ├─requires─> Project:frontend
                   │                ├─requires─> Runtime:node-20
                   │                └─requires─> npm dependencies
                   └─requires─> Native extension
                                    ├─requires─> Tool:rustc
                                    ├─requires─> Tool:cargo
                                    └─requires─> Cargo dependencies
```

执行顺序由依赖关系确定：

```
安装 Rust toolchain
→ 编译 Python 原生扩展
→ 安装 Python 项目
→ 安装并测试前端
→ 运行 pytest
```

## 3. Base Image 选择

得到语言集合、版本和角色后，再按以下规则选择一个基础镜像：

| 仓库情况 | Base Image |
|---|---|
| 仓库已有可信 Dockerfile 或 CI image | 优先使用仓库声明 |
| 一个语言是主要测试运行时，版本明确 | 使用该语言官方镜像 |
| 多个语言同等重要 | 使用 Ubuntu 或 Debian |
| 次要语言只是编译工具 | 使用主要运行时镜像，再安装编译工具 |
| 所有语言都没有明确版本 | 使用 Ubuntu 或 Debian |

例如：

| 仓库 | 选择 |
|---|---|
| Python + Rust 扩展 | `python:3.11-slim`，随后安装 Rust |
| Python + C++ 扩展 | `python:3.11-slim`，随后安装 `g++` |
| Java 后端 + Node 前端 | 若 Java 是测试入口，使用 JDK 镜像并安装 Node |
| Python 与 Java 同等重要 | `ubuntu:24.04`，分别安装 Python 和 JDK |

实现上只需要把当前的：

```python
choose_base_image(repo, language)
```

调整为：

```python
choose_base_image(repo, languages)
```

当静态证据存在冲突时，LLM 可以辅助判断主要运行时；最终选择仍由 Host 按上述
规则完成，并记录所采用的证据。

## 4. 扩展 DepGraph

现有 DepGraph 已经包含 `Runtime`、`Tool`、`Package`、`Project`、
`SystemLib` 等通用节点类型，因此无需另建一张 Graph。不过，也不能只给现有
`Package` 节点增加一个语言字段，因为：

- 不同生态中可能存在同名包；
- npm 和 Cargo 可以在同一解析结果中包含同一个包的多个版本；
- **npm、Cargo、Go、Maven 和 Gradle 通常以整个 workspace 为安装事务，
  不能为每个 Package 节点单独生成安装命令**；
- 当前 `Layer.PIP`、`resolved_python` 和默认 `pip install` 都是
  Python 专属语义。

因此需要在保留现有节点的基础上增加两个节点类型：

```python
class NodeType(Enum):
    ...
    REQUIREMENT = "Requirement"
    DEPENDENCY_SET = "DependencySet"
```

- `Requirement`：Manifest 中的原始依赖声明，例如 `react:^18`、
  `serde:1` 或 `requests>=2.31`；
- `Package`：Resolver 最终选择的具体包、版本和 Resolver locator；
- `DependencySet`：某个 workspace 的一次完整依赖安装事务，例如
  `npm ci`、`cargo fetch --locked` 或 `go mod download`。

四类节点分别回答不同问题：

```text
Import        -> 源码实际引用了什么
Requirement   -> Manifest 声明需要什么
Package       -> Resolver 最终选择了什么
DependencySet -> 应当如何一次性安装该 workspace 的依赖
```

后续构图、命令生成和失败定位都围绕这四层信息展开。

### 4.1 通用生态字段

核心字段应从 Python 专属字段推广为：

```python
class Ecosystem(Enum):
    PYPI = "pypi"
    NPM = "npm"
    CARGO = "cargo"
    GO_MODULE = "gomod"
    MAVEN = "maven"
    GRADLE = "gradle"

@dataclass(frozen=True)
class Node:
    ...
    ecosystem: Ecosystem | None = None
    workspace: str | None = None
    package_manager: str | None = None
    declared_constraint: str | None = None
    resolved_locator: str | None = None
    lock_digest: str | None = None
```

`ecosystem`、`workspace` 和 `package_manager` 属于核心身份信息，应使用明确
字段，而不是全部放入 `data`。`resolved_python` 可以暂时保留为兼容字段，
新 Provider 应统一写入目标平台和运行时组成的 resolution context。

### 4.2 多语言 Import 静态扫描

现有 Python DepGraph 会扫描源码中的 `import` 和 `from ... import ...`，
生成 Import 节点，表示“源码实际引用了这个模块”。其他语言也可以保留同样的
需求侧节点，不需要新增另一种节点类型；只需将 `NodeType.IMPORT` 推广为带语言
和 workspace 信息的多语言源码引用节点：

```python
@dataclass(frozen=True)
class ImportRef:
    language: str
    workspace: str
    raw_specifier: str
    source_file: str
    scope: str                 # runtime | test | build
    conditional: str | None
    confidence: str            # high | medium | low
```

对应的节点 ID 例如：

```text
import:python:.:yaml
import:go:backend:github.com/gin-gonic/gin
import:npm:frontend:@vitejs/plugin-react
import:cargo:native:tokio
import:jvm:server:org.junit.jupiter.api.Test
```

前三类节点的关系可以进一步概括为：

```text
Import      = 源码实际引用了什么
Requirement = Manifest 声明需要什么以及版本约束
Package     = Resolver 最终选择了哪个具体包
```

同一个 Package 可以同时被 Import 和 Requirement 指向。例如：

```text
Import:python:yaml -------------------maps_to---\
                                                > Package:pypi:PyYAML==6.0.2
Requirement:pypi:PyYAML>=6.0 --resolves_to-----/
```

#### Python

使用 Python AST 扫描：

- `ast.Import`；
- `ast.ImportFrom`；
- 区分 runtime source 和 test source；
- 过滤标准库和仓库内本地模块；
- 使用 `importlib.metadata.packages_distributions()`、已有映射表和 Resolver
  结果将 import name 映射到 PyPI distribution。

例如：

```text
import yaml
  -> Import:python:yaml
  -> Package:pypi:PyYAML
```

Python import name 与 distribution name 不一定相同，因此不能直接把 `yaml`
当作 PyPI 包名。

#### Go

Go 的 import path 与 package/module 关系最明确，优先使用：

- `go/parser` 的 `ImportsOnly` 模式提取 import declaration；
- `golang.org/x/tools/go/packages` 根据 build tags、GOOS、GOARCH 和 package
  配置确定本次目标实际包含的源码文件；
- `go list -deps -json ./...` 确认 package；
- `go list -m -json all` 将 package import path 映射到最终 Go module。

例如：

```text
import "github.com/gin-gonic/gin"
  -> Import:go:github.com/gin-gonic/gin
  -> Package:gomod:github.com/gin-gonic/gin@v1.10.0
```

扫描后需要过滤 Go 标准库、当前 module 内 package 和 `go.work` 中的本地
workspace module。不能只遍历所有 `.go` 文件，因为 build tags 可能使部分文件
在当前目标平台无效。

#### JavaScript / TypeScript

使用 TypeScript Compiler API 统一解析 `.js`、`.jsx`、`.ts` 和 `.tsx`，
提取：

- `import ... from "..."`；
- `export ... from "..."`；
- 字符串字面的 `require("...")`；
- 字符串字面的 `import("...")`。

外部包名按 package specifier 归一化：

```text
react                  -> react
lodash/fp              -> lodash
@scope/pkg/subpath     -> @scope/pkg
./local                -> 本地模块，不生成外部 Package
../utils               -> 本地模块
```

还要读取 `tsconfig.json` 的 `baseUrl`、`paths` 和 project references，防止把
路径别名误判为 npm 包。`require(variable)` 或 `import(variable)` 无法静态确定，
只记录为低置信度动态 Import，等待执行证据补充。

#### Rust

解析 Rust AST 中的：

- `use` declaration；
- `extern crate`；
- 能确定根路径的外部 crate 引用。

提取路径第一段后，必须与 `Cargo.toml` 的 dependency key 和
`cargo metadata` 联合判断，因为第一段也可能是本地 module，并且 Cargo 支持
依赖重命名：

```toml
[dependencies]
json = { package = "serde_json", version = "1" }
```

```text
use json::Value
  -> Import:cargo:json
  -> Package:cargo:serde_json
```

`crate`、`self`、`super` 和本地 `mod` 需要过滤。宏生成代码、条件 feature 和
build script 中的动态依赖不能仅靠源码 `use` 完整发现，因此
`Cargo.toml`/`cargo metadata` 仍是权威来源。

#### Java

解析 Java compilation unit 中的：

- 普通 import；
- static import；
- 源码中出现的全限定类名；
- main source、test source 和 generated source 的 scope。

Java Import 指向类或 package，而 Maven/Gradle Package 使用
`groupId:artifactId:version`，二者通常不能只根据名称直接映射：

```text
com.fasterxml.jackson.databind.ObjectMapper
  -> com.fasterxml.jackson.core:jackson-databind
```

推荐在 Maven/Gradle Resolver 得到依赖 JAR 后建立 class-to-JAR index，再将
Java Import 映射到包含该类的 Package 节点。编译成功后可以使用 `jdeps` 对
`.class` 或 JAR 做第二次静态依赖分析；`jdeps` 不能替代初始源码扫描，因为它
要求先有编译产物。reflection、ServiceLoader 和 annotation processor 仍需由
Manifest、构建配置或运行失败补充。

#### 统一 Provider 接口

每个 EcosystemProvider 增加：

```python
def scan_imports(
    workspace: Workspace,
    target: ResolutionTarget,
) -> tuple[ImportRef, ...]:
    ...

def map_imports(
    imports: tuple[ImportRef, ...],
    resolution: ResolutionResult,
) -> tuple[Edge, ...]:
    ...
```

统一构图顺序为：

```text
Manifest 扫描
  -> 生成 Requirement
源码静态扫描
  -> 生成 Import
原生 Resolver
  -> 生成 Package 和依赖闭包
Provider mapping
  -> 连接 Import/Requirement 到 Package
编译、测试和运行时 Probe
  -> 补充静态分析遗漏的依赖
```

静态 Import 扫描提供的是“源码使用证据”，不能代替 Manifest 和 Resolver。
第一版建议按 Go、JS/TS、Rust、Java 的顺序实现：Go 的映射最直接；Java 则
必须等 Maven/Gradle 解析出 classpath 后，才能可靠映射到 artifact。

### 4.3 节点 ID 和包身份

节点 ID 必须带生态和 workspace：

```text
runtime:python:3.11
runtime:node:20
runtime:java:17
runtime:go:1.24
runtime:rust:1.82

tool:pypi:uv
tool:npm:npm
tool:cargo:cargo
tool:gomod:go
tool:maven:maven

project:pypi:.
project:npm:frontend
project:cargo:native

deps:pypi:.
deps:npm:frontend
deps:cargo:native

req:npm:frontend:react
pkg:npm:<resolver-locator>
```

`Requirement` 保持稳定身份，保存仓库原始声明；`Package` 使用 Resolver
提供的 locator 标识实际解析结果。不能把所有生态都强制压缩成稳定的
`pkg:<name>`，因为 npm 和 Cargo 可能同时选择同名包的多个版本，npm 的 peer
dependency 上下文也可能产生不同实例。

推荐关系为：

```text
Project
  └─requires─> Requirement(react ^18)
                   └─resolves_to─> Package(react 18.3.1)
```

直接 Manifest 声明使用稳定的 Requirement 节点。Resolver 成功后增加
`resolves_to` 边；Resolver 失败时 Requirement 仍然保留，并记录
`resolution_status` 和 `resolution_error`。传递依赖可以继续使用
`Package --requires--> Package`，并在 edge data 中保存请求的版本约束和 scope。

### 4.4 DependencySet 安装事务

Package 节点负责描述解析闭包，DependencySet 节点负责执行安装：

```text
Project:npm:frontend
  ├─requires─> Runtime:node:20
  ├─requires─> Tool:npm
  └─requires─> DependencySet:npm:frontend
                    ├─describes─> Package:react
                    └─describes─> Package:vite
```

```json
{
  "id": "deps:npm:frontend",
  "setup_commands": ["cd frontend && npm ci"],
  "check_command": "cd frontend && npm ls --all --json >/dev/null",
  "lock_digest": "sha256:..."
}
```

非 Python Package 节点默认不直接生成命令。第一阶段可以继续保留 Python
Provider 当前的逐包安装策略，以保护现有 RAT Python 基线；其他生态先通过
DependencySet 执行。后续再决定是否把 Python 也统一成依赖事务。

### 4.5 Python + Rust 示例

```text
Test
  └─requires─> Python Project
                  ├─requires─> Python Runtime 3.11
                  ├─requires─> Python DependencySet
                  └─requires─> Rust Project
                                  ├─requires─> Rust Runtime 1.82
                                  ├─requires─> Cargo
                                  └─requires─> Cargo DependencySet
```

Python Project 对 Rust Project 的 `requires` 边保证 Rust 组件先完成，再执行
Python editable install。跨语言依赖只需要表达执行和检查顺序，不需要再实现
一个跨语言版本求解器。

### 4.6 Layer 顺序

将 Python 专属的 `Layer.PIP` 逐步迁移为 `Layer.DEPENDENCIES`，并增加
`Layer.BUILD`：

```text
PLATFORM
  -> SYSTEM
  -> RUNTIME
  -> TOOLCHAIN
  -> DEPENDENCIES
  -> BUILD
  -> CONFIG / SERVICES
  -> TESTS
```

`Layer.PIP` 可在迁移期保留为兼容别名，但新 Provider 不应再依赖它。

## 5. Build Script 生成

DepGraph 负责描述“需要什么以及依赖顺序”，Provider 负责描述“具体怎么安装和
检查”。Build Script 仍使用当前的 Topological Sort，但命令生成需要从 Python
专属映射改为 Provider 驱动。

`populate_setup_commands()` 不应看到一个有版本的 Package 就默认生成
`pip install`；它应调用该节点所属的 Provider，或者直接使用 Provider 在构图
阶段写入的 `setup_commands` 和 `check_command`。

建议执行顺序：

```text
1. System packages
2. Language runtimes
3. Compilers and package managers
4. Dependencies of each language
5. Repository project installation/build
6. Configuration
7. Tests
```

例如 Python + Rust：

```bash
apt-get update
apt-get install -y curl build-essential pkg-config

# Rust toolchain
curl https://sh.rustup.rs ...
rustc --version
cargo --version

# Python dependencies
python -m pip install -r requirements.txt

# Project build
python -m pip install -e .
```

不同生态的依赖事务由对应 Provider 生成：

| 生态 | Resolver/图来源 | 安装命令 | 验证命令 |
|---|---|---|---|
| Python | `uv lock`、`uv.lock` | 当前 pinned pip closure；后续可用 `uv sync` | distribution/import check |
| Go | `go list -m -json all` + `go mod graph` | `go mod download` | `go mod verify` |
| Rust | `cargo metadata --format-version 1` | `cargo fetch --locked` | `cargo metadata --locked` |
| JS/TS | 对应 npm/pnpm/Yarn lockfile | `npm ci`、`pnpm install --frozen-lockfile`、`yarn install --immutable` | `npm ls --all --json` 或对应 manager check |
| Java/Maven | `dependency:tree -DoutputType=json` | `dependency:go-offline` | Maven offline resolve/build |
| Java/Gradle | Tooling API 或临时 init script 的 `ResolutionResult` | Gradle wrapper | dependency locking/offline build |

每个 Block 继续关联对应的 DepGraph node，因此失败后仍然可以直接定位节点。

### 5.1 各生态 Resolver 的边界

- Go 自带 MVS Resolver。`go.mod` 和 `go.sum` 是可复现输入，不应把
  `go mod tidy` 当作普通只读解析命令，因为它可能修改仓库；
- Cargo 的 `cargo metadata` 提供机器可读解析图，优先使用 `Cargo.lock`；
  缺少锁文件时只能在临时副本生成，并把生成结果作为 resolution artifact；
- JS/TS 必须根据 `packageManager` 字段和 lockfile 选择 npm、pnpm 或 Yarn。
  已有 lockfile 时优先静态解析；缺少 lockfile 时可在临时环境中使用
  `--package-lock-only --ignore-scripts` 生成候选结果；
- Java 不是单一 Resolver。Maven 和 Gradle 必须作为两个 Provider；
- Maven 和未启用 dependency locking 的 Gradle 对动态版本的可复现性较弱，
  DepGraph 必须记录解析结果、输入摘要和 reproducibility confidence。

### 5.2 EcosystemProvider

新增统一 Provider 接口：

```python
class EcosystemProvider(Protocol):
    def detect_workspaces(repo) -> tuple[Workspace, ...]: ...
    def detect_runtime(workspace) -> RuntimeRequirement: ...
    def resolve(workspace, target, sandbox) -> ResolutionResult: ...
    def to_graph(result) -> DepGraphFragment: ...
    def dependency_block(result) -> Block: ...
    def project_blocks(workspace) -> tuple[Block, ...]: ...
```

目录建议为：

```text
src/ecosystems/
  base.py
  registry.py
  python/
  go/
  rust/
  node/
  maven/
  gradle/
```

总入口不再直接调用一个 Python `resolve_closure()`，而是检测全部 workspace，
调用相应 Provider，最后按稳定 ID 合并各个 `DepGraphFragment`。

### 5.3 Resolver Sandbox

当前 Python `uv` 可以在 Host 上针对目标 Python/平台解析，但不能把所有生态
都照搬为 Host 执行。npm 的 optional dependency、Cargo target、Go CGO 以及
Gradle 配置逻辑都可能依赖目标环境，Gradle/Maven 还可能执行项目构建逻辑。

因此需要新增独立的 Resolver Sandbox：

```text
选定 Base Image
  -> 创建临时 Resolver Container
  -> 将仓库复制到临时工作区
  -> 不注入宿主凭证
  -> 执行各生态 Resolver
  -> 取回 ResolutionResult、lock artifact 和 digest
  -> 删除临时容器
```

Resolver Sandbox 与 Execute Agent 的 candidate container 职责不同：前者
只负责初始确定性解析，不运行 LLM，也不会直接成为 working container。

## 6. Execute Agent

多语言改造不新增 Agent 控制流。Execute Agent 继续采用现有流程：

```text
Block 执行失败
  -> 定位失败 Block
  -> 找到对应 DepGraph Node
  -> 构建 RepairScope
  -> Agent 执行只读 Probe
  -> Agent 提交 PatchProposal
  -> PatchGate 检查
  -> 从最近 Checkpoint 创建 Candidate Container
  -> 验证修复
  -> 成功则提交，失败则回滚
```

需要调整的只是 `RepairScope`，使其携带以下语言和生态信息：

```text
language
language_role
version_constraint
manifest_file
package_manager
base_image
failed_node
failed_command
```

例如 `node-gyp` 编译失败时，Agent 可以判断缺少的是：

- Python；
- Make；
- C++ compiler；
- 某个系统头文件。

修复仍然必须写入 DepGraph，不能只在容器里临时执行命令。

## 7. Clean Replay

增量构建和候选修复通过后，继续执行现有 clean replay：

```text
固定 Base Image
  -> 创建全新容器
  -> 从头执行最终 Build Script
  -> 执行节点检查
  -> 运行测试
```

只有 clean replay 成功，才能证明最终 Build Script 已完整记录多语言环境的
构建过程，并且能够从固定 Base Image 复现。

## 8. 需要修改的主要文件

| 文件 | 修改内容 |
|---|---|
| `src/language_handlers.py` | 返回全部语言、版本和角色 |
| `src/image_selector.py` | 接收语言集合，而不是单个语言 |
| `src/envstate/base_image_selection.py` | 实现多语言 Base Image 规则 |
| `src/python_deps/depgraph/schema.py` | 增加 Ecosystem、Requirement、DependencySet 和通用解析字段 |
| `src/python_deps/depgraph/ids.py` | 实现带 ecosystem/workspace/locator 的稳定 ID |
| `src/python_deps/depgraph/build.py` | 改为 Provider 注册表驱动，合并多语言 Import、Requirement 和解析子图 |
| `src/python_deps/depgraph/populate.py` | 移除非 PyPI Package 的默认 pip 命令，改为 Provider 命令 |
| `src/python_deps/depgraph/emit.py` | 支持 Runtime、DependencySet 和 Build 节点的可执行性 |
| `src/python_deps/depgraph/certify.py` | 使用通用 Layer 顺序和 Provider checks |
| `src/python_deps/depgraph/execution_plan.py` | 按 Runtime、Toolchain、Dependencies、Build 顺序编译 |
| `src/ecosystems/` | 新增五种语言、六个包管理生态的 Provider、静态 Import scanner 和 mapping |
| `src/sandbox.py` | 增加隔离的 Resolver Container 接口 |
| `src/envstate/repair_scope.py` | 携带语言、版本、manifest 和包管理器信息 |
| `scripts/run_v3_e2e.py` | 串联多语言检测、镜像选择和 Graph 构建 |
| `src/envstate/orchestrator.py` | 调度 Resolver Sandbox，之后继续复用现有事务式执行 |

现有的以下模块基本可以直接复用：

- `GraphExecuteAgent`；
- `PatchGate`；
- `IncrementalPlanExecutor`；
- candidate container；
- checkpoint；
- transaction commit/rollback；
- run trace；
- clean replay。

## 9. 实施步骤

为降低回归风险，建议按照“先扩展发现与图模型，再接入执行”的顺序实施。

### 第一阶段：多语言检测和镜像选择

- 实现 `detect_languages()`；
- 提取常见语言版本；
- 判断主要运行时和编译工具；
- 按规则选择 Base Image。

### 第二阶段：通用 DepGraph

- 增加 Ecosystem、Requirement 和 DependencySet；
- 将 Import 推广为带 language、workspace、scope 和 confidence 的多语言节点；
- 增加 `maps_to`、`resolves_to`、`describes` 等非执行关系及其 endpoint 规则；
- 修改 Package 身份和 ID，支持 npm/Cargo 多版本；
- 将 `Layer.PIP` 迁移为通用 Dependencies/Build 层；
- 保持 Python 现有路径兼容。

### 第三阶段：Provider 和 Build Script

- 实现 Provider 注册表和 Resolver Sandbox；
- 依次接入 Rust、Go、npm、Maven 和 Gradle；
- 为 Go、JS/TS、Rust 和 Java 实现 `scan_imports()` 与 `map_imports()`；
- 将源码 Import、Manifest Requirement 和 Resolver Package 汇合到同一子图；
- 由 Provider 生成 DependencySet 与 Project Build Block；
- 确保 Topological Sort 顺序正确。

### 第四阶段：Execute Agent 上下文

- RepairScope 增加语言和 manifest 信息；
- 增加 Node、Rust、Go、Maven 和 Gradle 等诊断 probe；
- 保持现有事务式修复流程。

### 第五阶段：测试

先为每个生态测试一个单语言仓库：

1. Python；
2. Rust；
3. Go；
4. JS/TS；
5. Java Maven；
6. Java Gradle。

再测试三个典型 polyglot 场景：

1. Python + Rust 扩展；
2. Java 后端 + Node 前端；
3. Go 服务 + Node 代码生成或前端。

每个场景都必须覆盖初始解析、Build Plan、失败节点定位、candidate transaction、
checkpoint 恢复和 clean replay。

静态扫描还必须分别覆盖：

- Python import name 与 PyPI distribution 名称不一致；
- Go build tags、标准库和 `go.work` 本地 module 过滤；
- JS/TS 相对路径、scoped package、subpath、tsconfig path alias 和动态 import；
- Rust 本地 module、dependency rename、feature 和 macro 边界；
- Java source import 到 Maven/Gradle JAR class index 的映射；
- 静态扫描遗漏后由编译或测试证据写回 DepGraph。

## 10. 最终方案概括

本次改造既不需要推翻现有 GraphExecuteAgent，也不只是增加几条安装命令：

> 原来系统用 Python 专属 Resolver 构造一张 Python DepGraph；现在由多个
> EcosystemProvider 分别解析各自 workspace，把 Requirement、Package、
> DependencySet、Runtime 和 Toolchain 合并到同一张图，再继续使用现有
> Build Plan、checkpoint repair 和 clean replay。

不需要实现一个跨语言版本求解器，因为版本选择仍由 uv、Go Modules、Cargo、
npm/pnpm/Yarn、Maven 和 Gradle 负责。系统新增的核心工作集中在：

```text
多语言检测
+ Base Image 规则
+ 通用 DepGraph schema
+ EcosystemProvider
+ Resolver Sandbox
+ DependencySet 安装事务
+ 跨 workspace requires 边
```

在补充语言上下文和 Provider 检查后，后续的执行、修复、事务验证和
clean replay 可以继续复用当前实现。
