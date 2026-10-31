# 主线演示：同一条检测告警进入调查

2026-10-31。源数据是IBM合成交易；模型分数来自已冻结神经模型。调查客户端为确定性接线基线，真实MCP进程，0次大模型请求。不能称为真实银行案例或新Agent效果验证。

## 本机已有源数据与模型演示包

仓库根目录，Python 3.12；本机使用 `scripts/run_project.ps1`。新环境需要 `requirements-service.txt`、`requirements-investigation.txt`；旧发布包核验模块还会导入检测依赖，因此准备主线包也需 `requirements-dl.txt`。原始数据、训练权重不在Git中，来源及已有发布包准备方式见 [RELEASE_DEMO.md](RELEASE_DEMO.md)。不要用手写交易冒充缺失的模型源案例。

```powershell
# 输出目录必须不存在；不会修改原发布包或调用大模型
.\scripts\run_project.ps1 -m app.cli.fraud_bridge --release runs/release-demo-v1 --output runs/my-fraud-review

.\scripts\run_project.ps1 -m app.cli.workbench --directory runs/my-fraud-review/service --catalog runs/my-fraud-review/catalog.json
```

打开 `http://127.0.0.1:8765/investigations/page`，输入新目录中 `service/auth.token` 的本地令牌，点击“刷新任务列表”可查看已保存的四个任务；点击“加载检测告警”可以对同一注册告警再做一次免费核查。

本机已完成的包是 `runs/stage6a-fraud-bridge-v1`。如果只想查看它，把启动参数替换为对应目录即可。旧任务的最终报告仍可查看；源码变化后不能继续旧的未完成任务，这是冻结合同的正常行为。

## 核对哪些内容

1. 源交易ID、神经模型分数、模型版本与原告警保持一致；不是从12个手写案卷中挑一个来代替。
2. 明确两个时间：交易时点的事实截止和次日日初可得的日终排名。当前使用源时间、时区未知；不说成实时预警。
3. 三项行为线索显示数值与覆盖，统计口径是同一用户而非单卡；完整源字段、预览行和指纹可展开查看。三个预览行不是完整历史。
4. 保留持卡人授权、认证设备及外部结案资料缺失。三项线索全部不支持也不能“正常关闭”。
5. 保存草稿、逐项确认、再批准并提交本机模拟待办。没有联系客户、冻结账户、退款或将结论写回训练标签。

“历史支付差错实验”是单独折叠入口；底部清算/商户解释方法也属于历史实验，不能从欺诈任务发布此类经验。新主线不读取退款经验或争议知识库。

## 结果与数据边界

- `summary.json`：源材料核验、3650条审核记录的容量统计、四条任务状态及MCP接线信息；不含欺诈标签和真实模型能力评分。
- `service/investigations/jobs.sqlite`：冻结输入、检查点、待审阅结果；`job-*/mcp-handshakes.json`显示两个真实进程的协议发现。
- `catalog.json`引用本机已验证源投影路径。包不是可迁移的全量数据分发；换机器需要按原数据卡重新准备数据和路径。
- 不使用付费开关，不读取模型API密钥。真实数据与新LLM实验需先满足 [业务方案](FRAUD_PRODUCT_PLAN.md) 的下一阶段条件。
