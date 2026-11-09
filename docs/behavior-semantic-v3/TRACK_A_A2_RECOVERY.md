# Track A A2 中断与恢复记录

A1 转换已在无网络 Docker 容器中完成。核对覆盖 105,303 条原始记录、101 个正标签、1 条损坏序列和 609 种实际事件。数据来源、环境约束和转换哈希见 [A1 审查](TRACK_A_A1_REVIEW.md)。

## 已观察的开发结果

| 模型 | 完成的种子 | 开发 AP |
| --- | --- | --- |
| 数值 LR | 42、43、44 | 0.1483 |
| 数值随机森林 | 42、43、44 | 0.3675、0.3249、0.3560 |
| 事件 TF-IDF + LR | 42、43、44 | 0.2690 |
| 事件 TF-IDF + 线性 SVM | 42、43、44 | 0.2863 |
| 数值 + 事件 TF-IDF + LR | 42、43、44 | 0.4766 |
| 普通事件 Embedding + GRU | 42、43、44 | 见每个种子的 `training.json`，已完成 21、24、24 轮 |

确定性对照的相同 AP 只列一次。组合 LR 与纯数值 LR 的同类对照给出开发 AP 增量约 0.3283，支持行为序列可能含有数值特征之外的信息。事件 TF-IDF 单独低于数值随机森林。开发仅 23 个正例，尚需配对不确定性分析。匿名静态划分及已发现的跨集合近似序列限制推断范围，绝对时间与用户分组不可用。

## 恢复记录

原 A2 运行没有正常完成标记。最后记录为 Transformer 种子 42 的第 23 轮，未保存该种子的选定模型。18 个已完成检查点和首次中断的全部产物保留于 `runs/track-a-a2-01`。

恢复协议只选取 Transformer 家族，保留三个种子、预算、提前停止、特征及切分，使用原冻结入口重跑该家族。启动时没有使用部分曲线缩减预算或挑选种子。重跑输出独立写入 `runs/track-a-a2-transformer-retry-01`。重跑成功后，`merge_a2_recovery.py` 验证协议、模型、分数及输入哈希并在新目录生成 21 个实例的开发对照。最终评测从清单引用原始检查点，保留每个检查点实际来源。

```powershell
& ../financial-report-agent/.venv/Scripts/python.exe -B -m scripts.behavior_v3.run_baselines --protocol runs/track-a-recovery-01/protocol.json --output runs/track-a-a2-transformer-retry-01
& ../financial-report-agent/.venv/Scripts/python.exe -B -m scripts.behavior_v3.continue_research --a2-transformer-retry runs/track-a-a2-transformer-retry-01 --output runs/track-a-continuation-02
```

第一次 A3 编码前核查了完整的公开字典。1,916 个名称中有 59 个省略前导斜线，其中有一个单段操作名。解析器现接受这两种已发布格式并保留名称原文。该修正发生在任何 Qwen 语义训练前，未改动 A2 的冻结训练代码。

后续在 A2 对照完整后运行本地公开词典编码和已登记的 A3 对照。协调程序只生成开发结果和配对分析。最终静态测试、A4 复杂模型准入及面试材料需要后续证据。
