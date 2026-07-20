# Repo2Run Benchmark 运行说明

本文档说明如何在本仓库中使用 `Repo2Run` 论文的数据集，并按论文口径运行 benchmark。

这里的流程是**独立于 `Multi-Docker-Eval` 的**，只依赖本仓库当前实现的：

- `build_repo2run_dataset.py`
- `run_repo2run_benchmark.py`
- `agent.py`

对应论文文件是 [repo2run.pdf](/Users/panjianying/Desktop/Jayint-repo/docs/repo2run.pdf)。

## 1. 背景

Repo2Run 论文在主实验中评估的是 420 个 Python GitHub 仓库。论文的主指标有两个：

- `DGSR`：Dockerfile Generation Success Rate
- `EBSR`：Environment Building Success Rate

论文对这两个指标的定义是：

- `DGSR`：生成的 Dockerfile 能够成功 `docker build`
- `EBSR`：生成的 Dockerfile 不仅能 build 成功，而且在容器中能够执行 `pytest`

这里的 `EBSR` **不要求测试全部通过**。只要测试真正运行起来即可；测试失败和测试根本无法执行是两个不同概念。

## 2. 本仓库中的实现

本仓库将论文 Appendix / Table 15 的 benchmark 落成了一个本地 JSON 数据集：

- [datasets/repo2run_table15.json](/Users/panjianying/Desktop/Jayint-repo/datasets/repo2run_table15.json)

这个文件来自论文 Table 15，包含：

- `instance_id`
- `full_name`
- `repo_url`
- `sha`
- `base_commit`
- `paper_build_success`

其中：

- `paper_build_success = true` 对应论文表 15 的 `Yes`
- `paper_build_success = false` 对应论文表 15 的 `No`

当前解析结果是：

- 总实例数：420
- 论文成功数：361
- 论文失败数：59

## 3. 相关脚本

### 3.1 数据集生成脚本

- [build_repo2run_dataset.py](/Users/panjianying/Desktop/Jayint-repo/build_repo2run_dataset.py)

作用：

- 从 [docs/repo2run.pdf](/Users/panjianying/Desktop/Jayint-repo/docs/repo2run.pdf) 的 Table 15 提取仓库、commit 和成功标记
- 生成 [datasets/repo2run_table15.json](/Users/panjianying/Desktop/Jayint-repo/datasets/repo2run_table15.json)

### 3.2 Benchmark 运行脚本

- [run_repo2run_benchmark.py](/Users/panjianying/Desktop/Jayint-repo/run_repo2run_benchmark.py)

作用：

1. 对每个 Repo2Run 实例调用 `agent.py`
2. 用 agent 输出的 Dockerfile 构造一个临时 eval Dockerfile
3. 真实执行 `docker build` 计算 `DGSR`
4. 真实执行容器内测试命令计算 `EBSR`
5. 汇总与论文表 15 的一致性结果

## 4. 环境要求

运行前需要满足：

- 本机 Docker daemon 正常运行
- 已配置 LLM 所需的 API key，例如 `.env` 中的 `MINIMAX_API_KEY` 或 `OPENAI_API_KEY`
- Python 环境已安装本仓库依赖

推荐准备方式：

```bash
cd /Users/panjianying/Desktop/Jayint-repo
source .venv/bin/activate
uv pip install -r requirements.txt
```

`run_repo2run_benchmark.py` 当前默认会：

- 使用 `--max-steps 100`
- 开启 observation compression

## 5. 生成数据集

如果你想从论文 PDF 重新生成一次数据集，可以执行：

```bash
cd /Users/panjianying/Desktop/Jayint-repo
python3 build_repo2run_dataset.py
```

默认输出：

- [datasets/repo2run_table15.json](/Users/panjianying/Desktop/Jayint-repo/datasets/repo2run_table15.json)

你也可以指定输入和输出：

```bash
python3 build_repo2run_dataset.py \
  --pdf docs/repo2run.pdf \
  --output datasets/repo2run_table15.json
```

## 6. 运行 Benchmark

### 6.1 先跑一个实例

```bash
cd /Users/panjianying/Desktop/Jayint-repo
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --limit 1
```

### 6.2 按仓库名筛选

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --instance-regex "FastAnime|logbook"
```

### 6.3 只跑论文中标记成功的实例

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --only-paper-success
```

### 6.4 调整超时

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --docker-build-timeout 3600 \
  --test-timeout 3600
```

### 6.5 保留镜像以便排查

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --limit 1 \
  --keep-docker-artifacts
```

### 6.6 关闭 observation compression

