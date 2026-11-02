# 独立检测研究的运行方法

本研究在 `codex/llm-semantic-detector` 独立worktree内。现有Agent工作目录与历史模型保留。全部输出要求新目录，不覆盖旧运行。默认不读取2019记录、不调用付费API、不替换检测器。

## 环境

Python3.12，独立环境安装 `requirements-semantic-detection.txt`。模型推理不依赖GPU。运行可选通用调查演示还需 `requirements-service.txt`；共享MCP/Harness回归另需 `requirements-investigation.txt`。不要向正在运行的Agent环境随意安装新依赖。

本机为了复用已安装依赖，从新worktree调用：

```powershell
& ..\financial-report-agent\scripts\run_project.ps1 -m app.cli.semantic_detection_research --help
```

下列命令以独立环境的 `python` 表示；本机可以替换为上述脚本前缀。`--source-root`是旧研究数据所在仓库，仅读。源数据不随Git再分发。

## 公开来源与编码器

从Gate0所列官方URL保存Visa PDF到 `runs/semantic-detection-sources/visa-mcc.pdf`，先核对版本及SHA。重新提取需要pdfplumber：

```powershell
python -m app.cli.semantic_detection_sources --pdf runs/semantic-detection-sources/visa-mcc.pdf --features ../financial-report-agent/data/features/features-b9692149f1c1/features.sqlite --output config/semantic-detection/mcc-glossary.json
```

公开手册URL内容会更新，不能将新版本覆盖旧词典而声称相同实验。复现既有运行应使用已提交的glossary及对应原始PDF。5912源冲突保留unknown，不自行纠正。

下载固定、已核对许可的公开模型，约1.2GB；不上传交易：

```powershell
python -m app.cli.semantic_detection_download --output runs/semantic-detection-models/qwen3-0.6b
```

本次首次下载截断，模型加载拒绝；已通过HTTP范围补全，并核对官方LFS SHA。首次失败在prepare-v1.log、原目录与下载回执保留。无需为了复现成功重复制造失败。

## 编码与E1

```powershell
python -m app.cli.semantic_detection_research prepare --source-root ../financial-report-agent --encoder runs/semantic-detection-models/qwen3-0.6b --output runs/semantic-prepare-my-run
python -m app.cli.semantic_detection_research e1 --source-root ../financial-report-agent --cache runs/semantic-prepare-my-run/embedding-cache --output runs/semantic-e1-my-run
```

prepare只选择训练记录。274个类别键对应272个唯一文本；缓存保留文本、来源哈希、tokenizer/权重逐文件哈希、池化、维度、时间和完整性键。

本轮训练使用已冻结的v1缓存。后续v2缓存身份仅包含真正模型/tokenizer文件，并将类别键绑定进cache key，避免下载器临时元数据影响等价模型身份。`runs/semantic-cache-attested-v2`核对核心文件后复用完全相同的向量字节，保留原编码时间，另记审计时间；不是再次编码或重新训练，旧缓存及结果未改写。

E1固定5条件×3seed×5轮。每个seed有曲线、预处理、模型权重、全量开发分数、重载差异、参数量、耗时、进程峰值内存和指标。`protocol.json`冻结代码指纹、协议及环境，`frozen-code`保存实际检测模块。旧无渠道残差与强树另外比较；不得把它们当成本轮完全匹配输入控制。

默认正式协议为 `e1-additive-v2.json`：所有条件保留数值和原MCC/渠道分支，再附加普通或语义32维。`e1.json`是设计复核前的replacement先导协议，旧结果留存但正式命令拒绝继续使用。模型加载兼容旧产物，不意味着旧设计获得正式验收。

## E3标签预算研究

```powershell
python -m app.cli.semantic_detection_research e3 --source-root ../financial-report-agent --cache runs/semantic-prepare-my-run/embedding-cache --output runs/semantic-e3-my-run
```

固定5%、10%、20%、100%四预算、五条件、三seed，每个2轮，属于有界短预算研究。所有训练mask先保存后评分；隐藏标签变为-1，不能用于监督损失或类别权重。2018/2019不参与一致性训练。100%预算下无无标签样本，一致性项应为0，是内置负对照。

