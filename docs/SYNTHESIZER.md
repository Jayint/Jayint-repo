# Synthesizer 在做什么

本文基于对根项目代码的阅读整理，重点覆盖 `src/synthesizer.py`，并结合它在 `agent.py`、`src/sandbox.py`、`multi_docker_eval_adapter.py` 以及相关测试中的调用方式说明。仓库里的 `outputs/`、`workplace/`、`eval_output/`、`失败例子/`、`others_work/`、`Multi-Docker-Eval/` 等目录主要是运行产物、外部项目副本或 benchmark 代码，不是本项目 `Synthesizer` 的实现主体。

## 一句话概括

`Synthesizer` 是这个 Docker 环境配置 Agent 的“轨迹编译器”。

它把 Planner 在 Sandbox 中探索出来的一串 bash 动作，整理成后续可复现的构建配方：

- 从成功命令里提取真正需要固化进 Docker image 的 setup/build 步骤。
- 过滤掉只读诊断、测试命令、健康检查、纯运行时服务启动等不该写进 Dockerfile 的动作。
- 判断某条命令是不是测试命令，以及它的 observation 是否真的证明测试被执行过。
- 在最终成功后调用 LLM，把完整探索轨迹综合成结构化 `build_recipe`。
- 根据最终 `build_recipe` 生成中间 Dockerfile，并让 adapter 继续转换成 Multi-Docker-Eval 需要的 Dockerfile 和 eval script。

换句话说，Planner 负责“下一步做什么”，Sandbox 负责“真实执行命令”，而 Synthesizer 负责“哪些已执行事实应该变成可回放工件”。

## 在系统中的位置

核心调用链如下：

```text
DockerAgent
  -> ImageSelector 选择基础镜像
  -> Planner 生成单步 Action
  -> Sandbox 执行 Action
  -> Synthesizer 记录/分类成功 Action
  -> DockerAgent 聚合最终 Verification Bundle
  -> Synthesizer 生成 build_recipe 和 Dockerfile
  -> MultiDockerEvalAdapter 生成 benchmark Dockerfile / eval_script
```

`Synthesizer` 在运行中被三个地方使用：

1. `agent.py`
   - 初始化 `self.synthesizer = Synthesizer(base_image=base_image)`。
   - 每条成功命令后调用 `record_success(action)`，收集候选 Dockerfile 指令。
   - 用 `command_mutates_environment()`、`analyze_test_run()`、`is_runtime_service_command()` 等结果维护验证状态。
   - 最终成功时调用 `synthesize_build_recipe()`，再调用 `generate_dockerfile()`。

2. `src/sandbox.py`
   - 内部创建一个 `Synthesizer()` 作为命令分类器。
   - 用它识别运行时服务命令，决定 rollback 后哪些服务启动命令要重放。
   - 用它识别被 `head` / `tail` 截断的测试输出，并在 observation 前加系统警告。

3. `multi_docker_eval_adapter.py`
   - 读取 Agent 生成的 Dockerfile 中的 `RUN` 指令。
   - 读取 `build_recipe.post_test_patch_commands`，在 test patch 应用后执行必要重建。
   - 复用 `quote_shell_sensitive_package_specs()` 和 apt bootstrap helper，保证 eval Dockerfile 更稳定。

## 它维护的核心状态

`Synthesizer` 本身状态很小：

- `base_image`：最终 Dockerfile 的 `FROM` 镜像。
- `workdir`：默认 `/app`，最终 Dockerfile 的 `WORKDIR`。
- `instructions`：在线规则从成功命令中抽出的 `RUN ...` 指令列表。
- `build_recipe`：最终由 LLM recipe synthesis 得到的结构化配方。

`instructions` 是运行过程中的规则候选集；`build_recipe` 是最终产物语义的权威版本。最终 `apply_build_recipe()` 会清空原来的 `instructions`，再把 `build_recipe["build_commands"]` 重新写成 `RUN` 指令。

## 四类命令的职责划分

这个模块最重要的设计，是把探索轨迹里的命令分成不同生命周期：

