# V2.1复现与阶段边界

在专属检测worktree执行。原数据、模型、失败日志均保留；所有输出必须是新目录，命令拒绝覆盖已有运行。

## 环境与数据

标准独立环境安装`requirements-semantic-detection.txt`。本机复用原项目环境时，从当前worktree调用：

```powershell
& ../financial-report-agent/scripts/run_project.ps1 -m app.cli.semantic_detection_v21 --help
```

以下命令中的`python`代表该独立Python环境；在本机可将其替换为上述启动器。源码基准829bcb1，Gate A冻结代码3b9f5e0，Gate B冻结代码700556b。通过GitHub连接发布的提交身份另记录在提交说明，本地代码和远程完整Git内容树核对一致。

数据使用原核验特征清单`data/features/features-b9692149f1c1/manifest.json`与其绑定的cohort事实/标签库，均位于`--source-root`；完整原始重建入口见[既有指南](../FRAUD_REBUILD_GUIDE.md)。旧九组参照权重及Qwen固定缓存留在本机原runs路径，未随Git提交。不能把缺少本地数据/权重的克隆直接称作完整端到端复现。

## Gate A

```powershell
python -m app.cli.semantic_detection_v21 channel-audit --source-root ../financial-report-agent --research-root . --output runs/recheck-v21-a1
python -m app.cli.semantic_detection_v21 source-audit --source-root ../financial-report-agent --audit runs/recheck-v21-a1 --output runs/recheck-v21-source
python -m app.cli.semantic_detection_v21 prepare-a --source-root ../financial-report-agent --output runs/recheck-v21-representations
python -m app.cli.semantic_detection_v21 geometry --source-root ../financial-report-agent --output runs/recheck-v21-geometry
python -m app.cli.semantic_detection_v21 embedding-audit --source-root ../financial-report-agent --bank runs/recheck-v21-representations --output runs/recheck-v21-embedding
python -m app.cli.semantic_detection_v21 matched --source-root ../financial-report-agent --bank runs/recheck-v21-representations --policy config/semantic-detection/v21-a.json --output runs/recheck-v21-matched
python -m app.cli.semantic_detection_v21 audit-matched --source-root ../financial-report-agent --run runs/recheck-v21-matched --output runs/recheck-v21-matched-audit
```

`prepare-a`仅对训练期类别做冻结编码，不上传交易；原272条文本先编码，以复核原32维缓存。保存完整1024维再切32/128/256，记录432条唯一文本及原缓存重放差。行业范围文字修正不影响任何已观察输入文本，证明见本机`runs/semantic-v21-initialization/category-rule-equivalence.json`。

18个匹配实例与10个单seed筛查分别报告；当前没有候选达到补seed的固定条件。2018重复使用，日配对区间仅作开发描述，不能当新外部测试。

## Gate B

必须先有通过独立核验的`docs/semantic-detection-v2/gate-a-results.json`，再运行冻结的38实例主协议：

```powershell
python -m app.cli.semantic_detection_v21 history --source-root ../financial-report-agent --gate-a docs/semantic-detection-v2/gate-a-results.json --policy config/semantic-detection/v21-b.json --output runs/recheck-v21-history
```

程序核验原始用户卡关联、全部24邻居缓存边及256个源SQL样例；模型只取注册K前缀。12轮固定末轮，不因2018更好看而早停。全部优化器检查点、曲线和模型保留，不能直接将中间阶段完成标志当作最终科研验收。

追加控制明确标注为事后挑战，不能混入原38实例的预注册分母；其登记时点和停止规则见[GATE_B_ADDENDUM.md](GATE_B_ADDENDUM.md)。资源限制下依次执行，不与主训练争用大块内存：

```powershell
python -m app.cli.semantic_detection_v21 statistics-tree --source-root ../financial-report-agent --run runs/recheck-v21-history --output runs/recheck-v21-tree14
python -m app.cli.semantic_detection_v21 broad-statistics --source-root ../financial-report-agent --run runs/recheck-v21-history --output runs/recheck-v21-broad
```

## 只读补充诊断与测试

```powershell
python -m app.cli.semantic_detection_v21 capacity --source-root ../financial-report-agent --output runs/recheck-v21-capacity
python -m app.cli.semantic_detection_v21 clusters --source-root ../financial-report-agent --output runs/recheck-v21-clusters
python -m app.cli.semantic_detection_v21 raw-sample --source-root ../financial-report-agent --output runs/recheck-v21-raw
python -m pytest -q -p no:cacheprovider tests/semantic_detection --basetemp=.test-review-v21-new
```

原CSV抽查仅解析预先选定的2016–2018物理行，其余行按字节跳过；不解析或评分2019。模型、统计和样例标签只用于宿主核验，未进入文本模板或NN输入。Oracle上限仅是诊断，不是部署模型、训练目标或替代主指标。

共享MCP回归在本机需要先对原`.venv-service-addons`执行`site.addsitedir`，将展开的sys.path传给子进程；Windows异步测试必要时按已有工具权限流程运行，并使用工作区内较短的独立`.test-review-*`目录，避免264字符临时路径问题。不修改共享启动脚本或环境。
# 后续补充来源与完整历史核验

以下继续在研究worktree执行，输出目录必须是新目录。主历史训练仍使用冻结的700556b源文件，不修改其实现。原28个A实例不需要为这些只读诊断重新训练。

```powershell
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 capacity --source-root ..\financial-report-agent --output runs/semantic-v21-capacity-01
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 clusters --source-root ..\financial-report-agent --output runs/semantic-v21-clusters-01
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 raw-sample --source-root ..\financial-report-agent --output runs/semantic-v21-raw-sample-02
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 raw-label-census --source-root ..\financial-report-agent --output runs/semantic-v21-raw-label-census-01
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 composition --source-root ..\financial-report-agent --audit runs/semantic-v21-a1-01 --output runs/semantic-v21-composition-01
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 report-supplementary --source-root ..\financial-report-agent --output docs/semantic-detection-v2
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 progress-b --source-root ..\financial-report-agent --run runs/semantic-v21-b-01 --output docs/semantic-detection-v2
```

下列重型步骤在主38个训练实例全部结束后**顺序**执行。不得在原失败或不完整目录上覆盖重跑。

```powershell
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 broad-statistics --source-root ..\financial-report-agent --run runs/semantic-v21-b-01 --output runs/semantic-v21-b-broad-01
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 audit-history --source-root ..\financial-report-agent --run runs/semantic-v21-b-01 --output runs/semantic-v21-b-audit-01
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_v21 audit-broad --source-root ..\financial-report-agent --run runs/semantic-v21-b-01 --output runs/semantic-v21-b-broad-audit-01
```

当前本地完整回归：239项通过，XML在`runs/semantic-v21-initialization/regression-01.xml`；最后新增的独立宽统计重建又通过14项针对性检查。来源标签全量核对、聚类和容量诊断未产生新的欺诈训练实例。GitHub共享CI缺少调查依赖的既有失败仍须单独说明，不以本地结果冒充线上CI通过。