如果你想显式关闭 compression，可以传：

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --disable-observation-compression
```

## 7. 当前评测流程

对单个实例，脚本执行如下步骤：

1. 调用 `agent.py`
2. `agent.py` clone 指定仓库并 checkout 到表 15 里的 `sha`
3. 若 agent 成功，会在对应 `workplace` 下产出 `Dockerfile` 和 `agent_run_summary.json`
4. benchmark runner 读取该 Dockerfile，生成一个临时 eval Dockerfile
5. eval Dockerfile 会把当前 `workplace` 里的仓库内容 `COPY` 进镜像
6. 真实执行 `docker build`
7. build 成功后，在容器里执行：
   - `runtime_preparation_commands`
   - `test_commands`
8. 根据测试输出判定 tests 是否真正执行

## 8. DGSR 与 EBSR 的判定方式

### 8.1 DGSR

`dockerfile_generation_success = true` 的条件是：

- agent 的 Dockerfile 文件存在
- 临时 eval Dockerfile 能够真实 `docker build` 成功

这对应论文里的 `DGSR`。

### 8.2 EBSR

`environment_build_success = true` 的条件是：

- 已满足 `DGSR`
- 容器中的测试命令真正执行

当前 runner 将以下情况视为“测试真正执行”：

- 有明显的测试执行信号，例如 `collected N items`
- 或有明确的测试结果统计，例如 `9 passed, 1 failed`
- 即使测试有失败，只要说明 tests 已经真实运行，也算 EBSR 成功

以下情况不算 EBSR 成功：

- `pytest --help` 这类帮助输出
- `collected 0 items`
- `ERROR collecting`
- 纯参数错误或收集阶段错误
- 命令超时且没有有效测试执行信号

这和论文的定义一致：**只关心 tests 是否能执行，不关心 tests 是否全部通过。**

## 9. 输出目录结构

默认输出到：

- `outputs/repo2run_benchmark/`

主要结构如下：

```text
outputs/repo2run_benchmark/
├── summary.json
├── results/
│   └── <instance>.json
├── workplaces/
│   └── <instance>/
└── eval_artifacts/
    └── <instance>/
        └── Dockerfile.eval
```

说明：

- `results/<instance>.json`：单个实例的完整执行结果
- `workplaces/<instance>/`：agent 的本地工作目录，包含 clone 后仓库、Dockerfile、run summary
- `eval_artifacts/<instance>/Dockerfile.eval`：实际拿去做 `docker build` 的评测 Dockerfile
- `summary.json`：汇总统计

## 10. `summary.json` 中的关键字段

`summary.json` 中最重要的是：

- `metrics.DGSR.success_count`
- `metrics.DGSR.rate`
- `metrics.EBSR.success_count`
- `metrics.EBSR.rate`

此外还会保留和论文表 15 的对比：

- `paper_alignment_counts.matched_success`
- `paper_alignment_counts.matched_failure`
- `paper_alignment_counts.unexpected_success`
- `paper_alignment_counts.unexpected_failure`

它们的含义是：

- `matched_success`：我们这边 `EBSR` 成功，论文表 15 也是 `Yes`
- `matched_failure`：我们这边 `EBSR` 失败，论文表 15 也是 `No`
- `unexpected_success`：我们这边成功，但论文表 15 是 `No`
- `unexpected_failure`：我们这边失败，但论文表 15 是 `Yes`

## 11. 单实例结果文件说明

每个 `results/<instance>.json` 会包含：

- 原始数据集条目
- `agent_run`
- `run_summary`
- `docker_build`
- `test_execution`
- `dockerfile_generation_success`
- `environment_build_success`
- `paper_alignment`
- `execution_status`

其中：

- `docker_build` 是真实 `docker build` 的 stdout/stderr/returncode
- `test_execution` 是真实容器测试执行记录

## 12. 常见使用方式

### 12.1 只做 smoke test

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --limit 3
```

### 12.2 复现实验时跑完整集

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json
```

### 12.3 只复现论文成功样本

```bash
python3 run_repo2run_benchmark.py \
  --dataset datasets/repo2run_table15.json \
  --only-paper-success
```

## 13. 注意事项

### 13.1 这不是 Multi-Docker-Eval

这个 benchmark runner 不依赖 `Multi-Docker-Eval`，也不会生成 `docker_res.json` 或 eval script 给官方评测框架消费。它是本仓库自己的 Repo2Run 论文复现实验入口。

### 13.2 当前实现对论文流程做了一个工程化适配

论文原文描述的思路更接近“生成 Dockerfile 后，用 Dockerfile 复现环境并在容器里运行 `pytest`”。本仓库实现时，为了避免再次依赖网络 clone，采用的是：

- agent 先在 `workplace` 中 checkout 到指定 commit
- eval Dockerfile 再把这个本地工作区 `COPY` 到镜像

这使得评测更稳定，也更适合当前仓库结构。

### 13.3 EBSR 不等于测试全通过

如果你希望额外统计“测试全部通过率”，需要在 `test_execution` 结果上再做一层更严格的分析。这不是 Repo2Run 论文主表的口径。

### 13.4 运行成本较高

完整跑 420 个实例会消耗：

- 大量 Docker build 时间
- 较多磁盘空间
- 模型推理成本

建议先用 `--limit` 或 `--instance-regex` 小规模试跑。

## 14. 已验证的命令

以下命令已经在当前仓库中验证通过：

```bash
python3 build_repo2run_dataset.py
python3 run_repo2run_benchmark.py --help
pytest -q tests/test_repo2run_dataset.py tests/test_repo2run_benchmark.py
```

## 15. 相关文件

- [docs/repo2run.pdf](/Users/panjianying/Desktop/Jayint-repo/docs/repo2run.pdf)
- [datasets/repo2run_table15.json](/Users/panjianying/Desktop/Jayint-repo/datasets/repo2run_table15.json)
- [build_repo2run_dataset.py](/Users/panjianying/Desktop/Jayint-repo/build_repo2run_dataset.py)
- [run_repo2run_benchmark.py](/Users/panjianying/Desktop/Jayint-repo/run_repo2run_benchmark.py)
- [src/repo2run_dataset.py](/Users/panjianying/Desktop/Jayint-repo/src/repo2run_dataset.py)
- [tests/test_repo2run_dataset.py](/Users/panjianying/Desktop/Jayint-repo/tests/test_repo2run_dataset.py)
- [tests/test_repo2run_benchmark.py](/Users/panjianying/Desktop/Jayint-repo/tests/test_repo2run_benchmark.py)