| 类别 | 含义 | 最终去向 |
| --- | --- | --- |
| `build_commands` | 会持久改变镜像的依赖安装、编译、文件修补等命令 | 写入 Dockerfile 的 `RUN` |
| `runtime_preparation_commands` | 只在容器运行时有效的准备动作，如启动 Redis/PostgreSQL | 写入 eval script，测试前执行 |
| `test_commands` | 最终证明环境可用的测试命令 | 写入 eval script |
| `post_test_patch_commands` | benchmark 的 `test_patch` 应用后才需要运行的重建/安装命令 | 注入 patched eval Dockerfile |

这四类命令不能混在一起。比如 `service redis-server start` 对当前容器有用，但它不应该在 Docker build 阶段运行；`pytest tests` 能证明环境成功，但也不应该固化进 Dockerfile；`npm install` 则通常属于镜像构建步骤。

## 在线阶段：从成功命令中抽取候选 RUN

每当 Sandbox 执行命令成功，`DockerAgent.run()` 会调用：

```python
self.synthesizer.record_success(action)
```

`record_success()` 的核心流程是：

1. `_extract_recordable_setup_commands(command)` 判断这条成功命令里是否有可记录的 setup/build 部分。
2. 如果有，就调用 `_record_setup_instruction()` 写入 `self.instructions`。
3. 写入前会做 pip requirement shell quoting，避免 `pip install packaging>=24` 被 shell 当成重定向。
4. 重复的 `RUN` 指令不会重复记录。

它不是简单地把成功命令整体加上 `RUN`。它会做一层命令语义过滤。

### Shell 拆分

`Synthesizer` 自己实现了轻量 shell 拆分：

- `_split_shell_chain()` 按 `&&`、`||`、`;`、换行拆命令段，同时尊重单双引号和转义。
- `_split_pipeline()` 按单个 `|` 拆 pipeline，同样尊重引号和转义。
- `_normalize_command_segment()` 会小写、去掉前置环境变量赋值、去掉 `time` 前缀。

这不是完整 shell parser，但足够支撑本项目里常见的一行安装、构建、测试、诊断命令分类。

### 过滤规则示例

| 原始成功命令 | 记录结果 | 原因 |
| --- | --- | --- |
| `pip install -e . && pytest tests` | `RUN pip install -e .` | 测试前的 setup 前缀需要保留，测试本身不进 Dockerfile |
| `cd backend && npm install && npm test` | `RUN cd backend && npm install` | `cd` 本身不记录，但当后续 setup 依赖它时保留 |
| `cd build && ctest --output-on-failure` | 不记录 | 只有目录切换加测试，没有持久 setup |
| `apt-get install -y redis-server && service redis-server start` | `RUN apt-get install -y redis-server` | 服务启动是运行时行为，不写进 build layer |
| `redis-server --daemonize yes && sed -i "s/foo/bar/" app.py` | `RUN sed -i "s/foo/bar/" app.py` | 纯运行时服务段丢弃，文件修改保留 |
| `grep -r foo . \| head -10` | 不记录 | 只读诊断 pipeline |
| `mv file.tar.gz.1 file.tar.gz && tar -xzf file.tar.gz && mv ... /opt/...` | 不记录 | 被识别为脏 workspace 上的临时归档修复 |

测试文件 `tests/test_synthesizer.py` 对这些场景有直接覆盖。

## 只读命令识别

`_is_readonly_command()` 用于判断命令是否只是查看信息。它会检查每个 shell segment / pipeline component：

- 常见只读命令：`ls`、`cat`、`pwd`、`grep`、`find`、`head`、`tail`、`wc`、`awk`、`env`、`which`、`file`、`stat` 等。
- 常见版本探测：`python --version`、`pip -v`、`node --version`、`mvn -v`、`git version` 等。
- 如果存在普通输出重定向 `>`，就不再当作只读，因为这会写文件。
- 对 `2>/dev/null`、`&>/dev/null` 这类静默输出的重定向会特殊忽略，用于避免误判版本探测。

这个判断被多个地方复用：

- `record_success()` 不记录只读命令。
- `DockerAgent` 判断某条成功动作是否会破坏已有验证块。
- `Sandbox` 决定是否需要为命令结果创建快照。

## 测试命令识别和有效性判断

`Synthesizer` 还承担“测试是否真的执行了”的判定工作。

### `is_test_command()`

它先排除只读命令，再用跨语言模式识别测试命令，例如：