默认正式协议为 `e3-additive-v2.json`；旧replacement半监督批次已因设计复核中止，部分产物保留，不作为正式结果。

## 独立轻量历史研究

```powershell
python -m app.cli.semantic_detection_history --source-root ../financial-report-agent --cache runs/semantic-prepare-my-run/embedding-cache --output runs/semantic-history-my-run
```

6个固定运行：mean与attention各3seed，全部训练标签、K=6、2轮。训练邻域只含2016–2017；推理可以读取目标之前已经发生的2018记录，但不更新参数。无2015预热节点，不能声称完整历史。模型每次只收到K个已筛选的过去输入，不收到整个未来图再mask。

通过`predict_history(predictor, context_frame, context_metadata, target_positions)`可重放历史模型。缺失邻居在进入模型之前变为零占位，不把某条未来记录当作被mask的占位。重载抽验保留每个样本的完整K邻居闭包。

## 可选候选告警与通用调查演示

```powershell
python -m app.cli.semantic_detection_research export --source-root ../financial-report-agent --run runs/semantic-e1-my-run --variant B3 --output runs/semantic-challenger-my-demo --users 2
python -m app.cli.workbench --directory runs/semantic-challenger-my-demo/service --catalog runs/semantic-challenger-my-demo/catalog.json
```

先检查全部研究材料哈希，再加载三个模型对2018全量重算，核对分数—行序绑定，按原日top10和tie规则排名。核心队列六字段不变，模型元数据单独保存。新release ID绝不冒充 `neural_v2_ensemble`。

通用调查入口使用模拟确认、关键词计划和确定性工具，报告停在待审阅；另做源SQL核对。默认不开live，令牌只在本地service/auth.token读取。

现有特化欺诈MCP合同硬编码旧检测器，因此不能声称已接入该特化分支。修改它必须另做独立集成PR和新旧回归，本研究不绕过验证器或静默扩权。

## 测试

只复核已有产物，不重训、不调用API；核验输出必须为新文件：

```powershell
python -m app.cli.semantic_detection_audit --source-root ../financial-report-agent --run runs/semantic-e1-additive-v2 --output runs/recheck-semantic-e1.json
python -m app.cli.semantic_detection_audit_e3 --source-root ../financial-report-agent --run runs/semantic-e3-additive-v2 --cache runs/semantic-prepare-v2/embedding-cache --output runs/recheck-semantic-e3.json
python -m app.cli.semantic_detection_audit_history --source-root ../financial-report-agent --history runs/semantic-e2-history-v1 --e3 runs/semantic-e3-additive-v2 --output runs/recheck-semantic-history.json
```

这些命令复核2018已观察开发结果，不是一次新的独立效果评测。原始本轮审计回执在 `runs/semantic-detection-sources/`，汇总绑定对应报告SHA。改变K或其他配置必须保存新协议与运行，不把本轮K=6当作已证明最优。

```powershell
python -m pytest -q -p no:cacheprovider tests/semantic_detection
```

本机复用额外依赖目录时，该目录通过PYTHONPATH添加不会自动执行pywin32.pth。MCP回归需要先 `site.addsitedir(额外依赖目录)`，再将展开的sys.path传给子进程；标准新建venv安装通常不需要此桥接。首次收集失败原文保留，不改共享启动脚本。

MCP/Harness/Memory/服务恢复回归命令及JUnit回执保留在runs/semantic-detection-sources。本机Windows沙箱内部分异步测试曾停滞，停止后通过工具授权在隔离临时目录重跑；不把中止尝试计作通过。

## 成本解读

记录实际本机墙钟与整进程峰值内存，后者包括数据及框架，不是单模型独占显存。E1首段与离线回归重叠，E1后段与E3重叠运行（每进程4线程）；这些耗时不是隔离硬件基准，不用于宣称架构更快或服务吞吐。

任何新结果都写入新目录。2019已经观察过，不能用于本研究调参后再包装为新独立测试。
