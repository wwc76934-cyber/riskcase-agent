# 同用户同日日终告警导航：实现与验证

2026-10-31。对应业务计划6D的队列整理部分；仍为IBM合成源研究，不是已证明人工收益的合并调查。

## 改变的行为

原来只统计到308个同用户同日重复名额。现在可以核验完整日终名单、生成可追溯分组，并把某组全部成员逐笔导出为工作台可注册的源案件。页面按日期和原排名展示，可按组过滤；组按源事实版本、检测模型版本、用户和日期隔离，展示版本标识。

每笔保留原分数、排名、事实截止和独立审批。不补入排名11以后的交易，不共用日终截止进行较早交易的历史取证，不提供组级批准。再次核查创建新任务，原任务和批准保留。页面明确只覆盖当前已装载资料，完整交易标识可展开查看。

## 实测

- 完整冻结队列3650笔，分组3342个；分组前后每笔字段逐一相等，无遗漏、重复、补入或重排原风险名次。仍为每日10笔预算，不是每日10组。
- 按预先固定的成员数、日期、排名和ID口径选出最大六笔组；未读取标签。六笔全部导出，六个不同事实截止，从同一验证过的源事实库读取。
- 最终浏览器实际运行六笔确定性调查，经真实MCP取得30份当前证据；另重新核查一笔取得5份证据，合计7次任务、35份证据。原始浏览器回执的`current_fact_evidence=30`仅统计首轮六笔，不含重开一次。
- 只批准一笔时，其他五笔无草稿/批准；新任务不继承原批准，原任务批准仍保留。没有组级副作用，没有模型费用。
- 390像素窄屏无横向溢出，前端无异常、无外部请求。检查截图后纠正了原目录按内部ID排序导致优先级混乱的问题；最终页面与选单按原排名展示，源行短标识用于阅读，完整交易ID保留在可展开区域。
- 受影响共享回归59项通过；之后针对最终API隔离与准备合同的12项通过，与59项重叠，不能相加称为71项不同测试。最终页面另经真实浏览器确认。

## 保留的失败

相同分数时，队列生成器按`(score,tie_hash,transaction_id)`倒序排列，原校验器却对tie_hash正序校验，错误拒绝合法队列。先保存失败测试，再修正校验器；没有改变已冻结名单或重跑检测模型。

新准备测试首次传错项目根路径，缺少配置文件；新浏览器脚本首次在页面完成任务选中前读取current，得到404。分别修正测试配置和等待方式：浏览器绑定创建响应中的任务ID轮询，再读取结果。原失败回执保留；这些是验证脚本错误，不计为产品任务失败率。

## 复现

需要已有验证过的`runs/release-demo-v1`及同源cohort数据；依赖与准备方式见[主线指南](FRAUD_WORKBENCH_QUICKSTART.md)。以下命令不运行模型；输出目录需不存在。

```powershell
.\scripts\run_project.ps1 -m app.cli.fraud_queue inspect --release runs/release-demo-v1 --output runs/new-review-queue
$reviewPlan = Get-Content runs/new-review-queue/queue.json -Raw | ConvertFrom-Json
$reviewGroupId = ($reviewPlan.groups | Sort-Object @{Expression={$_.members.Count};Descending=$true},day,first_day_rank,group_id | Select-Object -First 1).group_id
.\scripts\run_project.ps1 -m app.cli.fraud_queue prepare --release runs/release-demo-v1 --group-id $reviewGroupId --cohort-report data/cohorts/cohort-3bfdbaab486f/build-report.json --output runs/new-group-review
.\scripts\run_project.ps1 -m app.cli.workbench --directory runs/new-group-review/service --catalog runs/new-group-review/catalog.json
```

进入`/investigations/page`，读取本机新目录的`service/auth.token`，加载检测告警并选择分组，逐笔核查。当前已准备本机目录`runs/stage6d-group-demo-v1`可直接用于演示；没有替换或覆盖原四案付费验收输入。

协议见[FRAUD_GROUPING_PROTOCOL](FRAUD_GROUPING_PROTOCOL.md)，不含原始行的汇总见[证据清单](evidence/fraud-groups-v1/manifest.json)。完整队列、六笔投影、数据库及截图留本机。来源与时间边界成立，不等于银行授权认证或独立分析师效果。

## 仍缺的证据

成员保持不变，只说明入选交易覆盖没有下降，不能说明调查质量、漏报率或人审时间保持不变。七次任务来自同一用户同日六笔交易（其中一笔重复），不是七个独立案例；浏览器批准由实验控制器完成，不是独立人审。

真实模型功能验收仍等待原四案八组批次授权。新分组不改变该批事实、提示、工具执行代码和预算。即使该批通过，独立业务任务、外部反馈及人工收益仍需另行取得；整体目标尚未完成。
