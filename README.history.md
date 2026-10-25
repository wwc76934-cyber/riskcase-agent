# RiskCase Agent

**2026-10-22：G6-A自然语言计划与确认后取证已完成。** 支持一句话选择调查范围、显式确认、代码取证计算、报告审阅；模型不接触事实或金融执行工具。80题冻结对照中，评测关键词37/40、模型33/40，模型仍未证明优于简单入口；保留错误范围反例，未重跑改分。94份按所选范围生成的报告通过独立源SQL核验，确认均为评测脚本模拟。新增79次真实请求估算0.293636元，累计估算6.031868元。原V1仍待最终验收。

先看 [这轮结果与剩余工作](docs/NATURAL_STAGE_REPORT.md)、[一句话调查的直白运行指南](docs/NATURAL_TASK_GUIDE.md)、[为什么数字对但范围会错](examples/natural-scope-overreach.md)。免费演示：`powershell -File scripts/run_project.ps1 -m app.cli.natural_demo`。下方各段保留历史状态，最新进度以本段与项目总进度页为准。

**2026-10-22：G5-A混合调查与强基线对照已验收。** 新增五类检查组合并接入持久服务；8名新任务用户、32题，评测部分固定流程16/16、固定证据模型14/16、工具型Agent14/16，尚无Agent优势。发现并修复工具尝试超限仍生成报告的问题，保留原失败成绩。全仓521项测试、干净环境67项通过，本轮146请求估算3.031292元。先读 [阶段结论](docs/MIXED_STAGE_REPORT.md)、[运行指南](docs/MIXED_TASK_GUIDE.md) 或 [示例报告](examples/mixed-investigation.md)。原V1尚未结项；以下为各批历史记录。

**最新阶段 G4-A 已验收：本地任务服务与安全恢复。** 已支持鉴权提交/查询/取消、版本绑定的报告审阅，以及跨进程响应回放；487项全仓测试通过，全新服务环境33项通过。真实模型服务验证3请求、估算0.064972元，报告独立SQL复核通过，仍待审阅；原金融候选未批准/部署。先读 [阶段验收](docs/SERVICE_STAGE_REPORT.md) 或 [服务运行指南](docs/SERVICE_GUIDE.md)。原V1尚未全部完成，以下各批数字为历史记录。

**最新阶段 G3-A 已验收：同审核容量规则实验。** 422,088条开发记录、三档预算、四种政策、12份名单与48项月度统计独立SQL复核；全仓454测试通过。每日10笔主口径下，HGB捕获95，规则保留20%捕获90（新增11、挤出16），不支持据此采纳。候选第2版仍待人工审阅，未批准/部署，2019最终测试封存。见 [阶段验收](docs/CAPACITY_STAGE_REPORT.md) 与 [可读比较报告](examples/capacity-comparison.md)。以下各批是历史状态。

**2026-10-21：G2-A 源内调查评测阶段已验收。** 12 名用户、24 个任务、三组对照、72 个执行单元，61 份成功报告独立 SQL 复核、11 个失败保留；全仓 412 测试通过。用户隔离评测：固定脚本 12/12、固定证据模型 7/12、工具型 Agent 最终 12/12（首次 0/12，均经一次格式纠正）。没有成本/查询优势或真实业务效果结论。先读 [阶段验收报告](docs/SOURCE_MERCHANT_STAGE_REPORT.md)，可直接看 [示例报告](examples/merchant-source-distribution.md)。

**最新：新增一次受控格式纠正，首次与最终成绩分别保存。** 两个已使用的商户开发题新真实验证首次 1/2、最终 2/2，通过独立 SQL 与轨迹复核；覆盖不足题仍有重复查询。8 个手工任务离线链 8/8，全仓 398 测试通过，尚非保留评测或业务收益。见 [第 21 课](docs/lesson21-format-recovery.md)。

**最新：商户调查已有 8 个手工开发场景，离线控制链与独立 SQL 复核 8/8 通过，全仓 385 测试通过。** 两题真实模型验证 0/2，通过率不与离线混报：均有输出格式错误，其中一题还忽略资料覆盖不足。新框架/组件的采用条件与现有缺口见 [架构演进说明](docs/ARCHITECTURE_EVOLUTION.md)；尚未引入 LangGraph、文档 RAG、MCP 或长期记忆。