- Python：`pytest`、`py.test`、`python -m pytest`、`python -m unittest`、`tox`。
- Node.js：`npm test`、`yarn test`、`pnpm test`、`jest`、`mocha`、`vitest`。
- Rust / Go / Java：`cargo test`、`go test`、`mvn test`、`gradle test`。
- Ruby / PHP：`bundle exec rspec`、`rake test`、`phpunit`、`pest`。
- C / C++：`ctest`、`cmake --build ... --target test`、`make test/check`。
- 直接执行的测试二进制：如 `./FooTests`、`/path/to/bar_test`。

### `analyze_test_run()`

只看命令名还不够，所以它会结合 observation 判断测试是否有效执行：

- 看到 `collected 12 items`、`3 passed`、`Tests run: ...`、`OK (94 tests, ...)`、`test result: ok` 等，判为高置信有效测试。
- 看到 `no tests found`、`collected 0 items`、`[no test files]` 等，判为空跑。
- 看到 `usage:`、`optional arguments:` 等，判为帮助文本，不算测试。
- 如果测试命令通过 `head` / `tail` 截断输出，判为无效验证，避免隐藏失败。
- 对某些直接测试二进制，只要有输出但没有明确 summary，会给中等置信。

`DockerAgent` 用这个结果维护 `verified_test_commands`。也就是说，最终成功不是模型说了算，而是必须有被 `Synthesizer` 或 agent-reported verification bundle 接受的测试命令。

## 环境变更和运行时服务判断

`command_mutates_environment()` 用来判断成功命令是否改变了有效运行环境。它会识别：

- 安装/构建命令：`pip install`、`npm install`、`cargo build`、`go mod download`、`mvn install`、`bundle install`、`composer install`、`cmake`、`make`、`apt-get install` 等。
- 文件系统修改：`mkdir`、`rm`、`cp`、`mv`、`ln`、`chmod`、`sed`、`patch`、`git apply`、`git checkout` 等。
- 运行时服务启动：`service xxx start`、`redis-server`、`rabbitmq-server -detached`、`mongod --fork`、`nginx` 等。

这对最终验证非常关键：如果测试通过之后又执行了新的环境变更，之前的测试结果就不再能证明最终环境，`DockerAgent` 会清空当前 verification block。

此外，模块还区分：

- `is_runtime_service_command()`：启动/停止/重启服务。
- `is_runtime_healthcheck_command()`：`redis-cli ping`、`pg_isready`、`mysqladmin ping`、`curl localhost` 等健康检查。

健康检查不会进入 runtime preparation；服务启动可能会进入 runtime preparation，由最终 verification bundle 决定。

## 最终阶段：LLM 合成 build_recipe

在线记录的 `instructions` 是规则候选集，但最终成功后，系统还会调用一次 LLM 做全局配方合成：

```python
self.synthesizer.synthesize_build_recipe(
    self.client,
    self.model,
    recipe_input,
    log_dir=self.setup_log_dir,
)
```

`recipe_input` 由 `DockerAgent._build_recipe_synthesis_input()` 构造，包含：

- 任务信息：repo URL、base commit、base image、workdir、语言、检测到的本地服务。
- `problem_statement` 和 `test_patch`。
- 最终 `verification_bundle`。
- 完整轨迹或压缩后的轨迹摘要。
- 成功动作、失败动作、当前规则候选 `current_rule_based_candidates`。

LLM 必须返回严格 JSON，包含这些键：

```json
{
  "build_commands": [],
  "post_test_patch_commands": [],
  "runtime_preparation_commands": [],
  "test_commands": [],
  "excluded_commands": [],
  "rationale": "",
  "confidence": "high"
}
```

这里的目标不是让模型自由写 Dockerfile，而是让它在“真实执行轨迹”基础上做一次全局清理：

- 排除失败命令。
- 排除只读诊断、版本检查、健康检查和最终测试命令。
- 保留成功安装/构建步骤。
- 对 `A && B && pytest` 这类混合命令，保留测试前必要 setup。
- 如果 `test_patch` 应用后需要重建，把命令放进 `post_test_patch_commands`。

`normalize_build_recipe()` 只做形状规范化、去重和 `RUN` 前缀剥离。它会优先保留输入里的最终 verification bundle，确保 runtime preparation 和 test commands 不被 LLM 改写掉。需要注意的是，它不会再对 LLM 的 `build_commands` 做强语义过滤；系统 prompt 明确说明 host code 信任 recipe 语义。

