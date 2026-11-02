# 历史Mean的新增与挤出案例

这是已完成MeanK6组的只读案例组件，不代表Gate B全阶段已验收。完整模型与最终宽统计挑战仍需完成。

| 对照 | 新增正例 | 挤出正例 | 净变化 |
|---|---:|---:|---:|
| frozen_residual | 114 | 21 | 93 |
| mean-self | 103 | 25 | 78 |
| flat | 101 | 24 | 77 |
| statistics | 96 | 32 | 64 |

每个对照固定选择新增正例、挤出正例、新增评测负例各6个，以既定review-tie哈希排序取前6，共72条；不按是否便于讲故事挑例。每条核对同用户卡且所有邻居严格早于目标。

完整案例仅存本机`runs/semantic-v21-history-cases-01/report.json`，包含源本地时间、金额/MCC、最近6笔事实、日排名及未校准分数。真实ID映射另存在同目录private-id-map.json，不提交GitHub。仓库仅提交聚合计数、选择规则和SHA256，不上传逐笔交易事实。

案例中的历史变化是可观察事实，不是模型得分的因果解释。正负标签来自同一IBM合成基准，不包装成真实结案，也不跨数据源拼接用户。原点特征的count24h仍属用户级；新邻居与14统计是用户卡级。

复现：`python -m app.cli.semantic_detection_v21 history-cases --source-root ../financial-report-agent --run runs/semantic-v21-b-01 --output runs/new-history-cases`。输出须新目录；不训练或重新选择候选。
