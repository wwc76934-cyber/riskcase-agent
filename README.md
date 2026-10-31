# RiskCase：欺诈告警调查工作台

**当前定位（2026-10-31）**：面向支付风险分析师的事后批量审核研究原型。检测模型回答“先看哪些交易”，调查层核查同一笔交易的历史行为线索与证据缺口，分析师决定后续调查。当前没有真实机构业务验证，不提供自动欺诈认定、退款处理或资金操作。

## 一条业务主线

```mermaid
flowchart LR
  A[交易与严格历史特征] --> B[检测模型排序]
  B --> C[日终固定审核容量名单]
  C --> D[绑定交易身份与截止时间的案件]
  D --> E[MCP事实工具与调查Harness]
  E --> F[行为线索、反证与缺失资料]
  F --> G[人工审核与本地待办]
  G -.尚缺真实来源.-> H[外部确认与结案结果]
```

告警分数不是欺诈概率；新商户或金额升高不是欺诈事实；三项线索均未发现也不能自动关闭告警。模型标签、调查发现和人工确认结果分开存放与评估。

## 当前已经接通什么

- 冻结神经模型的2018开发期422,088条分数对应3,650条日审核记录；已有4个无标签选择的用户案例，现已接入与新调查层相同的真实MCP、版本Skill、检查点、响应日志和内容绑定审批。
- 主线工具只读取同一用户、目标交易时点前的历史：1小时频次、30天金额基线、30天商户出现记录。统计复用已有验证代码；没有伪造设备日志、客户确认或退款记录。
- 新入口当前使用**确定性接线客户端**，不调用大模型。它证明检测→调查的连接与执行边界，不能作为Agent效果或智能规划成绩。既有模型实验单独保留。
- 日终名单核查发现308个同用户同日重复名额（3650条交易、3342个用户日期组合）。已加入保持全部成员的导航分组与六笔组工作台验证，仍逐笔取证和审批；不代表节省308次审核或提高召回。见[分组报告](docs/STAGE6D_GROUP_REPORT.md)。
- 欺诈审阅记忆已接入：候选来源重算、内容批准、版本更新、主体与双时间过滤、任务快照和撤下；后续任务仍重新读取当前事实。它不更新欺诈标签或自动学习新规则。

[业务纠偏方案与后续验收](docs/FRAUD_PRODUCT_PLAN.md) · [第6A阶段结果](docs/STAGE6A_REPORT.md) · [主线运行指南](docs/FRAUD_WORKBENCH_QUICKSTART.md) · [公开评测复现](docs/PUBLIC_EVAL_QUICKSTART.md) · [面试手册](docs/AGENT_INTERVIEW_PLAYBOOK.md)

## 数据和效果分开说明

| 证据 | 已有结果 | 边界 |
|---|---|---|
| 检测 | IBM合成源2019期423,189笔；每天10笔，残差三seed捕获99个源正标签，匹配强树94；差异区间跨0 | 固定408用户，已观察时间评测；不是银行真实交易验证 |
| 检测→调查 | 四个源案例、两个真实MCP进程、同一交易身份与分数绑定、人工审批 | 本轮是代码接线，未开展新LLM对照 |
| 队列 | 完整核查3650条已有日审核记录；重复用户日期容量统计 | 不是3650个Agent调查，也不是并发压测 |
| 外部匿名检测 | ULB/Worldline公开来源284807笔；时间保留92576笔，开发选中的逻辑回归AP0.7335，树0.7745，残差集成0.7714 | 新特征上分别训练；上游PCA范围未知；不是IBM权重已真实泛化或真实案卷 |
| 公共记忆检索 | LongMemEval-S全500题；470可回答题BM25会话召回@5为85.30%，完整证据会话覆盖75.32% | 无生成模型与judge；30不可回答题均非空召回，不能当可回答性 |
| 支付争议实验 | 已观察六个合成案卷两路径各6/6；动态成本约2.89倍 | 独立文档取证实验，不计入反欺诈效果 |

上游IBM约24M记录也仍是合成数据。本项目未声称训练了全量；新增真实匿名检测与公共检索实验见[6B与记忆报告](docs/STAGE6B_MEMORY_REPORT.md)。真实电商实体验证、独立调查任务与分析师收益均待验证。参见[数据卡](docs/DATA_CARD.md)、[来源边界](NOTICE.md)、[原IBM检测结果](docs/FINAL_DETECTOR_RESULTS.md)。

## 运行

Python 3.12，安装 `requirements-service.txt` 和 `requirements-investigation.txt`。有本机已冻结源数据与模型演示包时：

```powershell
.\scripts\run_project.ps1 -m app.cli.fraud_bridge --release runs/release-demo-v1 --output runs/my-fraud-review
.\scripts\run_project.ps1 -m app.cli.workbench --directory runs/my-fraud-review/service --catalog runs/my-fraud-review/catalog.json
```

访问本地8765端口，在调查页输入生成的 `service/auth.token`，加载检测告警并审阅；不产生模型费用。目录必须新建，具体说明见[运行指南](docs/FRAUD_WORKBENCH_QUICKSTART.md)。源数据/模型不在Git仓库中；没有它们时默认工作台只有手写安装夹具，不显示为神经模型源告警。

## 工程与研究资产

MCP负责受控服务协议，宿主负责范围/schema/预算；Skill负责可版本化指导，不授予权限；Harness负责可恢复执行与审计。退款/清算经验与反欺诈主线隔离。欺诈记忆使用有范围约束的结构化审阅记录，BM25已有独立公共检索证据，向量与重排仍为研究对照；不能把组件数量说成业务收益。[参考项目与原论文取舍](docs/REFERENCE_MEMORY_ANALYSIS.md)说明为何采用生命周期与治理、而没有堆叠五套记忆框架。

[第1阶段](docs/MULTISOURCE_STAGE1_REPORT.md)、[第2阶段](docs/HARNESS_STAGE2_REPORT.md)、[第3阶段](docs/RAG_STAGE3_REPORT.md)、[第4阶段](docs/STAGE4_REPORT.md)、[支付争议实验](docs/STAGE5_REPORT.md)保留原始成功与失败；[争议实验证据包](docs/evidence/dossier-v4/README.md)包含实际模型调用记录，材料仍是合成案卷。

[历史README](docs/archive/README.before-fraud-correction.md)保留纠偏前叙事，历史数字不可混用分母。项目估算模型费用累计13.048320元，本轮未增加付费调用。当前工程与产品验收分开，未完成的目标见[路线](docs/AGENT_ROADMAP.md)。
