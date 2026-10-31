# 从官方源归档重建欺诈主线

本指南解决新检出中不存在作者本机数据／权重目录的问题。它重跑已有IBM合成源开发流程，不提供真实银行案例，不改变实验选择规则，也不会发送模型API请求。重新训练是已观察数据上的复现，不能成为新的留出成绩。

## 环境与来源

在仓库根目录使用Python 3.12，检出包含本指南的`codex/research-upgrades`分支。下面是PowerShell命令；逐步执行，前一步退出码非0时停止，保留失败目录。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dl.txt -r requirements-investigation.txt
```

先阅读[数据卡](DATA_CARD.md)：官方固定提交、合成来源、完整归档大小与SHA-256、使用和再分发边界均有记录。归档约279MB；展开后的处理库、特征和模型还需要额外磁盘空间。全量盘点、构建和训练是长任务，不能承诺一键几秒完成。旧机器的耗时不是新机器的性能保证。

首次下载使用已有的固定源缓存入口；已存在的完整归档会核对大小和哈希，不重新下载。网络端如果不能提供该固定文件或合法访问，停止并保留错误，不换不明镜像：

```powershell
.\.venv\Scripts\python -m app.cli.cache_archive
```

## 显式传递新产物

以下每个输出目录都必须不存在。重新执行时使用新的总目录名；不要覆盖首次失败或把作者历史目录重命名为本次产物。

```powershell
$rebuildRoot = 'runs/my-fraud-rebuild-v1'

# 全量源盘点；汇总会看到各时期标签数量，不是模型效果评测
.\.venv\Scripts\python -m app.cli.profile_data --output "$rebuildRoot/profile"

# 仅按固定身份选择规则选群，核对完整身份目录；不按正例数选用户
.\.venv\Scripts\python -m app.cli.build_cohort --inventory "$rebuildRoot/profile/profile.json" --output "$rebuildRoot/cohort"

# 特征构建不连接标签库；历史严格早于当前交易时刻
.\.venv\Scripts\python -m app.cli.build_features --cohort-report "$rebuildRoot/cohort/build-report.json" --output "$rebuildRoot/features"

# 六配方开发搜索与三seed确认；只加载2016–2017训练及2018开发记录
.\.venv\Scripts\python -m app.cli.train_neural_v2 --features-manifest "$rebuildRoot/features/manifest.json" --policy config/neural-experiment-v2.json --output "$rebuildRoot/neural"

# 用本次模型重算2018分数，导出同一源告警；不读取最终标签
.\.venv\Scripts\python -m app.cli.release_demo --neural-run "$rebuildRoot/neural" --features-manifest "$rebuildRoot/features/manifest.json" --cohort-report "$rebuildRoot/cohort/build-report.json" --output "$rebuildRoot/release"

# 将上一步告警接入欺诈Skill、真实MCP与持久Harness
.\.venv\Scripts\python -m app.cli.fraud_bridge --release "$rebuildRoot/release" --output "$rebuildRoot/fraud"

.\.venv\Scripts\python -m app.cli.workbench --directory "$rebuildRoot/fraud/service" --catalog "$rebuildRoot/fraud/catalog.json"
```

打开`http://127.0.0.1:8765/investigations/page`，使用本次`fraud/service/auth.token`的本地令牌。演示后续步骤见[主线指南](FRAUD_WORKBENCH_QUICKSTART.md)。这里的新调查使用确定性客户端，不能展示为新LLM推理实验。

原开发协议中有“以后用截至2018数据重训”的历史设想，本命令实际仍只用2016–2017拟合，2018选配方和轮次；原最终实验也没有执行该设想。保留协议原文用于追溯，不据此声称已经用2018重训。此重建路径不会执行2019效果评测，也不授予任何新付费调用。

## 产物与失败处理

`profile/profile.json`记录完整源身份目录；`cohort/frozen-plan.json`记录本次实际盘点文件与固定身份选择；`features/manifest.json`绑定源事实库；`neural/data.json`绑定本次特征清单；`release/manifest.json`绑定模型与告警；`fraud/summary.json`记录真实MCP接线。重新生成的时间戳、目录和清单哈希可以不同，不能手改哈希使旧权重冒充新训练产物。

源文件不符、身份目录变化、事实／特征／标签库哈希不符、记录数不符、时间分区越界均应停止。发生失败时先阅读原日志，修改后用新的输出目录；不要删除失败证据后重复运行直到只剩成功记录。

## 本轮实际验证范围

这轮修复前，训练函数不接受重建特征路径，6项检查均在入口失败；修复后相关61项检查通过，包含路径存在空格、来源变化、未来记录伪装为训练数据及最终标签隔离。中间一项测试因把JSON破坏成不可解析内容，未到达其想测试的哈希门槛而失败；测试改为保留JSON格式的字节变化，原失败回执保留。

另外从现有完整源事实库实际重建1687169行特征，随后通过显式新路径加载训练／开发数据；结果见[本轮证据](evidence/fraud-rebuild-v1/manifest.json)。这不是一台全新机器从下载到全部训练的再次验收，依赖安装、全量盘点和神经搜索没有在本轮重跑。不能用路径修复或测试数声称业务价值目标已经完成。