如果 LLM 调用失败或 JSON 无法解析，`build_fallback_recipe()` 会回退到在线规则记录下来的 `instructions`，并把 confidence 标为 `low`。

## Dockerfile 生成

`generate_dockerfile()` 生成的 Dockerfile 结构很简单：

```dockerfile
FROM <base_image>
WORKDIR <workdir>

RUN printf '%s\n' ... > /etc/apt/apt.conf.d/99jayint-retries
...
RUN <build command 1>
RUN <build command 2>
```

它会固定加入 apt 可靠性配置：

- `Acquire::Retries`
- HTTP / HTTPS timeout
- `Acquire::http::Pipeline-Depth "0"`

如果设置了 `JAYINT_APT_MIRROR_URL` 或 `APT_MIRROR_URL`，还会生成 apt source mirror rewrite 和 `apt-get update`。

在 Multi-Docker-Eval 路径中，这个 Dockerfile 更像是“中间构建配方载体”。`MultiDockerEvalAdapter` 会读取其中的 `FROM` 和 `RUN` 指令，再生成 benchmark 需要的 Dockerfile：

- 设置 `WORKDIR /testbed`。
- 安装 `git`。
- `git clone` 目标仓库并 checkout `base_commit`。
- 把 Agent 的 `/app` 路径改写为 `/testbed`。
- 去掉重复的 apt bootstrap。
- 应用 `test_patch`。
- 如有 `post_test_patch_commands`，在 patch 后执行。

因此，adapter 路径下最终可评测 Dockerfile 不是 `Synthesizer.generate_dockerfile()` 的原样输出，而是经过 benchmark 语义重组后的结果。

## 它不负责什么

为了避免误解，`Synthesizer` 不负责这些事情：

- 不执行命令，命令执行由 `Sandbox` 负责。
- 不决定下一步探索动作，决策由 `Planner` 负责。
- 不选择基础镜像，镜像选择由 `ImageSelector` 负责。
- 不直接运行 Multi-Docker-Eval，评测适配由 `MultiDockerEvalAdapter` 和 `run_verified_regression.py` 负责。
- 不完整解析 shell 语法，只覆盖常见 setup/test/diagnostic 命令模式。
- 不保证 LLM 合成的 `build_commands` 一定语义正确；它只做结构规范化，语义约束主要依赖 prompt、真实轨迹输入和测试覆盖。
- 独立生成的 Dockerfile 不包含 `COPY . /app` 或 `git clone`；在当前 benchmark 主链路里，仓库克隆由 adapter 生成的 eval Dockerfile 补上。

## 为什么需要这个模块

如果没有 `Synthesizer`，系统容易出现三类问题：

1. 把测试命令写进 Dockerfile，导致镜像构建阶段就运行测试，职责混乱。
2. 把只读诊断、健康检查、服务启动等临时动作固化进镜像，导致不可复现或构建失败。
3. 仅凭 LLM 最终一句 “Success” 判断环境成功，无法确认测试是否真实执行。

`Synthesizer` 通过命令分类、测试信号解析和 recipe 合成，把“探索过程”转换成“可构建、可运行、可评测”的结构化产物，是整个 Agent 从在线试错走向离线复现的关键模块。

## 相关测试覆盖

主要测试文件是 `tests/test_synthesizer.py`，覆盖内容包括：

- 从混合 setup + test 命令中抽取 setup 前缀。
- 保留依赖目录切换的 setup 命令。
- 丢弃导航-only、只读 pipeline、健康检查和运行时服务段。
- 识别多语言测试命令及真实测试输出。
- 拒绝空测试、帮助文本和截断测试输出。
- 处理 pip requirement 中的 shell 敏感比较符。
- 生成 apt bootstrap Dockerfile 片段。
- LLM recipe JSON 提取、规范化、日志记录和 fallback 行为。

相关联的测试还包括：

- `tests/test_agent_verification.py`：验证 `Synthesizer` 的分类结果如何影响最终 verification block。
- `tests/test_sandbox.py`：验证 runtime service replay、测试输出截断警告和 apt bootstrap。
- `tests/test_adapter_logic.py`：验证 adapter 如何消费 `build_recipe`、Dockerfile 指令和 post-test-patch commands。