**本批新增：用户内商户分布调查。** 默认比较近 7 天/30 天，分别计算笔数与正金额占比；离线报告和独立原始 SQL 核验通过，全仓 371 测试通过。首次真实模型把比较方向说反，系统拒绝答案、没有发布报告。外部真实数据寻找后置，优先在合成环境扩展能力和评测。见 [直白讲解与运行方法](docs/LESSON_20_MERCHANT_RESEARCH.md)，结果包含失败记录。

**先看 [项目总进度与剩余验收](docs/PROJECT_STATUS.md)。** 当前主链已通，但原定 V1 尚未验收；工程建设快于价值验证，后续优先任务能力、数据适用性和独立评测。新增真实开发场景对照后，四题双方均通过、均查询 16 次，尚无 Agent 效率优势证据。下方逐批记录保留当时状态。

**第十九批：已有统一离线演示命令。** `python -m app.cli.project_demo` 可生成新调查、Fake 候选、实际本地回放及独立演示审阅记录，六阶段实测完成，0 次模型请求。新增进度保存和索引检查，全仓 344 测试通过；尚无自动恢复能力。命令、前提与结果入口见 [第十九课](docs/lesson19-unified-offline-demo.md)。

**第十八批：新增版本绑定的本地候选审阅。** 真实候选已登记为第 1 版、待审阅；材料改变后旧审批不再适用。状态、幂等回执和哈希链可复核，新增 22 测试，全仓 340 测试通过；没有批准或部署实际候选。讲解和状态命令见 [第十八课](docs/lesson18-versioned-review.md)。

**第十七批：持久凭据自动读取、正常场景真实对照、模型选模板到开发回放均已实际验证。** 模型候选仍为未批准、未部署，不能认定策略有效；全仓 318 测试通过。当前状态与免输入验证命令见 [第十七课](docs/lesson17-model-proposal-to-replay.md)。下方逐批记录保留当时状态，最新状态以本段和项目地图为准。

**第十六批：新增一次性 Windows 凭据配置和候选规则增量回放。** 2018 开发期 422,088 笔数据上，手工候选在冻结树模型名单外新增 22 笔欺诈标签，同时新增 2,863 笔正常审核；追加审核不等于同预算提升。Python 与 SQL 复核通过，全仓 301 测试通过。讲解、一次性密钥保存和后续免重复输入命令见 [第十六课](docs/lesson16-candidate-replay-and-credentials.md)。尚未接通模型提议与人工审阅闭环。

**第十五批最新：已增加同案例、同工具权限的固定流程对照，离线双方均通过 9 个开发任务；全仓 274 测试通过。尚无真实模型相对固定流程的收益结论。** 讲解与单题真实对照命令见 [第十五课](docs/lesson15-fixed-workflow-comparison.md)。下方各批测试数为当时记录。

支付反欺诈调查与规则验证工作台。目标是帮助风险分析人员核查预警、查询历史行为、整理证据、提出受限规则候选，并在冻结样本上验证增量和误报。

**当前状态（2026-10-18）：首条真实模型调查已通过事实、引用和 usage 复核；模型经一次格式纠正完成报告。新增 9 个手工开发任务，离线策略通过 23 项要求，尚非真实模型任务集成绩。数据、经典模型、时间对照和 PyTorch 首轮结果保留；尚无最终测试、真实业务效果或完整规则验证闭环结论。**

**入门读 [按具体例子排列的学习目录](docs/LEARNING_PATH.md)**：第十一课连接工具调查，第十二课讲真实接口，第十四课讲任务评测。整体进度见 [项目地图](docs/START_HERE.md)。最近全仓 260 测试通过；真实效果看独立评测，下方早期测试数是历史记录。

最新证据和免密钥运行方式见 [第十四批：真实调查成功与开发任务评分](docs/lesson14-investigation-evaluation.md)。本批核查真实运行 `live-case-v1-d1dde6e08986`，新增开发任务集实测 `investigation-dev-v1-f14dedada88d`。

第十三批曾因最终回答夹带说明文字被拒绝，排查见 [真实调用与格式修复](docs/live-v2-format-repair.md)；第十四批已验证修复后真实运行成功。下方“真实调用 0 次”等为早期记录。

第十二批已实现受控硅基流动客户端，并完成 5 次模拟 HTTP 请求、4 次工具查询和独立事实/usage 复核。当前缺少本地密钥，真实模型调用仍为 0。运行方法见 [接口与费用讲解](docs/lesson12-real-model-and-budget.md)，验证证据见 [第十二批结果](docs/live-v1-results.md)。

本聊天只负责风控项目；TradingAgents 由其他 Agent 负责。本科项目不作为依赖。仓库物理目录暂保留 `financial-report-agent`，避免重命名影响独立环境。

