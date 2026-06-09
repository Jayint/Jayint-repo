# 失败样例归因分析

## 主因分类

| 主因类别 | 数量 | 代表样例 | 说明 |
| --- | ---: | --- | --- |
| A. Verification Bundle / 协议 / action 解析失败 | 21 | `NUS-HPC-AI-Lab/VideoSys`, `narwhals-dev/narwhals`, `computer-agents/agent-studio`, `PrefectHQ/ControlFlow` | agent 已经有部分测试信号，或至少进入 finalization 阶段，但没有形成被 adapter 接受的结构化成功证据。 |
| B. 依赖、版本、硬件、外部资源或项目环境未解 | 14 | `opendatalab/MinerU`, `Marker-Inc-Korea/AutoRAG`, `yinjunbo/IS-Fusion`, `mobiusml/gemlite` | 测试 collection 被真实环境问题阻断，包括缺包、版本不兼容、GPU/模型/GUI/ROS/MPI 等需求。 |
| C. 源码快照不可用 | 13 | `fedirz/faster-whisper-server`, `NexaAI/nexa-sdk`, `run-llama/llama_extract` | 数据集中的 repo/ref 当前无法从公开远端 checkout，属于数据集/镜像问题。 |
| D. 基础设施/传输问题 | 1 | `NVlabs/Sana` | Docker SDK 复制仓库到容器阶段失败，不是项目依赖本身。 |

## A. Verification Bundle / 协议 / action 解析失败

这类共有 21 个，是当前失败的最大来源。它们的共同点是：失败不一定来自“环境完全没配好”，而是来自 agent 探索结果没有稳定转化为可评测证据。

| 样例 | 状态 | 主因 |
| --- | --- | --- |
| `Indoxer/LKAN` | `dockerfile_missing` | 模型输出 XML/tool-call 标记混入 shell action，例如 `</Action>`，导致命令解析和执行异常，后续陷入 final bundle/protocol 循环。 |
| `Infini-AI-Lab/Sequoia` | `dockerfile_missing` | 没有 accepted verification bundle；运行中安装了重依赖，但最终卡在 `Verification Bundle` 与 `Action:` 协议冲突。 |
| `NUS-HPC-AI-Lab/VideoSys` | `dockerfile_missing` | 最后已有 `pytest --collect-only -q --disable-warnings 2>&1` 的高置信 verified command，但第 100 步到达 step limit，未完成 final bundle。 |
| `Nike-Inc/koheesio` | `dockerfile_missing` | pytest 尝试被判为 `no_reliable_test_execution_signal`，final answer 又被解析成混合 action，未产出 accepted bundle。 |
| `Open-Wine-Components/umu-launcher` | `dockerfile_missing` | 运行结束于 final response 是否应包含 `Action:` 的协议循环，没有 accepted bundle。 |
| `OpenSPG/KAG` | `dockerfile_missing` | 使用 shell chaining/exit-code echo 作为测试探针，被判为不可靠测试信号；final bundle 未被接受。 |
| `PrefectHQ/ControlFlow` | `dockerfile_missing` | 模型输出 `<Action: ...</Action>`、`pwd</Action>` 这类畸形命令，executor 字面执行后无效。 |
| `Realiserad/fish-ai` | `dockerfile_missing` | agent 报告的 pytest 命令未在最终环境中被观测为可靠成功，因此 bundle 被拒绝。 |
| `StacklokLabs/promptwright` | `dockerfile_missing` | 卡在 `poetry run pytest` final verification bundle 格式尝试，没有 accepted verified test command。 |
| `alvin-r/databonsai` | `dockerfile_missing` | 后期进入 final-answer/protocol 循环，raw output 被当成 shell 命令执行；早期还存在缺 `pandas` 等测试依赖。 |
| `apple/ml-cross-entropy` | `dockerfile_missing` | agent 声称成功，但 bundle 中 runtime prep 命令没有被最终环境观测成功；pytest 尝试也缺可靠成功信号。 |
| `chernyadev/bigym` | `dockerfile_missing` | 模型反复输出 Markdown/tool-call 形态命令，例如带 `**` 和反引号的命令，几乎没有有效 setup action。 |
| `computer-agents/agent-studio` | `dockerfile_missing` | `xvfb-run -a pytest --collect-only -q --disable-warnings` 已收集到 48 个 tests，但 LLM 在输出 accepted final bundle 前超时。 |
| `mbodiai/embodied-agents` | `dockerfile_missing` | 大量 action 因 Markdown 前缀而被 shell 字面执行失败，没有真正完成 setup。 |
| `meta-llama/llama-stack-apps` | `dockerfile_missing` | 运行后期陷入 final-output protocol 循环，没有 accepted bundle。 |
| `muditbhargava66/PyxLSTM` | `dockerfile_missing` | agent 通过 shell `echo` 输出 verification bundle，而不是作为最终回答提交，validator 拒绝。 |
| `narwhals-dev/narwhals` | `dockerfile_missing` | 存在高置信 compound test command：`pip show polars && pytest ...`，但 max steps 前未输出 accepted final bundle。 |
| `serverless-ca/terraform-aws-ca` | `dockerfile_missing` | 部分子目录/narrow pytest collection 成功，但根级最终 collection 未被 accepted bundle 固化。 |
| `showlab/computer_use_ootb` | `dockerfile_missing` | 依赖只推进到一部分，后期进入 final response/protocol loop，未形成 accepted verified command。 |
| `thousandbrainsproject/tbp.monty` | `dockerfile_missing` | 尝试 finalize 的 pytest 命令没有被判定为 previously verified，bundle 被拒绝。 |
| `vintasoftware/django-ai-assistant` | `dockerfile_missing` | 输出 raw JSON 加 `Final Answer`，但不满足 parser 接受的 final bundle 结构。 |

