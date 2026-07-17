# RAT hard subset 前 5 条运行与修改说明

日期：2026-07-16  
数据集：`datasets/rat_python_hard_subset.json` 前 5 条  
代码校验摘要：`df6105cee4ecfe49926c556a139d8fc2421941fe3d6520b212ff6946110518b1`

## 结果

| 序号 | 仓库 | 结果 | test_pass_rate | 说明 |
|---:|---|---|---:|---|
| 1 | `jhao104/proxy_pool` | success | 1.0000 | 248/248 通过 |
| 2 | `microsoft/markitdown` | success | 0.9893 | 369/374 通过 |
| 3 | `nginx-proxy/nginx-proxy` | success | 0.0000 | 评估容器缺 Docker socket，collection 阶段失败 |
| 4 | `NevaMind-AI/memU-server` | success | 1.0000 | 99/99 通过 |
| 5 | `conor-is-my-name/n8n-autoscaling` | success | 0.0000 | 目标仓库 `examples/test_python_packages.py` 存在语法错误，collection 阶段失败 |

使用的结果目录：

- `outputs/rat_v3_first5_20260716_194200_final`
- `outputs/rat_v3_markitdown_single_20260716_214500_final`
- `outputs/rat_v3_first5_offset2_20260716_205600_parallel`

## 修改内容概述

这次没有改 benchmark 目标仓库的代码，也没有通过忽略测试来刷分。主要改的是当前项目对环境依赖、测试 gate 和 replay 的判断：

1. 补齐常见系统工具映射，例如 `curl`、`git`、`Rscript`、`exiftool`。
2. 降低配置误报：识别 `.env`、pytest/tox 配置、Dockerfile `ENV` 和代码默认值。
3. 过滤伪 service 节点，避免把 `git`、`R`、`mamba` 等工具误判成服务。
4. 强化运行时错误分类和 Python 版本兼容 resolver 回退。
5. 支持证据驱动的配置模板复制。
6. 补 pytest runner 依赖根，避免目标仓库没显式声明 pytest 时直接失败。
7. 调整测试 gate：真实 pytest 已执行且是测试断言失败时，允许继续产出可评估结果；但 `ModuleNotFoundError`、conftest import error、语法错误、Docker socket 缺失等环境/collection 问题仍会被判为不可通过。

## 验证

项目测试：

```bash
pytest -q tests --ignore=tests/test_repo2run_dataset.py
```

结果：`1717 passed, 32 skipped, 2 warnings`

## 未解决限制

- `nginx-proxy/nginx-proxy` 的测试依赖真实 Docker daemon/socket，当前评估容器没有挂载 `/var/run/docker.sock`，因此无法收集测试。
- `conor-is-my-name/n8n-autoscaling` 的唯一 pytest 文件存在语法错误，属于目标仓库自身 collection failure；在不修改目标仓库测试、不使用 ignore/deselect 的前提下无法得到非零 pass rate。