当前规格与验收依据：

- [第十二批结果：硅基流动客户端、请求预算与模拟验证](docs/live-v1-results.md)
- [第十一批结果：预警如何变成有证据的调查报告](docs/case-v1-results.md)
- [第十批结果：PyTorch 训练曲线、重训与模型比较](docs/mlp-v1-results.md)
- [第九批结果：时间变化、渠道消融与 PyTorch 协议](docs/temporal-v1-results.md)
- [第八批结果：两个模型的训练与错误分析](docs/ml-v1-results.md)
- [模型学习路线：时间验证、PyTorch 与 Agent 接入](docs/model-learning-roadmap.md)
- [第七批结果：初始规则的效果与局限](docs/rule-baseline-v1-results.md)
- [第六批结果：历史线索已算好](docs/features-v1-results.md)
- [当前数据说明：来源、合成属性、许可记录与适用范围](docs/DATA_CARD.md)
- [全量数据剖析结果与下一步决定](docs/data-profile-results.md)
- [第五批：固定样本结果与下一步](docs/cohort-v1-results.md)
- [第五批教学：抽样、预热与时间切分](docs/lesson05-cohort-and-splits.md)
- [第四批：如何做全源数据剖析](docs/lesson04-data-inventory.md)
- [风控项目规格](docs/RISK_PROJECT_SPEC.md)
- [FinCopilot 源码核查](docs/fincopilot-review.md)
- [G0 数据验收](docs/data-gate.md)
- [课程底座来源](ORIGIN.md)

本次新增的有界数据探测（在仓库根目录 PowerShell 运行）：

```powershell
.\.venv\Scripts\python.exe -B scripts/probe_tabformer.py --rows 10000
```

命令联网读取固定提交的 IBM TabFormer 合成归档前缀，最多消费 8 MiB 压缩流，不解压写入、不执行远程代码、不保存或打印原始记录、不调用模型。结果写入忽略目录 `data/probes/tabformer-sample.json`。已实测 10,000 行、15 列，但仅有一个用户，不能视为代表性训练/评测样本。

实施顺序：G0 数据验收 → G1 查询/检测基线 → G2 单 Agent 调查 → G3 规则验证/审阅 → G4 持久工作流/工程交付。每阶段分批讲解、用户运行和反馈后推进。预算仍为人民币 20 元，当前真实调用 0 次。