这类的直接修复方向：

- action 执行前清洗 Markdown、XML/tool-call 包装和多余标签。
- 在 step limit 或 transient LLM error 时，如果已有高置信 `verified_test_commands`，host 侧自动 finalize。
- 允许 adapter 接受 agent 内部已经严格校验过的 auto-finalized verification source。
- 将 compound test command 规范化，尽量把最终测试命令收敛为纯 pytest collection 命令。
- final bundle parser 应该容忍常见 JSON fence/raw JSON 形式，但仍必须要求命令来自成功执行记录。

## B. 依赖、版本、硬件、外部资源或项目环境未解

这类共有 14 个。它们不是单纯格式问题，而是测试 collection 过程中仍有真实环境障碍。也就是说，即使 Verification Bundle 解析器和 finalization 逻辑完全正确，这些样例也不能直接成功，因为当前 sandbox 或 replay 镜像里确实缺少项目测试所需的依赖、版本组合、系统库、硬件能力或外部资源。

这类问题的关键特征是：失败点通常出现在 `pytest --collect-only` 的导入阶段，而不是测试断言阶段。Repo2Run 的目标是让测试可以被收集，所以 collection 阶段的 import error、plugin error、模型加载错误、CUDA 初始化错误、系统库链接错误都会被视为环境配置失败。它和 A 类 Verification Bundle 问题的区别在于，A 类主要是“证据已经出现但没有被结构化接受”，B 类则是“证据本身还没有出现，因为项目环境还没有真正满足”。

B 类可以进一步拆成 5 个子问题：

