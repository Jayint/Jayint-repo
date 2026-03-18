# Dependency Draft

## 问题定义

某些 benchmark 样本虽然固定了主仓库的 `base_commit`，但没有固定关键依赖的具体版本或 commit。
如果依赖是从 GitHub 直接拉取、且仓库中没有 lockfile，那么今天解析出的依赖状态可能和样本创建时完全不同。

这会导致一种特殊失败：

- 主仓库代码是历史版本
- 关键依赖却是“今天的最新兼容解析结果”
- 两者之间出现 CLI、API、DSL 或行为不兼容

## 当前项目中的典型案例

以 `yippee-fun/phlex` 的样本 `phlex-889` 为例：

- 主仓库 commit 被 benchmark 固定
- `Gemfile` 中的 `quickdraw` 依赖是 GitHub 依赖
- 该依赖没有 `ref` / `tag` / `branch` 以外的精确锚点
- 仓库中没有 `Gemfile.lock`
- 结果当前 `bundle install` 拉下来的 `quickdraw` 与仓库 CI 语义不一致

具体表现为：

- CI 里使用 `bundle exec qt -t 1`
- 当前安装得到的 `qt` 不支持 `-t`
- 退回到 `bundle exec qt` 后，又在测试 DSL 上出现 `Kernel#test` 参数错误

这说明问题不再是普通环境配置失败，而更像：

- 历史主仓库 commit
- 搭配了错误时间线上的依赖版本

## 为什么这类问题不能简单归为 agent 太弱

agent 确实可以更强，例如：

- 更早识别依赖漂移
- 不要在明显不相关的方向上浪费探索步数
- 更快把问题归类为“依赖版本不匹配”

但这类样本的核心难点不只是探索能力，而是输入信息本身不完整：

- benchmark 固定了主仓库 commit
- 却没有固定关键 git 依赖的 commit
- 仓库又没有 lockfile

所以问题本质上是：

- 可复现性缺失
- 历史依赖状态丢失

## 后续建议方向

不要只靠主 agent 临场试命令，应该增加独立的 `Dependency Resolver` / `Dependency Archaeologist` 子系统。

### 第一阶段：识别问题

先做一个“依赖漂移检测器”，把失败分类成：

- `missing_dependency`
- `toolchain_version_mismatch`
- `platform_incompatibility`
- `unlocked_git_dependency_drift`

优先目标不是自动修复，而是尽快判断“这不是普通缺包问题”。

### 第二阶段：候选版本生成

对未锁定的 GitHub 依赖，生成候选版本：

- 主仓库 `base_commit` 时间点之前的依赖仓库 commit
- 依赖仓库 tags / releases
- 与当前 CI 命令形态兼容的版本候选

### 第三阶段：低成本验证

不要一上来跑完整测试集，而是用分层 oracle：

- CLI 参数是否存在
- 最小 require / import 是否成功
- 最小 DSL / API smoke test 是否通过
- 最后才跑正式测试命令

### 第四阶段：自动改写与缓存

如果候选版本通过验证：

- 临时把依赖 pin 到该 commit
- 再进入正常环境配置与测试
- 将成功结果缓存为：
  - `repo@base_commit -> dependency_name -> resolved_ref`

## 在当前项目中的落点建议

建议新增独立模块，而不是把逻辑塞进 planner prompt：

- `src/dependency_resolver.py`
- `src/ruby_dependency_resolver.py`

并在 `DockerAgent` checkout `base_commit` 之后、正式进入 setup/test loop 之前介入。

## 当前结论

“未锁定 git 依赖漂移”是这个项目中值得单独建模的一类问题。
它不是普通环境配置失败，也不应该简单算作纯 agent 能力不足。
后续如果要提升这类样本的解决率，最值得投入的是：

- 问题识别
- 候选版本恢复
- 分层验证
- 结果缓存
