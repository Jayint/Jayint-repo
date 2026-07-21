# DepGraph 消融实验

## 实验目的

为了评估 DepGraph 对自动化环境构建效果的实际贡献，我们设计了一个移除 DepGraph 的消融版本，记为 **w/o DepGraph**。该版本保留 ExecuteAgent 的诊断、修复和验证能力，只移除依赖图构建、节点状态维护、依赖调度以及图级增量执行。因此，完整方法与消融方法之间的核心差异是是否使用 DepGraph 表示和管理环境构建过程。

| 方法 | DepGraph | ExecuteAgent |
|---|:---:|:---:|
| Full | ✓ | ✓ |
| w/o DepGraph | ✗ | ✓ |

## 消融方法

在没有 DepGraph 的情况下，系统首先收集仓库中的依赖清单、锁文件、CI 配置、Dockerfile 和测试配置等静态证据，并将其直接提供给 ExecuteAgent。ExecuteAgent 根据这些原始证据生成一个按顺序排列的线性构建计划，系统再将该计划确定性地转换为可执行的 `setup.sh`。线性计划只保存命令顺序和证据来源，不包含依赖节点、依赖边、状态传播或拓扑关系。

构建脚本始终在原始基础镜像创建的全新环境中执行。若执行失败，系统会把失败命令、错误日志、当前构建计划和历史失败记录反馈给 ExecuteAgent。ExecuteAgent 可以申请受限的只读诊断，并对失败位置附近的构建块进行局部修改。每次修改后，运行环境都会被重置，完整构建脚本也会从头执行，不使用检查点或已验证前缀复用。

当搜索阶段的构建和测试均成功后，系统还会创建一个新的原始环境，再次完整执行 `setup.sh` 和固定测试。用于 ESSR 评测时，如果构建已完成且固定测试能够真实执行，但仍存在仓库自身的断言失败，系统也会在全新环境中复现一次该结果；只有复现成功后才将环境交给 RAT，由 RAT 保留实际测试通过率。依赖缺失、导入失败等环境问题仍继续进入修复流程。ExecuteAgent 本身不能执行安装操作，也不能自行宣布成功。

## 公平性控制

完整方法和消融方法使用相同的仓库版本、基础镜像摘要、测试命令、测试环境变量、语言模型、采样参数、总模型调用预算、超时限制和运行资源。消融方法首次生成构建计划所消耗的模型调用同样计入总预算。两个方法均不允许修改项目源码或测试代码，也不允许通过忽略错误、删减测试或隐藏返回码来绕过失败。为落实这一约束，主机在运行前记录源码、测试和构建配置的内容摘要，并在每次执行 `setup.sh` 后、运行固定测试前重新校验；发生新增、删除或改写时，本轮直接判定失败。

为了减少额外变量，正式实验应显式固定同一个仓库 commit、基础镜像摘要和测试命令，而不是在不同实验组中分别自动选择。两个实验组都应关闭跨任务共享的可写包缓存；外层自动 Dockerfile 修复也应关闭，避免缓存状态或额外修复机制为某一组提供补救。

## 评价方式

实验以 **Environment Setup Success Rate（ESSR）** 作为主指标。每个仓库使用 RAT 的 `run_pytest.py` 在最终构建环境中执行完整测试，并根据其生成的原始 `run_pytest_results.json` 计算测试通过率：

\[
\mathrm{ESSR}_i=\frac{\mathrm{passed}_i}{\mathrm{total\_tests}_i-\mathrm{skipped}_i}.
\]

数据集层面的主结果为所有已分配仓库的宏平均；若某仓库未能完成测试执行，则其 ESSR 记为 0。同时报告测试执行覆盖率（coverage）以及仅在已执行仓库上计算的宏平均，两者仅作为诊断指标，用于区分环境构建覆盖不足与已执行测试的通过质量。

系统内部的 `status` 表示构建、修复和终局重放流程的控制结果，不与 ESSR 等价。即使测试未全部通过，只要 RAT 产生了有效的原始测试结果，仍保留其实际测试通过率，而不将其简化为二元的成功或失败。除 ESSR 外，实验还记录模型调用次数、Token 消耗、端到端时间、修复轮数和完整重放次数，用于分析 DepGraph 对构建质量和执行成本的影响。

## 独立运行

下面的命令从 `datasets/rat_python_easy_stratified_50.json` 中选取第一条 small 数据 `ozguralp/gmapsapiscanner`（`offset=40, limit=1`），运行一次完整的 **w/o DepGraph** 流程：

```bash
python3 -m ablation.run_rat_ablation \
  --repos-json datasets/rat_python_easy_stratified_50.json \
  --offset 40 \
  --limit 1 \
  --root-path ablation/rat_runs/easy_small_gatefix \
  --llm MiniMax-M3 \
  --num-turn 50 \
  --timeout 7800 \
  --base-image auto
```

运行产物包括最终 `setup.sh`、RAT 原始 `run_pytest_results.json`、包含 ESSR 的结构化结果、证据快照和执行轨迹，均保存在指定的输出目录中。其中单仓库结果位于 `ablation/rat_runs/easy_small_gatefix/output/ozguralp/gmapsapiscanner/`，汇总 ESSR 位于 `ablation/rat_runs/easy_small_gatefix/ablation_essr.json`。自动选择到 Python Alpine 镜像时，消融入口会将其规范化为同版本的 Python slim 镜像，以满足 Sandbox 的 Bash 执行契约。安全门保留源码、测试和命令完整性约束，同时允许 `pip`、`pipx` 与 `poetry` 的精确只读版本查询。每次独立实验应使用新的输出目录，避免复用已完成的结果。正式配对实验还应使用与 Full 组相同的仓库 commit、基础镜像摘要、模型预算和运行资源。
