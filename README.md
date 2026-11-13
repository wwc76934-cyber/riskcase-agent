# RiskCase Agent V4：欺诈告警调查中的 LLM 语义理解与受控 Agent

面向支付风险分析师的事后调查研究原型。检测层给出告警，调查层用 LLM 读客户陈述和多来源证据，宿主程序的规则表决定处置。数据全部为作者模拟，没有真实机构验证，不做自动欺诈认定、退款或资金操作。

## 结论速览

| 问题 | 结果 | 证据 |
|---|---|---|
| LLM 能读懂规则读不懂的客户陈述吗 | 能。6 个语义案例共 18 阶段：规则流 B0 判对 9 个，三个模型条件判对 15 到 18 个 | [基准报告](docs/FINAL_BENCHMARK_REPORT.md) |
| 让模型直接输出处置可靠吗 | 不可靠。模型原始输出通过 Tier 1 的只有 3 到 5 / 18。由宿主 Policy Guard 按模型引用的客户状态推导处置后为 14 到 15 / 18 | 同上 |
| 动态 Agent 比固定流程好吗 | 本批数据没有。准确率和证据成本都不优于固定规则取证加模型报告 | 同上，[门控研究](docs/V4_GATE_STUDY.md) |
| 旧评测的 0/17 是评分器问题吗 | 部分是。新评分器给 8/17，其余是处置或客户状态判错 | [重评报告](docs/G4_V4_RESCUE_EVALUATION.md) |
| 为什么冻结的 LLM 向量在 FraudNLP 上不如随机向量 | 向量高度各向异性：平均余弦 0.69，有效维数 92/1024。这只是相符的证据，不是已证明的原因 | [Track A 结案](docs/TRACK_A_FINAL_POSTMORTEM.md) |

这些结论的限制：案例是同一位 AI 作者写的，其中语义案例是在知道规则 B0 的关键词后设计的；每组 6 案例各跑 1 次，没有置信区间；没有人工审核标签。完整限制见各报告。

## 架构与职责边界

```mermaid
flowchart LR
  A[告警与来源原件] --> B[MCP 受控取证工具]
  B --> C[LLM: 带引用的事实 + 客户状态]
  C --> D[Policy Guard: 校验引用后推导处置、停止理由、必选动作]
  D --> E[三态审计 SQLite: 模型原始决定 / Guard 决定 / 干预记录]
  E --> F[人工审核]
  B -.成本分档门控.-> C
```

* 模型负责：读陈述，抽取带来源引用的事实，给出客户状态。
* 宿主负责：来源权限、预算、引用校验、处置与动作的最终取值、审计留存。
* 模型提议的越权动作（如冻结账户）由 Guard 删除并记录，其余额外建议保留为非强制备注。

## 主要模块

| 模块 | 作用 |
|---|---|
| `app/evaluation/v4_score.py` | 两层评测：第一层硬门槛，第二层连续质量分。旧严格结果保留对照 |
| `app/investigation/v3/policy_guard.py` | 确定性 Policy Guard 与结构化谓词 |
| `app/investigation/v3/guard_audit.py` | 三态审计写入 SQLite |
| `app/investigation/v3/gate4_stream.py` | 流式传输，解决响应头前读超时 |
| `app/investigation/v3/tier_gate.py` | 付费来源按档位顺序放行（默认关闭） |
| `app/investigation/v3/untrusted_text.py`, `pii.py` | 来源文本的指令式内容筛查，个人信息脱敏。尚未接入线上请求路径 |
| `app/evaluation/v4_heldout.py` | 12 个冻结后新写的案例 |
| `app/evaluation/v4_runner.py`, `scripts/run_v4_study.py` | 带授权文件、批次上限、遇未知请求即停的付费运行器 |

## 复现

Python 3.12。无需密钥即可离线重算：

```powershell
pip install -r requirements-investigation.txt pytest numpy
python -m pytest -q                      # 部分测试需要 requirements-dl.txt 才能收集
python -m scripts.heldout_b0_check       # 规则流在新案例上的结果
python -m scripts.analyze_embedding_anisotropy name=path.npy   # Track A 分析
```

付费运行需要本机凭据和 `runs/` 下的账本，参见 `scripts/run_v4_study.py` 文件头。原始轨迹不入库。

## 当前缺口

* 没有真实机构数据，也没有独立人工审核的标签。
* 检测层与调查层尚未用 SHAP 原因连接。
* 提示词注入筛查和脱敏模块已有单元测试，但没有接入请求路径。
* 没有服务接口、界面、可观测性和权限模型。
* 动态 Agent 的成本门控只有少量触发，真实作用没有证明。
* 完整测试：1153 通过，5 跳过，7 个模块因未装深度学习依赖无法收集。

## 面试材料与历史

[面试防守笔记](docs/INTERVIEW_DEFENSE_PLAYBOOK.md) · [V4 基准](docs/FINAL_BENCHMARK_REPORT.md) · [门控研究](docs/V4_GATE_STUDY.md) · [旧 README](docs/archive/README.before-v4.md)，其中的历史数字分母不同，不要混用。
