# 从能运行到能独立解释

不按lesson编号一次读到底。先跑[项目地图](START_HERE.md)里的演示，再沿下面五段阅读。旧目录留在[历史版本](archive/LEARNING_PATH.pre-final.md)。本页不要求问答，目标是让你能独立核对、改动和说明。

## 1. 看懂一笔交易如何变成特征

先读[数据合同](lesson02-data-contract-and-time.md)、[历史窗口](lesson03-history-window.md)、[直白特征](lesson06-features-plain.md)。重点看features.py中窗口如何排除本笔、同刻与未来，为什么抽样历史不能当完整窗口，为什么先按时间切分再拟合预处理。

自己运行数据/历史/特征测试，观察空历史、同刻、边界、缺失和覆盖不足的输出。掌握SQL筛选与聚合、Pandas分组、分位数、缺失率、按年/渠道/用户分布；这些比先背模型名更基础。

## 2. 看懂模型训练与结果

先读[规则和指标](lesson07-rules-and-metrics.md)、[机器学习](lesson08-machine-learning.md)，再读[新神经模型指南](NEURAL_V2_GUIDE.md)、[开发结果](NEURAL_V2_RESULTS.md)、[最终时间评测](FINAL_DETECTOR_RESULTS.md)。

必须掌握：训练集拟合预处理、训练批次（本实现用randperm与手工切片，也应理解DataLoader）、Tensor形状、Embedding、残差连接、BCEWithLogitsLoss、类别权重、AdamW、train/eval、no_grad、验证选择、检查点重载、随机种子。先用小型测试看forward/backward与重载一致，再读真实训练曲线，不必一开始重训全数据。

PyTorch的输入是数字向量与MCC类别，输出一个logit，经sigmoid形成排序分数；它还不是可信的欺诈概率。AP好不一定每天10笔更好；少数类权重越大也不一定更好。我们的6方案和强树对照就是为避免凭直觉选模型。

2019已经是看过的最终结果。为了学习可在独立练习目录做小数据实验，但不能再用它调参后声称新的无偏测试提升。

## 3. 看懂Agent为什么需要宿主程序

读[Fake与Observation](lesson01-fake-and-observation.md)，再读[Planner V2设计](PLANNER_V2_DESIGN.md)与[对照结果](AGENT_V2_RESULTS.md)。

FakeClient返回预设回答，用来测试循环能否执行工具并把Observation放回消息。它不是模型能力证明。真实模型输出JSON也可能选错范围；所以要类型校验、原文依据、预算、状态和确认。确认后的数字由代码生成，不能让模型凭记忆算。

沿一个任务看输入→route.json→plan.json→确认→trace.json→report.md。对比“只查频次”和“先不查频次，只查金额”，看为什么格式正确与语义正确不同。

## 4. 看懂工程如何限制失败

读[持久任务决定](ADR_004_DURABLE_JOBS.md)、[服务指南](SERVICE_GUIDE.md)。关注幂等键、租约代数、响应日志、取消检查点、范围哈希与审阅版本。运行服务进程测试，看真正退出后如何回放已保存响应，为什么请求结果不确定时宁可停止。

消息历史不是长期记忆；SQLite任务库不是向量数据库；本地Bearer不是企业实名授权。面试时只说自己实际实现并验证的能力。

## 5. 看懂金融研究结论为什么可以是否定的

读[新DL同容量规则研究](CAPACITY_V2_REPORT.md)、[敏感性说明](DETECTOR_SENSITIVITY.md)。新增5个捕获不等于提升，因为同时挤掉6个。改一个输入后分数下降不等于这个因素造成欺诈。

最后用[面试说明](INTERVIEW_GUIDE.md)整理自己的叙述。代码由助手协助产生，只有你能够解释数据、修改一处逻辑并自己验证后，才适合说已掌握该部分。项目结果可展示，个人熟练程度要诚实。
