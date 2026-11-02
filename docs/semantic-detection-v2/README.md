# 检测研究V2.1

先看[本轮研究结论](RESEARCH_CONCLUSIONS.md)，再按问题进入下表。旧进度快照和首次失败均保留；当前最终数字以最终结果/复核报告为准。

| 阶段 | 当前决定 | 主要报告 |
|---|---|---|
| A | 失败归因完成；不采用IBM语义增强 | [归因](ROOT_CAUSE.md)、[渠道审计](CHANNEL_AUDIT.md) |
| B | 历史信号有用，Mean对强统计的独立增量未确认 | [完整结果](HISTORY_RESULTS.md)、[技术决定](HISTORY_DECISION.md) |
| C1/C2 | 来源审查完成，新业务文本不准入；不训练C2 | [候选审计](CANDIDATE_AUDIT.md) |
| D | 32实例可控研究完成；不采用一致性升级 | [结果](SEMI_SUPERVISED_RESULTS.md)、[判断](SEMI_SUPERVISED_DECISION.md) |
| D补充 | 9实例完成；Qwen98/普通97/随机93/TF-IDF99，无稳定增量 | [补充结果](LOW_LABEL_PRIOR_RESULTS.md) |

复现看[REPRODUCE](REPRODUCE.md)，首次失败看[FAILURES](FAILURES.md)，测试范围和指纹看[VALIDATION.json](VALIDATION.json)。本地数据、模型、逐笔案例及身份映射不随Git发布；复现前必须满足本地数据与权重前提。2018是已观察开发期，2019不是新的留出。默认检测器保持不变。
