# Track A 输入合同与恢复复核

## 输入合同

| 核验项 | 结论 |
| --- | --- |
| 事件与字典行号 | 发布方用 `enumerate(action_names)` 生成零基行号，并按原始事件号直接查表。发布样本实际 ID 范围为 1–1912，共 609 个，ID 0 无观测不构成换成一基索引的依据。模型输入 `i` 对应 CSV 数据行 `i` 和向量表 `i+2`，前两行保留给 PAD 和未知或缺失。假设一基映射会把 `/CARDS/FETCHCARDS` 放到两类高频榜第 4，零基映射则得到正类第 10、负类不进前十，与论文描述更相符。 |
| 间隔单位 | 官方 notebook 先逐项 `/1000` 再计算均值和标准差，论文图 1 把这两项标为秒。6,294,506 个有效时间槽均为有限非负值，原始中位数为 128,969，最大为 999,999。按发布方约定转换为 128.969 秒和 999.999 秒。原始日志时钟和可能的隐私处理无法独立还原。notebook 的总时长派生量还做了第二次除法，当前实验不使用该派生量。 |
| 神经模型末尾 256 槽 | 正类 19/101 条（18.8%）被截断，丢弃 3,759/15,841 个槽。负标签 3,087/105,201 条（2.9%）被截断，丢弃 574,124/6,278,665 个槽。序列长度中位数分别为 98 和 34。事件 TF-IDF 仍使用整段序列，跨架构比较包含输入窗口差异。 |
| 近似关联 | 对冻结训练集合做精确存在性查询，开发集中有 2,293/21,219 条（10.8%）至少有一条训练序列的 trigram 集合 Jaccard ≥ 0.9。这 2,293 条均为负标签，其余 18,926 条包含全部 23 个开发正例。精确前缀索引只排除数学上不可能达到阈值的候选，短于三项的序列作为独立短模式处理。 |
| 发布信息边界 | 未发布用户 ID、绝对交易时间、标签确认时间。负标签只表示发布时未被标为欺诈。发布 RFM 和终点日志的时点可得性无法独立重建。纯行为序列和含发布 RFM 的结果分开解释。仅评价匿名静态发布快照。 |

来源见 [发布方固定 notebook](https://github.com/pboulieris/FraudNLP/blob/76bf718418e80549c49ac615857f8edda352ec99/Fraud%20Detection%20with%20NLP.ipynb)、[固定公开词典](https://github.com/pboulieris/FraudNLP/blob/76bf718418e80549c49ac615857f8edda352ec99/vocab.csv) 和 [论文第 3 节](https://link.springer.com/article/10.1007/s10994-023-06354-5#Sec7)。时间槽包含 22 个保留间隔的空动作槽，所以比有效事件数多 22。

相似性敏感性保持模型与切分冻结。独立重放后，报告原开发集、去掉已检测到近训练序列的开发子集、以及全量固定 1% 队列中近似样本占用。近似子集零正例，无法单独解释其 AP。该筛查也无法控制未知用户、3-gram 指标未覆盖的关联或标签延迟。仅使用开发标签，无最终测试评分。

对原先保存的 18 份分数先做了同一敏感性计算。下表是三种子平均分数的结果。筛除前后各按当前集合的 1% 设定队列，分别为 213 和 190 个名额。全部 23 个正例留在两个口径中。

| 输入 | 模型 | 全量 AP → 排除近似 AP | 两口径捕获正例数 | 全量队列中近似负标签数 |
| --- | --- | --- | --- | --- |
| 发布数值/RFM | 逻辑回归 | 0.1483 → 0.1527 | 15 → 15 | 13 |
| 发布数值/RFM | 随机森林 | 0.3716 → 0.3738 | 16 → 16 | 13 |
| 行为序列 | 事件 TF-IDF + LR | 0.2690 → 0.2714 | 14 → 14 | 16 |
| 行为序列 | 事件 TF-IDF + SVM | 0.2863 → 0.2868 | 13 → 13 | 9 |
| 行为序列 | 事件 Embedding + GRU | 0.2329 → 0.2367 | 13 → 13 | 15 |
| 发布数值/RFM + 行为序列 | 组合 LR | 0.4766 → 0.4783 | 20 → 20 | 16 |

这个敏感性口径下，已完成模型的排序和捕获数保持一致。它只检验去掉已识别近似负标签的影响。Transformer 完成后才构成完整 A2，尚无预训练表示结果可用于顺序、时间或语义增量判断。

## A3 顺序控制修订

原注册版本先打乱所有历史槽，再保留末尾 256 个槽。实测这使 19 条被截断正例的可见事件多重集合全部改变，负标签中 3,086 条也受到影响。原版本的两个顺序消融因此同时改变输入事件和顺序，无法单独识别顺序效果。

原 `track-a-a3.json`、冻结训练源代码和旧运行目录均保留。停止旧协调流程并在任何正式 A3 拟合前登记 `track-a-a3-suffix-control.json`。修订只把两个顺序消融改为先截断，再在可见窗口内打乱历史槽，保留末槽。时间间隔仍由这两组及其有序对照共同移除。14 个实例的表示类型、种子、轮数、提前停止、数据和切分保持一致。新入口要求 21 实例 A2 独立开发重放收据和输入审计收据。

## A2 恢复与复现

原目录的 18 个完整模型和全部分数已建立独立 SHA256 清单。首次 Transformer 种子 42 留有 23 轮。上次从头补跑留有 13 轮，与首次运行的前 13 轮损失及 AP 完全一致。两次恢复进程重叠期间，Windows 报内存不足或原生异常退出，未得到新的选定检查点。后续只启动一个训练进程，从首次第 23 轮恢复模型、优化器和 Torch RNG，并通过重建前 23 轮的 NumPy 分桶调用恢复下一轮顺序。早停状态由首次曲线恢复。种子 43、44 使用原训练函数。恢复测试在合成数据上核对了完整曲线和权重逐项一致。

```powershell
$py = '../financial-report-agent/.venv/Scripts/python.exe'
& $py -B -m scripts.behavior_v3.audit_inputs --protocol runs/track-a-a2-01/protocol.json --notebook 'runs/behavior-v3-source-01/Fraud Detection with NLP.ipynb' --dictionary runs/behavior-v3-source-01/vocab.csv --output runs/track-a-input-audit-04
& $py -B -m scripts.behavior_v3.resume_transformer --protocol runs/track-a-recovery-01/protocol.json --partial runs/track-a-a2-01/transformer/seed-42 --output runs/track-a-a2-transformer-resume-02
& $py -B -m scripts.behavior_v3.continue_research --protocol config/behavior-semantic/track-a-a3-suffix-control.json --a2-transformer-retry runs/track-a-a2-transformer-resume-02 --input-audit runs/track-a-input-audit-04 --output runs/track-a-continuation-03
```

运行命令须串行。重现时使用新的输出目录，保留首次产物。恢复的种子 42 单独报告续跑计时，最初片段的逐模型 CPU 耗时不可恢复。最终评分需要在开发选择和 A4 决策冻结后提交许可检查。

种子 42 已从第 23 轮续跑至第 27 轮，按冻结早停规则选中第 22 轮（开发 AP 0.1568875）。随后执行会话在种子 43 第 19 轮后退出。新的独立本机进程通过 `--previous runs/track-a-a2-transformer-resume-02` 复用完成的 42、从 43 的第 19 轮继续，并在同一串行流程内开始 44。最终恢复输出目录为 `runs/track-a-a2-transformer-resume-03`。按前文命令复现这次续跑时，须增加 `--previous` 并把两处恢复输出路径换成新的目录。
