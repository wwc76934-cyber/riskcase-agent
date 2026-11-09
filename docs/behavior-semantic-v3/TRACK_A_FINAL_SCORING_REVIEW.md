# Track A 最终评分许可检查

**待批准。** A2 的 21 个实例和 A3 的 14 个实例已通过独立重放。A4 已作出 [C 决定](TRACK_A_A4_DECISION.json)，模型范围冻结为这 35 个已选检查点，无新增拟合候选。最终测试尚未生成预测或指标。

| 检查项 | 冻结内容 |
| --- | --- |
| 模型及来源 | A2 与修订版 A3 全部 35 个实例。145 项文件指纹覆盖检查点、分数、训练记录、固定源代码、数据和评估证据。 |
| 分析范围 | 20,673 条冻结测试记录，按先前切分聚合有 18 个正例。队列按固定静态预算取 207 条，一笔捕获对应约 5.56 个百分点召回。 |
| 指标 | 主指标 AP。报告固定 Top 1% 队列的 TP、FP、Precision、Recall，并用已保存的开发分数选择 F1 阈值。保留所有种子与固定集成，报告四组主要配对差值及其精确序列簇不确定性。 |
| 防止测试驱动选择 | 不因测试结果换模型、调参、改切分或排除相似测试样本。切片结果只作描述。 |
| 适用范围 | 匿名发布快照的静态行为分类。用户和时间外推、真实每日审阅效果均未验证。 |

本地只读复核提案为 `runs/track-a-development-reports-01/final-scoring-proposal.json`，SHA256 为 `4ae2526f1e94491135daa33d56713e8e85fe11c6370cdcd55e2b29a1e6eaa3d0`。提案状态为 `pending_explicit_final_scoring_approval`，当前评分入口会拒绝执行。批准应绑定此指纹。批准后另存授权收据及可执行决定，并在新目录单次评分。

```powershell
$py = '../financial-report-agent/.venv/Scripts/python.exe'
& $py -B -m scripts.behavior_v3.freeze_final_review --a2 runs/track-a-a2-recovered-01 --a3 runs/track-a-a3-suffix-01 --a2-replay runs/track-a-a2-replay-01/replay.json --a3-replay runs/track-a-a3-replay-01/replay.json --analysis runs/track-a-development-analysis-01/report.json --a4-decision docs/behavior-semantic-v3/TRACK_A_A4_DECISION.json --output runs/track-a-development-reports-01/final-scoring-proposal.json
```

本地证据目录包含逐行分数和模型权重。GitHub 仅发布复现代码、协议、聚合结果和指纹。
