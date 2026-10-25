# 混合调查：输入一个问题范围，拿到什么

现在可以针对一条预警，选择要检查的线索组合。程序按同一用户、预警截止时间之前的历史取证，模型只能使用这批受限工具；数字与答案由代码核对。

这版由用户明确选择检查项，不承诺理解任意自然语言问题。

| 检查项 | 实际问题 | 程序查询 |
|---|---|---|
| frequency | 此前1小时是否至少4笔？ | 用户此前1小时历史 |
| amount | 本笔正金额是否超过此前30天正金额最大值？ | 用户此前30天历史 |
| merchant | 此前30天是否没见过这个商户ID？ | 目标商户历史 |
| count_share | 近7天商户Top1笔数占比是否比30天高？ | 两窗口完整商户聚合 |
| amount_share | 近7天商户Top1正金额占比是否比30天高？ | 同一对聚合，复用查询 |

4笔和样本下限10都是本项目的实验政策，不是行业通用风险标准。全选需6个不同查询；金额+商户需3个；频次单项需2个，均包含读取目标。

## 先运行不用模型的版本

在项目目录执行：

```powershell
.\scripts\run_project.ps1 -m app.cli.mixed_investigation --arm fixed --checks amount merchant
.\scripts\run_project.ps1 -m app.cli.mixed_investigation --arm agent --checks frequency
```

第一条是按问题查询的确定性固定流程；第二条用脚本客户端演练Agent消息/工具循环，明确标为 `SCRIPTED_NOT_REAL_LLM`。两者都不发送真实请求。默认案例需已有本地数据。

要进行真实模型单题调查时，在第二条命令加 `--live`。它使用已保存凭据和已有预算账本，不需要重新输入API Key。`--arm evidence_only --live` 则由固定流程先取证，模型只总结相同证据，没有工具可调用。

`--min-records 10` 只约束两个集中度检查的两窗口记录数/正金额记录数。不会把金额历史最大值的政策偷偷改成“必须10笔”，也不会把频次4笔阈值改成10笔。

## 输出中的三种含义

例如“金额线索不支持，商户线索支持”：说明这笔金额没有超过历史最大值，但目标商户此前未出现；它并不等于“用户正常”或“用户欺诈”。报告会保留各自证据，不强行合成一个欺诈分数。

例如历史覆盖不足：未见过某个商户，可能只是资料不全，应写资料不足。不过，只要已观察到这个商户的一笔历史，就足以反驳“从未出现”，即使完整覆盖尚未证明。这叫对具体线索的反证。

集中度比较还要区分两件事：两窗口笔数够，不代表正金额样本也够；分母为0时不比较，不把0/0解释为0%。7天包含在30天内，这不是环比、因果结论或显著性检验。

输出目录包含 `report.md`、`trace.json`、`summary.json`、问题合同、策略快照和案例投影。真实调用另有 `usage.json`。被拒绝的任务保留原始输出与轨迹，不能只挑成功报告展示。

## 通过本机服务提交

仍用原服务启动方式。第三个终端中先按服务指南读取本机令牌到 `$serviceHeaders`，随后执行：

```powershell
$mixedBody = @{
    case_id = 'development-467716'
    task_type = 'mixed'
    mode = 'offline'
    question = @{ checks = @('amount', 'merchant'); min_records = 10 }
} | ConvertTo-Json -Depth 4
$serviceHeaders['Idempotency-Key'] = 'my-mixed-investigation-0001'
$mixedJob = Invoke-RestMethod http://127.0.0.1:8765/jobs -Method Post -Headers $serviceHeaders -ContentType application/json -Body $mixedBody
Invoke-RestMethod "http://127.0.0.1:8765/jobs/$($mixedJob.id)" -Headers $serviceHeaders
```

服务可以查询、取消和恢复这类任务。恢复测试验证了2次响应回放；报告仍需人工阅读，接受报告不会批准风控规则。

## 如何判断模型有没有价值

本轮与强固定基线比较：固定流程同样知道用户选择了哪些检查，只查询必要资料，不读答案、标签或全库。模型不能靠“固定流程故意多查”获得虚假的效率优势。

主要看完整任务通过率、首次通过率、失败类型、工具尝试/额外查询、请求次数和估算费用。任务全部检查正确才通过，不能漏掉难项以后只统计已回答部分。

结果与下一步决定见 [混合任务阶段验收](MIXED_STAGE_REPORT.md)。合成源内结果不能直接换算成真实欺诈召回率或业务收益。
