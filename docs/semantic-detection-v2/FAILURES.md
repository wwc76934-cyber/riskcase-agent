# V2.1失败与修正记录

所有首次原文留在本机独立runs，后续输出用新路径；不覆盖旧模型、旧指标或失败。

| 事项 | 首次证据 | 处理与实际影响 |
|---|---|---|
| 本机Git缺少原生登录凭据 | 工具输出`Cannot prompt ... unable to get password` | 使用已连接GitHub账号发布，并核对整个内容树；不读取或输出令牌，不伪称原生push成功。 |
| Matplotlib默认用户缓存不可写 | `runs/semantic-v21-initialization/report-a.log` | 图像仍成功生成并视觉检查；后续在专属runs设置MPLCONFIGDIR，不改用户全局配置。 |
| Joblib物理CPU探测子进程被系统拒绝 | A1/聚类/树的首次原始日志 | 库回退逻辑CPU信息；本研究另固定2/4线程，不将探测警告包装成模型失败或忽略真实运行结果。 |
| 行业范围通用端点校对 | 原bank manifest与`category-rule-equivalence.json` | 航空上界修正到3350、租车范围3351–3441；当前词表六套文本逐条完全不变，原向量与manifest保留，未重写训练。 |
| 原CSV额外抽查初版未识别卡号命名空间 | `runs/semantic-v21-raw-sample-01.log`及`first-mismatch.json` | 事实库按合同存`用户:原卡序号`，原CSV只有卡序号；其他字段与标签均一致。只修正独立核对程序，02运行的128笔分层原CSV抽查全部通过，数据/图/模型未变。 |

原冻结模型配置最初被误读为类的默认engineered=true，核对selected-recipe和实际权重后即更正：实际冻结模型是raw/no-channel/5轮。最终报告及2×3协议均按实际配置，并通过全分数重现误差0验证，不保留错误归因。

强控制的事后追加单独登记，不倒写成预注册主比较。资源检查实测本机总内存约15.3GiB、当时可用4.8GiB、C盘剩余约11.2GiB；大矩阵的广覆盖统计控制排在主训练退出后执行，避免内存争用。