| 子问题 | 代表样例 | 典型表现 | 本质 |
| --- | --- | --- | --- |
| Python 包缺失或测试 extras 未安装 | `opendatalab/MinerU`, `seanchatmangpt/dspygen`, `apple/ml-mdm` | `ModuleNotFoundError`，例如缺 `ppocr`、`transitions`、`torch.utils.tensorboard` | 项目运行依赖和测试依赖没有完整进入 Dockerfile recipe。 |
| 包版本/API 不兼容 | `Marker-Inc-Korea/AutoRAG`, `yinjunbo/IS-Fusion`, `COLA-Laboratory/TransOPT` | 已安装包存在，但导入的 symbol/API 不存在，或框架版本矩阵冲突 | 不能只安装最新版，需要复现项目期望的版本组合。 |
| 系统级/本地生态依赖缺失 | `RobotecAI/rai`, `lucasdelimanogueira/PyNorch`, `plinder-org/plinder` | ROS2、MPI/OpenMPI、OpenStructure 等 native/runtime 依赖不完整 | 需要 apt/conda/系统库/环境变量配套，pip 级修复不够。 |
| 硬件或外部资源不可用 | `mobiusml/gemlite`, `microsoft/MInference` | CUDA/Triton 需要 GPU；collection 阶段尝试下载或加载 HuggingFace 模型配置 | 当前评测环境无法满足 GPU 或外部模型资源假设。 |
| 重型依赖链未在 step budget 内收敛 | `warmshao/FasterLivePortrait`, `filipstrand/mflux`, `kujirahand/tkeasygui-python` | 长时间安装 CV/GUI/ML 依赖，或反复出现 collection failure signal | agent 需要更多预算、预构建基础镜像或生态级 repair 策略。 |

这类问题不能简单靠“发现缺什么就 `pip install` 什么”解决，原因有三点。

第一，很多项目的依赖是版本矩阵，而不是单个包。例如 OpenMMLab 项目要求 `mmcv`、`mmdet`、`mmdet3d`、PyTorch、CUDA 版本互相匹配；随手安装一个最新版会制造新的 ABI 或 API 冲突。`yinjunbo/IS-Fusion` 就是这一类。

第二，有些依赖不在 Python 包管理器里。ROS2、MPI/OpenMPI、OpenStructure、Tk/Xvfb、CUDA/Triton 需要系统包、动态库、环境变量、daemon 或专门镜像支持。`RobotecAI/rai`、`lucasdelimanogueira/PyNorch`、`plinder-org/plinder` 属于这类。

第三，有些 collection 行为会触发外部资源或硬件初始化。`microsoft/MInference` 在 collection 阶段尝试加载 HuggingFace 模型配置；`mobiusml/gemlite` 的测试在 collection 阶段创建 CUDA tensor。对这类项目，环境配置器需要能识别“需要 GPU/外部资源”，而不是无限尝试 pip repair。

| 样例 | 状态 | 主因 |
| --- | --- | --- |
| `COLA-Laboratory/TransOPT` | `dockerfile_missing` | 存在真实 import/API 问题，例如 `cannot import name 'DataManager' from transopt.datamanager`；同时 agent 还报告了未被观测成功的 pytest 命令。 |
| `Marker-Inc-Korea/AutoRAG` | `dockerfile_missing` | 安装的 `openai` 包缺少 `openai.types.chat.ParsedChatCompletion`，属于依赖版本/API 不兼容。 |
| `RobotecAI/rai` | `dockerfile_missing` | 卡在 ROS2/`rclpy` package layout 问题，并尝试在 Poetry virtualenv 内手动移动文件。 |
| `apple/ml-mdm` | `dockerfile_missing` | collection 仍缺 `/app/tests/data/bert.vocab`，并曾缺 `torch.utils.tensorboard`；安装过程中还有连接中断。 |
| `filipstrand/mflux` | `dockerfile_missing` | pytest collection 尝试均是 `test_failure_signal`，后期又进入 final bundle 尝试和连接错误。 |
| `kujirahand/tkeasygui-python` | `dockerfile_missing` | GUI/Tk 导入引发多个 collection error；后续 headless 尝试使用 `|| echo` 等不可靠命令。 |
| `lucasdelimanogueira/PyNorch` | `dockerfile_missing` | MPI/OpenMPI C++ symbol 问题未解决，stub 和 `ldconfig` 尝试没有带来 collection 成功。 |
| `microsoft/MInference` | `dockerfile_missing` | collection 阶段尝试加载 HuggingFace 模型配置 `gradientai/Llama-3-8B-Instruct-262k`，外部资源不可用。 |
| `mobiusml/gemlite` | `dockerfile_missing` | CUDA/Triton 测试在 collection 阶段创建 CUDA tensor，当前环境没有 NVIDIA GPU。 |
| `opendatalab/MinerU` | `test_execution_failed` | Docker build 成功，但 eval collection 失败于 `ModuleNotFoundError: No module named 'ppocr'`。 |
| `plinder-org/plinder` | `dockerfile_missing` | OpenStructure/`ost` import 依赖未稳定解决，临时 stub/hack 不足以支撑 collection。 |
| `seanchatmangpt/dspygen` | `docker_build_failed` | 当前结果进入 build/replay 阶段失败；旧分析显示 eval collection 还会缺 `transitions`，说明 recipe 中依赖仍不完整。 |
| `warmshao/FasterLivePortrait` | `dockerfile_missing` | 到第 100 步仍在安装重型 CV 依赖，没有进入稳定 pytest collection。 |
| `yinjunbo/IS-Fusion` | `dockerfile_missing` | OpenMMLab 版本矩阵不兼容：`mmdet` 要求 `mmcv>=2.0.0rc4,<2.2.0`，环境中是 `MMCV==1.7.2`。 |

