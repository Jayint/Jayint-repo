# Repo2Run 剩余 53 条失败样例分析

日期：2026-05-24

本报告分析 `outputs/repo2run_benchmark/results` 下当前仍失败的 53 条样例。失败口径使用 `environment_build_success != true`，而不是 `execution_status != success`，因为成功样例在结果文件里通常记录为 `environment_built`。

## 总体统计

结果文件总数：420

成功：367

失败：53

失败状态分布：

| 状态 | 数量 | 含义 |
| --- | ---: | --- |
| `dockerfile_missing` | 47 | sandbox/agent 阶段没有产出被接受的 Dockerfile。 |
| `docker_build_failed` | 3 | Dockerfile 已生成，但 `docker build` 阶段失败。 |
| `test_execution_failed` | 3 | Docker build 成功，但最终 eval 镜像内的 Repo2Run 风格 pytest collection 失败。 |

核心现象：大量 `dockerfile_missing` 不是 Docker build 问题，而是在更早的阶段失败，例如 agent 没有产出被接受的 `Verification Bundle`、clone/checkout 失败、或者 pytest collection 从未被可靠验证成功。

## 逐条失败分析

| # | 样例 | 状态 | 失败原因 | 修复方向 |
| ---: | --- | --- | --- | --- |
| 21 | `alvin-r/databonsai` | `dockerfile_missing` | 没有被接受的 pytest collection 结果。运行后期陷入 final-answer/protocol 循环，最后 raw output 被当成 shell 命令执行，并触发 `unexpected EOF while looking for matching '`。早期 collection 还显示缺少 `pandas` 等测试依赖。 | 安装未声明的测试依赖，重新执行普通 `pytest --collect-only -q --disable-warnings`；如果已经有可靠 collection 成功信号，应由 host 侧 auto-finalize，避免让 LLM 反复尝试 final bundle 格式。 |
| 31 | `apple/ml-cross-entropy` | `dockerfile_missing` | agent 尝试声明成功，但 bundle 被拒绝，因为它把 `git config --global --add safe.directory /app` 放进 runtime preparation，而该命令没有在 final environment 中被观测为成功。记录的 pytest 尝试均为 `no_reliable_test_execution_signal`。 | 将 `git config` 和 pytest 拆成独立可观测 action；最终 `test_commands` 只放已经真实成功的 pytest 命令，validator 不应接受未观测成功的 runtime prep 命令。 |
| 32 | `apple/ml-mdm` | `dockerfile_missing` | 安装 `tensorboard` 后 setup 被 `Connection error` 中断。中断前 collection 仍有真实错误：缺少 `/app/tests/data/bert.vocab`，以及安装 tensorboard 前缺少 `torch.utils.tensorboard`。没有最终 collection 成功信号。 | 复用当前 workplace 继续修；把 `data/` 中对应 vocab 文件复制或链接到 `tests/data/` 期望位置，然后重新跑 collection。 |
| 63 | `chernyadev/bigym` | `dockerfile_missing` | agent 反复输出 Markdown/tool-call 形态的命令，例如 `** \`python3 ...\``，executor 将其当成字面 shell 命令执行，300 步几乎没有有效 setup action。 | 加强 action extraction，执行前剥离 Markdown、反引号、tool-call 包装；或者换用能稳定输出普通 `Action:` 行的 planner/model 路径。 |
| 74 | `COLA-Laboratory/TransOPT` | `dockerfile_missing` | 同时存在真实 import 问题和 final bundle 拒绝。后期 import probe 失败于 `cannot import name 'DataManager' from transopt.datamanager`；agent 又声称 `pytest --collect-only -q --disable-warnings .` 成功，但该 exact command 没有被观测成功，最后以 `Connection error` 结束。 | 不应把手动 import probe 当成最终证明；先修包 API/import mismatch，再以真实 pytest collection 作为唯一成功信号。 |
| 77 | `computer-agents/agent-studio` | `dockerfile_missing` | `xvfb-run -a pytest --collect-only -q --disable-warnings` 已收集到 48 个 tests，但 LLM 请求在输出可接受 final bundle 前超时。 | 对 planner timeout 后已有高置信 verified test command 的场景做 salvage，直接基于成功 trajectory 合成 Dockerfile。 |
| 123 | `fedirz/faster-whisper-server` | `dockerfile_missing` | sandbox setup 前 clone 失败。所有 clone 策略都失败于 `fatal: couldn't find remote ref cbb6c9`。 | 数据集中的 commit 当前无法从公开仓库取到；需要更新 dataset commit、使用 mirror/cache，或标记为 source snapshot 不可恢复。 |
| 125 | `filipstrand/mflux` | `dockerfile_missing` | 所有 pytest collection 尝试均为 `test_failure_signal`；后期 agent 卡在 final bundle 格式尝试，并最终遇到 `Connection error`。 | 先查看并修复真实 collection 失败的依赖/import 问题；只有同一个普通 pytest 命令有高置信成功信号时才允许 final success。 |
| 137 | `getzep/graphiti` | `docker_build_failed` | 生成的 Dockerfile 语法非法。apt install wrapper 把多行 package list 拆坏，Docker 将 `gcc \` 当成顶层 Dockerfile 指令解析，报 `unknown instruction: gcc`。 | 修复 apt 命令归一化，确保多行 apt package list 仍位于同一个 `RUN` 指令内。 |
| 159 | `hpcaitech/Open-Sora` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref 38de63`。 | 更新或镜像该 source snapshot；否则应作为不可用 commit 处理。 |
| 176 | `Indoxer/LKAN` | `dockerfile_missing` | model 输出了错误的 tool-call 标记，例如 `cat Indoxer__LKAN/requirements.txt</Action>`，随后在 300 步内陷入 final-bundle/protocol 循环，没有 accepted verified collection。 | shell 执行前剥离 XML 风格 action tag；parser 修好后重新跑 setup。 |
| 177 | `Infini-AI-Lab/Sequoia` | `dockerfile_missing` | 运行中安装了较重的 PyTorch 依赖，但没有任何 verified collection 被接受；最终仍卡在 `Verification Bundle` 和 `Action:` 的协议冲突。 | 如果已存在高置信 collection 成功信号，host 侧应自动 finalize；否则先修 action parser 再重跑。 |
| 189 | `jialuechen/deepfolio` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref 15d247`。 | 更新或镜像 dataset commit。 |
| 207 | `kujirahand/tkeasygui-python` | `dockerfile_missing` | 真实 pytest collection 在导入 `TkEasyGUI` GUI 代码时出现 24 个错误；后续 headless 尝试使用了不可靠的 `|| echo` 命令，因此没有被接受。 | 配置真实 headless Tk/Xvfb 环境，并重新跑不带 `|| echo` 的普通 collection；不能接受被 mask 的命令。 |
| 210 | `landing-ai/vision-agent` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref 63eab8`。 | 更新或镜像 source snapshot。 |
| 225 | `lucasdelimanogueira/PyNorch` | `dockerfile_missing` | agent 花了大量步骤尝试绕过 MPI/OpenMPI C++ symbol 问题，包括编译 stub 和运行 `ldconfig`，但没有进入 pytest collection 成功阶段。 | 安装正确的 MPI/OpenMPI dev/runtime 包，不要依赖临时 stub；随后再跑 collection。 |
| 235 | `Marker-Inc-Korea/AutoRAG` | `dockerfile_missing` | collection/import 仍不通，因为安装的 `openai` 包没有在 `openai.types.chat` 下暴露 `ParsedChatCompletion`。没有 verified pytest collection。 | 将 `openai` pin 到 AutoRAG 期望版本，或安装仓库锁文件/test extras 中声明的精确依赖。 |
| 237 | `mbodiai/embodied-agents` | `dockerfile_missing` | 100 个 action 全部失败，因为 model 输出了 Markdown 前缀命令，例如 `** python3 ...`，没有真正成功执行 setup action。 | 修复 action parser 或 model output format 后重跑。 |
| 241 | `meta-llama/llama-stack-apps` | `dockerfile_missing` | 运行后期陷入 final-output protocol 循环。model 一直推理 `Verification Bundle` 格式，但没有输出被接受的 bundle，也没有 verified test command。 | 如果存在成功 collection 命令，应 deterministic finalize；否则用更严格 final-answer parser 重跑。 |
| 244 | `microsoft/MInference` | `dockerfile_missing` | pytest collection 失败，因为测试在 collection 阶段导入/下载 Hugging Face 模型配置 `gradientai/Llama-3-8B-Instruct-262k`，该 config 无法加载；随后运行遇到 `Connection error`。 | 提供离线模型 config/cache，或使用项目支持的方式避免 collection 阶段联网下载模型。 |
| 262 | `mobiusml/gemlite` | `dockerfile_missing` | agent 判断该项目是 CUDA/Triton kernel 库，测试文件在 collection 阶段创建 CUDA tensor；当前容器没有 NVIDIA GPU。没有 accepted verification bundle。 | 使用 GPU-capable eval 环境/基础镜像，或配置项目支持的 CPU fallback/跳过 GPU-only collection 路径。 |
| 269 | `muditbhargava66/PyxLSTM` | `dockerfile_missing` | agent 通过 shell `echo` 输出 verification bundle，而不是作为最终回答提交；validator 拒绝，因为 pytest 命令没有可靠成功信号。 | 重新执行真实 pytest collection，只报告已经观测成功的命令；不要把 echoed bundle 文本当成 final output。 |
| 270 | `narwhals-dev/narwhals` | `dockerfile_missing` | 存在高置信命令，但它是 compound command：`pip show polars && pytest ... /app/tests`。随后 agent 在 max steps 前未能输出 accepted final bundle。 | 依赖安装完成后归一化为纯 pytest collection 命令；从 verified test trajectory 自动 finalize。 |
| 276 | `NexaAI/nexa-sdk` | `dockerfile_missing` | clone 阶段失败。目标 ref `33f6ba` 多次 clone/fetch 均找不到，其中一次还出现 remote hangup。 | 更新或镜像 source snapshot。 |
| 278 | `Nike-Inc/koheesio` | `dockerfile_missing` | pytest 尝试被标记为 `no_reliable_test_execution_signal`；final answer 文本又被解析为混合/变更型 action 并被拒绝，没有生成 Dockerfile。 | 重跑普通 pytest collection，并修复 final-answer handling，避免 bundle 文本被当成 shell action。 |
| 279 | `nlmatics/nlm-ingestor` | `test_execution_failed` | Docker build 成功，但 eval 使用了字面量 workdir `${APP_HOME}`；测试 wrapper 执行 `cd '${APP_HOME}'`，因此报 `No such file or directory`。 | 写入 eval `workdir` 前解析 Docker `ENV` 变量，或直接使用 Dockerfile/agent summary 中的具体路径。 |
| 283 | `NUS-HPC-AI-Lab/VideoSys` | `dockerfile_missing` | 最后一步已经成功执行 `pytest --collect-only -q --disable-warnings`，summary 里也记录了一个 verified command，但 agent 在输出 final bundle 前到达 step limit。 | 当 max steps 到达且最后已有 collection 成功信号时，从最后的 verified test command 自动 finalize。 |
| 289 | `NVlabs/Sana` | `dockerfile_missing` | clone 成功，但 sandbox 初始化阶段把仓库复制进容器时失败：Docker SDK `put_archive` 抛出 `requests.exceptions.ConnectionError: OSError(22, 'Invalid argument')`。 | 这是 Docker transport/infrastructure 层问题；应重试 seeding、拆分大 archive，或使用 bind-mount/copy fallback。 |
| 293 | `Open-Wine-Components/umu-launcher` | `dockerfile_missing` | 运行结束于关于 final response 是否应包含 `Action:` 的协议循环，没有 accepted verification bundle，也没有 Dockerfile。 | 改进 final response parser；确保 collection 被真实观测成功后再重跑。 |
| 296 | `opendatalab/MinerU` | `test_execution_failed` | Docker build 成功，但 eval pytest collection 因 `ModuleNotFoundError: No module named 'ppocr'` 失败；207 个 tests 被收集，仍有 9 个 collection errors。 | 在 Dockerfile recipe 中安装 MinerU 期望的 PaddleOCR/`ppocr` 依赖路径，然后重新合成/构建。 |
| 298 | `opengeos/HyperCoast` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref c1604c`。 | 更新或镜像 source snapshot。 |
| 303 | `OpenSPG/KAG` | `dockerfile_missing` | test attempts 使用了 shell chaining/exit-code echo，被分类为 `no_reliable_test_execution_signal`；final bundle 未被接受。 | setup 后执行普通、未 mask 的 `cd /app && pytest --collect-only -q --disable-warnings`；移除 `; echo` 这类 probe。 |
| 305 | `outspeed-ai/outspeed` | `dockerfile_missing` | 数据集 URL 指向的 GitHub 仓库不可公开访问，返回 `Repository not found`。 | 更新 dataset URL/source mirror，或标记为 unavailable repository。 |
| 306 | `OwlAIProject/Owl` | `docker_build_failed` | Docker build 在 `poetry lock` 阶段失败。项目 Python 范围是 `>=3.11,<4.0`，但依赖 `whisperx` 要求 `<3.14`，Poetry 对 Python `>=3.14,<4.0` 区间无法求解。 | 在 `poetry lock` 前把项目 Python metadata 收紧到 `<3.14`，或如果现有 lock 有效则避免重新生成 lock。 |
| 312 | `plinder-org/plinder` | `dockerfile_missing` | setup 卡在 OpenStructure/`ost` import hack 上，在 Python site-packages 之间移动 stub 文件，没有达到 pytest collection 成功。 | 安装真实 OpenStructure 依赖，或准备干净、稳定、可复现的 supported stub package。 |
| 314 | `PrefectHQ/ControlFlow` | `dockerfile_missing` | model 输出 XML 风格 `<Action: ...</Action>` block 和 `pwd</Action>` 这类畸形命令，executor 字面执行后无效。 | 剥离 XML/tool-call markup，或为该 planner 增加 model output adapter。 |
| 316 | `PrimeIntellect-ai/prime` | `dockerfile_missing` | clone 阶段失败。目标 ref `a974cf` 找不到，其中一次还出现 RPC partial-transfer/EOF。 | 更新或镜像 source snapshot，并可增加更强的 clone retry/cache。 |
| 323 | `RapidAI/RapidDoc` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref 5e5fef`。 | 更新或镜像 source snapshot。 |
| 327 | `Realiserad/fish-ai` | `dockerfile_missing` | pytest 命令没有可靠成功信号；agent 报告的命令未在 final environment 中成功，因此 bundle 被拒绝。 | 先修底层 collection 问题；最终只报告已观测成功的 collection 命令。 |
| 334 | `RobotecAI/rai` | `dockerfile_missing` | setup 卡在 ROS2/`rclpy` package layout 问题上，并在 Poetry virtualenv 内手动移动文件；没有 pytest collection 成功。 | 为选定 Python 版本安装正确的 ROS2 Python 包和环境，不要手动搬迁 `rclpy` 文件。 |
| 338 | `run-llama/llama_extract` | `dockerfile_missing` | 数据集 URL 指向的 GitHub 仓库不可公开访问，返回 `Repository not found`。 | 更新 dataset URL/source mirror，或标记为 unavailable repository。 |
| 342 | `seanchatmangpt/dspygen` | `test_execution_failed` | Docker build 成功，但 eval collection 立即失败：`dspygen.mixin.fsm.fsm_mixin` 导入 `transitions` 时出现 `ModuleNotFoundError: No module named 'transitions'`。 | 在 Dockerfile recipe 中增加/安装缺失的 `transitions` 依赖。 |
| 343 | `serverless-ca/terraform-aws-ca` | `dockerfile_missing` | 部分子目录/收窄的 pytest 命令可以 collect，但带所需 `PYTHONPATH` 的根级 collection 仍失败，且没有 accepted final verification bundle。 | 安装 lambda module 或持久设置 `PYTHONPATH`，让普通 repo-level collection 无需 narrowing 即可成功。 |
| 348 | `ShaShekhar/aaiela` | `docker_build_failed` | 生成的 Dockerfile 语法非法。多行 apt package list 被拆坏，Docker 将 `build-essential \` 当成顶层 instruction。 | 与 `getzep/graphiti` 同类：保证多行 apt install 留在同一个 `RUN` 指令内。 |
| 351 | `showlab/computer_use_ootb` | `dockerfile_missing` | 依赖只安装到一部分，运行后期陷入 final response/protocol loop，在任何 verified collection command 被接受前结束。 | 修复 final-answer parser 后重跑，并验证普通 pytest collection。 |
| 353 | `siliconflow/BizyAir` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref cdb3bb`。 | 更新或镜像 source snapshot。 |
| 362 | `StacklokLabs/promptwright` | `dockerfile_missing` | agent 卡在 `poetry run pytest` final verification bundle 的格式尝试，没有记录 accepted verified test command。 | 改进 final bundle extraction；如果已经有成功 collection，则自动 finalize。 |
| 374 | `thousandbrainsproject/tbp.monty` | `dockerfile_missing` | agent 尝试用 `/usr/local/bin/python3 -m pytest --collect-only -q --disable-warnings` finalize，但 bundle 没有被接受为 previously verified，因此没有生成 Dockerfile。 | 不使用 shell chaining，重新执行该 exact command，确保它被记录为高置信 successful test command。 |
| 385 | `ucbepic/docetl` | `dockerfile_missing` | clone 阶段失败。所有策略都失败于 `fatal: couldn't find remote ref 00a761`。 | 更新或镜像 source snapshot。 |
| 391 | `vintasoftware/django-ai-assistant` | `dockerfile_missing` | agent 只输出 raw JSON 加 `Final Answer`，没有符合 parser 接受条件的 final bundle 结构；也没有 verified collection command。 | 修复 final bundle parser/格式处理，然后重跑普通 Poetry/pytest collection。 |
| 398 | `warmshao/FasterLivePortrait` | `dockerfile_missing` | 到第 100 步还在安装 `insightface` 等重型 CV 依赖，没有进入 pytest collection 阶段。 | 增加 step budget 或预装重型 CV 依赖，然后执行普通 collection。 |
| 411 | `yihong1120/Construction-Hazard-Detection` | `dockerfile_missing` | 数据集 URL 指向的 GitHub 仓库不可公开访问，返回 `Repository not found`。 | 更新 dataset URL/source mirror，或标记为 unavailable repository。 |
| 412 | `yinjunbo/IS-Fusion` | `dockerfile_missing` | 最终 import check 失败，因为 `mmdet` 要求 `mmcv>=2.0.0rc4,<2.2.0`，但环境中是 `MMCV==1.7.2`。没有 pytest collection 成功。 | 安装互相兼容的 OpenMMLab 栈版本：`mmcv`、`mmdet`、`mmdet3d`、CUDA/PyTorch 版本必须匹配。 |

