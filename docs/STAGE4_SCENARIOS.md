# 第四阶段场景索引

60个自建合成场景，按家族固定为开发/回归/冻结审计各20例。冻结审计不等于外部独立留出。

| 家族 | 分组 | 场景编号 | 期望结构结论 |
|---|---|---|---|
| single_settlement | development | e4-f01-v1 … v5 | duplicate_record |
| multiple_settlements | development | e4-f02-v1 … v5 | needs_review |
| corroborated_change | development | e4-f03-v1 … v5 | normal_change |
| contradicted_change | development | e4-f04-v1 … v5 | needs_review |
| history_gap | regression | e4-f05-v1 … v5 | insufficient_evidence |
| settlement_gap | regression | e4-f06-v1 … v5 | insufficient_evidence |
| missing_disposition | regression | e4-f07-v1 … v5 | insufficient_evidence |
| future_correction | regression | e4-f08-v1 … v5 | normal_change |
| obsolete_version | frozen_audit | e4-f09-v1 … v5 | normal_change |
| injected_disposition_conflict | frozen_audit | e4-f10-v1 … v5 | needs_review |
| history_gap_repeated_authorization | frozen_audit | e4-f11-v1 … v5 | insufficient_evidence |
| settlement_vs_confirmed_claim | frozen_audit | e4-f12-v1 … v5 | needs_review |

真实模型事前选定：e4-f10-v1, e4-f11-v1, e4-f12-v1。每案四条件，共12次案件运行。

场景文件SHA-256：`d7f81f6f27781a0a41ce6ee83c1700ca2b79998a2583aa7c0f4d61a05fbd4c25`。

完整快照、金标准、源码锁和方法快照在 `runs/stage4-freeze-v1/`；重建命令：`python -m app.cli.agent_evaluation freeze --bundle runs/new-freeze`。每次重建产生新的批次ID，不得将其作为绕过单批费用授权的手段。