这类的直接修复方向：

- 用项目锁文件、CI、extras/test requirements 作为优先证据，不要用宽泛最新版安装。
- 对 OpenMMLab、ROS2、MPI、CUDA、PaddleOCR 等生态建立专门规则或基础镜像。
- 区分“缺 Python 包”“版本 API 不兼容”“硬件/GPU 不满足”“外部模型资源不可用”。
- 对长依赖链项目提高 deterministic repair budget，优先处理明确的 `ModuleNotFoundError` 和版本矩阵。

## C. 源码快照不可用

这类共有 13 个。它们在 clone/checkout 阶段失败，主要是数据集 commit/ref 已经不可从当前公开 GitHub 远端获取，或者仓库不可访问。

| 样例 | 状态 | 主因 |
| --- | --- | --- |
| `NexaAI/nexa-sdk` | `dockerfile_missing` | 目标 ref `33f6ba` 找不到，且一次 clone 出现 remote hangup。 |
| `PrimeIntellect-ai/prime` | `dockerfile_missing` | 目标 ref `a974cf` 找不到，并出现过 RPC partial-transfer/EOF。 |
| `RapidAI/RapidDoc` | `dockerfile_missing` | 目标 ref `5e5fef` 找不到。 |
| `fedirz/faster-whisper-server` | `dockerfile_missing` | 目标 ref `cbb6c9` 找不到。 |
| `hpcaitech/Open-Sora` | `dockerfile_missing` | 目标 ref `38de63` 找不到。 |
| `jialuechen/deepfolio` | `dockerfile_missing` | 目标 ref `15d247` 找不到。 |
| `landing-ai/vision-agent` | `dockerfile_missing` | 目标 ref `63eab8` 找不到。 |
| `opengeos/HyperCoast` | `dockerfile_missing` | 目标 ref `c1604c` 找不到。 |
| `outspeed-ai/outspeed` | `dockerfile_missing` | GitHub 返回 `Repository not found`。 |
| `run-llama/llama_extract` | `dockerfile_missing` | GitHub 返回 `Repository not found`。 |
| `siliconflow/BizyAir` | `dockerfile_missing` | 目标 ref `cdb3bb` 找不到。 |
| `ucbepic/docetl` | `dockerfile_missing` | 目标 ref `00a761` 找不到。 |
| `yihong1120/Construction-Hazard-Detection` | `dockerfile_missing` | GitHub 返回 `Repository not found`。 |

这类不应算作 agent 环境配置能力失败。更合理的处理方式是：

- 运行前做 repo/ref 可达性预检。
- 为 benchmark 固定 source mirror 或归档 tarball。
- 将不可访问样例单独标记为 `source_unavailable`，从 DGSR/EBSR 的 agent 能力分析中剥离。

## D. 基础设施/传输问题

这类当前只有 1 个。