## 修复优先级

| 优先级 | 样例 | 原因 |
| --- | --- | --- |
| 高 | `getzep/graphiti`, `ShaShekhar/aaiela` | 同一个确定性的 Dockerfile synthesis bug：多行 apt list 被拆成非法 Dockerfile instruction。一个代码修复可覆盖多条。 |
| 高 | `nlmatics/nlm-ingestor` | 确定性的 eval wrapper bug：字面量 `${APP_HOME}` 在 `cd` 前没有解析。 |
| 高 | `computer-agents/agent-studio`, `NUS-HPC-AI-Lab/VideoSys`, `narwhals-dev/narwhals` | agent 已经拿到可用 collection 证据，失败主要在 finalization/salvage 层。 |
| 中 | `chernyadev/bigym`, `Indoxer/LKAN`, `mbodiai/embodied-agents`, `PrefectHQ/ControlFlow` 等 protocol-loop 样例 | 修复 action extraction 和 final-bundle parsing 可以减少大量 `dockerfile_missing`。 |
| 中 | `opendatalab/MinerU`, `seanchatmangpt/dspygen`, `yinjunbo/IS-Fusion`, `Marker-Inc-Korea/AutoRAG` | 主要是依赖/版本问题，通常可以针对性修复。 |
| 低 | source snapshot 缺失类样例 | 需要修 dataset/mirror，不属于 agent 环境配置能力本身。 |