第二批已加入数据合同、标签分离、事件时间边界和跨用户发现样本，见 [第二批教学](docs/lesson02-data-contract-and-time.md)。新增 17 个测试，全仓 66 个通过。运行：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_risk_data.py
.\.venv\Scripts\python.exe -B -m app.cli.data_gate --scan-rows 200000 --users 8 --per-user 32 --seed 42 --as-of 2019-12-31T23:59:59
```

该样本用于数据发现，不能作为完整历史窗口、总体欺诈率或模型效果的证据。时间边界只保证事件时间过滤，标签确认时间与入库时点仍未知。

第三批已实现完整源内历史快照、只读窗口工具及 Agent 工具接口，见 [历史窗口讲解](docs/lesson03-history-window.md)。完整归档 SHA-256 已核验，扫描 24,386,900 行，保留 8 用户一个月的 765 条记录；该范围无欺诈正例，不用于训练/召回评测。新增 20 测试，全仓 86 个通过。现有快照可直接查询：

```powershell
.\.venv\Scripts\python.exe -B -m app.cli.history_demo --latest --user 0 --as-of 2019-12-15T12:00:00 --hours 24
.\.venv\Scripts\python.exe -B -m app.cli.history_demo --latest --user 0 --as-of 2019-12-01T00:00:00 --hours 24
```

需要重建时运行 `python -B -m app.cli.build_history`（使用本项目虚拟环境）；首次约 266 MiB 下载，之后复用已校验缓存，完整扫描可能耗时数分钟。事实库和数据不提交 Git。后续按用户要求直接推进和讲解，不再提问。

<details>
<summary>历史年报方案（已停止推进，不是当前产品规格；环境与协议教学命令仍适用）</summary>

# Financial Report Agent（历史）

面向研究助理的 A 股年报研究助手，目标是跨公司完成财务变化核查、经营解释与证据追问。宁德时代（300750）2024、2025 年报为首个开发样本，不是最终产品范围。

**当前状态：独立课程基线和协议教学阶段。尚未实现财务资料获取、解析、财务计算或研究报告生成。**

## 产品范围

用户输入公司、两期年报、信息截止日和分析问题。目标输出为财务变化简报、事实/指标 JSON、来源与验证结果、运行轨迹。第一版聚焦 A 股非金融企业文本型年度报告，跨公司验证，不给买卖建议或目标价。产品级范围与验收门槛见 [产品规格讨论稿](docs/product-spec.md)。

与 TradingAgents、Dexter 等公开项目的设计对齐、借鉴边界和路线判断见 [参考项目评估](docs/reference-project-review.md)。当前处于产品评估阶段，业务开发暂不推进。

用户新增风控候选方向，最新推荐见 [五份 JD 对齐与支付反欺诈提案](docs/jd-alignment-and-fraud-proposal.md)。年报方案仍保留历史记录，尚未决定改名或转向；用户明确不考虑本科项目。旧的 [风控方向评估](docs/risk-direction-review.md) 供追溯，优先级以最新提案为准。

计划指标：营业收入同比、综合毛利率、毛利率百分点变化、经营活动现金流量净额同比。计算由代码执行；模型依据证据组织解释。缺失、口径冲突、重述和零/负基数须显式处理。

## 本地运行

在本仓库根目录使用 PowerShell。已建立 Python 3.12 独立虚拟环境；若重新安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

测试使用 Fake 和 HTTP Mock，不需要 API Key，不调用真实模型。当前依赖沿用课程固定版本，尚未重新划分运行与开发依赖。

第一个教学演示（读取合成夹具，不是金融分析）：

```powershell
.\.venv\Scripts\python.exe -B -m app.cli.protocol_demo
.\.venv\Scripts\python.exe -B -m app.cli.protocol_demo --missing
```

先阅读 [教学说明](docs/lesson01-fake-and-observation.md)，预测第二次请求的消息角色和缺失文件时的状态，再运行观察。

## 起步里程碑（原约 36 小时草案，最终排期待调整）

用户已要求提高最终项目完整性并愿意增加投入。下表仅为纵向起步链路，不能视为最终项目验收；跨公司验证、真实数据获取、失败恢复、评测与使用价值证据详见产品规格。

| 周 | 用户可见成果 | 验收证据 | 学习重点 |
|---|---|---|---|
| 1 | 独立基线、Fake 协议演示；后续登记资料并算一个指标 | 49 个基线测试、协议轨迹、来源/单位测试 | JSON、消息历史、Fake、事实模型 |
| 2 | 四个指标和固定工作流简报 | 口径/期间/重述/引用测试、离线样例 | 检索、确定性计算、报告验证 |
| 3 | 受限单 Agent，显式真实接口配置 | Fake/HTTP Mock/端到端、限额/超时/取消、脱敏日志 | 工具边界、控制循环、失败恢复 |
| 4 | 小型评测、复现和展示材料 | 金标准、保留集、基线对比、失败记录、README/架构 | 评测定义、成本时延、局限 |

每个知识批次单独讲解、运行、反馈和提交。跨公司验证、一个资料获取适配器和可复现运行记录纳入最终目标；复杂扫描件解析、全市场覆盖和部署仍为后续选项。所有能力以验收证据为准。

## 评测与预算提案

起步金标准约 24 题，最终目标为跨 3–5 家公司的约 40–60 题，至少一家保留公司；具体规模随标注与模型预算确定。按公司/文档或证据簇隔离开发和保留集。与固定工作流使用相同资料、模型和工具对比，记录准确率、引用支持率、完成率、工具调用、费用、时延及失败样本。离线测试和真实模型评测分别报告，不宣称全市场泛化。

每个输出数字必须可以定位并重算；引用存在不等于语义支持，后者人工核验。人工确认的事实必须注明人工参与。

真实调用总预算人民币 20 元：默认离线，后续显式开启 live；冒烟 2 元、排障 6 元、评测 8 元、余量 4 元。预算控制尚未实现，真实调用必须等实现并核对平台价格后再开始。本地估算不代表平台账户的硬消费上限。

## 来源

- 课程底座与复制清单见 [ORIGIN.md](ORIGIN.md)。
- 官方候选资料：[2024 年报](https://www.catl.com/uploads/1/file/public/202503/20250317094543_6ig9e0mwng.pdf)、[2025 年报](https://www.catl.com/uploads/1/file/public/202603/20260310105829_c5p2l3q9ll.pdf)。当前仅定位入口，尚未下载与核验快照。
- 原始 PDF 计划保存在忽略目录 `data/raw/`；公开再分发权限尚未核查，不将可下载等同于可再分发。

</details>