| 样例 | 状态 | 主因 |
| --- | --- | --- |
| `NVlabs/Sana` | `dockerfile_missing` | clone 成功后，sandbox 初始化时 Docker SDK `put_archive` 复制仓库进容器失败，抛出 `requests.exceptions.ConnectionError: OSError(22, 'Invalid argument')`。 |

这是 Docker 传输/宿主环境问题，不是项目依赖问题。可考虑：

- 对 `put_archive` 加 retry。
- 大仓库分块复制或改用 bind mount。
- 将该类失败单独归为 `infrastructure_error`。

## 根本原因分析

当前 49 个失败不是同一种问题，而是四层问题叠加。

第一层是数据集可复现性问题。13 个样例在 clone/checkout 阶段就失败，说明 benchmark 使用的 repo/ref 没有被完全归档。只依赖公开 GitHub 的短 SHA 或当前仓库状态，会让实验结果随时间漂移。

第二层是 agent 证据提交链路问题。21 个样例的主因集中在 action 格式、final answer 协议、Verification Bundle 解析、verified command 固化和 step limit 收尾。这里的根本矛盾是：系统需要严格防止 LLM 幻觉成功，但当前实现把“严格可验证”过度绑定到 LLM 最终输出格式。只要模型把 JSON 包在错误位置、输出 Markdown/XML 标签、用 shell echo 打印 bundle、或者在 step limit 前没有完成 final answer，已有的 sandbox 证据就可能无法转化为可评测 Dockerfile。

第三层是环境生态复杂性问题。14 个样例仍有真实依赖、版本、硬件或外部资源障碍。普通 `pip install -e .` 或宽泛缺包修复不足以处理 OpenMMLab、ROS2、MPI、CUDA/Triton、PaddleOCR、GUI/Tk、HuggingFace 模型资源这类项目。这里的根因不是 bundle，而是项目环境本身需要更强的生态规则、基础镜像和版本矩阵约束。

第四层是 replay/基础设施问题。少量样例已经越过 sandbox 探索，但在 Docker build、eval wrapper 或 Docker SDK 传输层失败。它们说明 sandbox 成功、Dockerfile 合成成功、fresh build 成功、eval collection 成功是不同阶段，不能用一个 `dockerfile_missing` 或 `environment_build_success=false` 简化解释。

因此，最核心的根因可以概括为：

> 当前系统把“环境是否真的被配置好”压缩成“是否产出被严格 parser 接受的 Verification Bundle 和可 replay 的 Dockerfile”。这个设计保证了可复现性，但验收入口过窄、对 LLM 输出格式过敏，并且对源码不可用、硬件/外部资源、复杂依赖生态缺少独立错误类型，导致大量不同性质的问题都表现为 `dockerfile_missing`。

## 修复优先级

| 优先级 | 方向 | 预期影响 |
| --- | --- | --- |
| P0 | 将 clone/ref 不可用样例预检并标记为 `source_unavailable` | 直接从 agent 能力失败中剥离 13 个不可比样例。 |
| P0 | step limit/transient error 时基于已有 `verified_test_commands` 自动 finalize | 可直接改善 `NUS-HPC-AI-Lab/VideoSys`, `narwhals-dev/narwhals`, `computer-agents/agent-studio` 等 finalization 型失败。 |
| P1 | action parser 清洗 Markdown/XML/tool-call 包装 | 可改善 `chernyadev/bigym`, `mbodiai/embodied-agents`, `PrefectHQ/ControlFlow`, `Indoxer/LKAN` 等。 |
| P1 | 将 Verification Bundle 解析从“LLM 格式优先”改为“结构化证据优先，LLM 格式补充” | 减少 protocol loop 和 final bundle 格式误伤。 |
| P1 | 为复杂生态增加规则化 repair：OpenMMLab、PaddleOCR、ROS2、MPI、CUDA、GUI/Tk | 改善真实依赖类失败。 |
| P2 | 细化失败状态：`source_unavailable`, `verification_not_finalized`, `dependency_unresolved`, `hardware_required`, `infrastructure_error` | 让后续统计和论文分析不再把所有失败压成 `dockerfile_missing`。 |
