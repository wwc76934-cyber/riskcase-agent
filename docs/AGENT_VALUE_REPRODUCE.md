# 多来源欺诈调查：运行与复核

2026-11-02，正式结果与预算受限压力条件已收束。见[完整报告](AGENT_VALUE_FINAL_REPORT.md)。打开工作台不会发送模型请求；不能用开发成功例代替全体结果。

## 先区分三种检查

1. 干净检出可以检查代码、合同和离线单元/MCP测试；这些测试使用明确的夹具与探针客户端。
2. 逐案重算90案来源需要本机IBM合成源、原冻结模型输出及导出包。源数据、权重、运行数据库和模型原始请求没有上传Git。
3. 重新执行真实模型比较需要独立的明确费用/材料授权。已有264次案件的授权绑定本轮数据、模型、次数和账本，不自动授权另一次重跑。

项目Python通过`.\scripts\run_project.ps1`启动。依赖分别见`requirements-service.txt`与`requirements-investigation.txt`；重建检测源还需要相应检测依赖。Windows使用短的隔离测试目录：

```powershell
.\scripts\run_project.ps1 -m pytest -q tests/test_fraud_world.py tests/test_world_harness.py tests/test_world_score.py tests/test_world_feedback.py tests/test_world_workbench.py --basetemp=.test-review-w1
```

这些检查不使用提供商凭据。真实MCP子进程必须在允许本机进程/asyncio正常启动的环境运行。历史长临时目录曾使原演示原子JSON临时文件超出Windows路径限制；已在全部模型条件结束后改用短临时名并清理失败文件，首次失败、修复与回归分别保留。最小调查环境还验证了延后训练依赖导入的修复，不需要安装整个训练栈来使用调查运行时。

## 数据来源与重建

先按[IBM源重建说明](FRAUD_REBUILD_GUIDE.md)准备源数据、时间特征、冻结神经检测运行和导出包。已有本机前置材料时：

```powershell
.\scripts\run_project.ps1 -m app.cli.fraud_world --release runs/release-demo-v1 --cohort-report data/cohorts/cohort-3bfdbaab486f/build-report.json --output runs/world-rebuilt-v1
.\scripts\run_project.ps1 -m app.cli.fraud_world --cohort-report data/cohorts/cohort-3bfdbaab486f/build-report.json --output runs/world-rebuilt-v1 --audit
```

输出目录必须新建；审计回执不可覆盖。原研究输入是`runs/agent-value-world-v3`，包含30开发、40冻结、20挑战案例。`runtime/`只提供A/B资料，`controller/`包含作者参考和生成源码。`runtime-catalog.json`是本机可信注册入口，不作为HTTP路径参数接收。不要把`controller`注册成工具资源。

来源审计重算每个A层告警、用户历史、截止及模型投影，检查B层关联、覆盖、可得时间与版本。它不把B/C变成真实机构资料，也不对全部自然语言解释作专家认证。

## 免费证据包与审阅

在独立的新服务目录运行：

```powershell
.\scripts\run_project.ps1 -m app.cli.workbench --directory runs/world-review-local/service --world-catalog runs/agent-value-world-v3/runtime-catalog.json --port 8765
```

打开本机8765端口，输入该目录生成的`auth.token`。页面载入90案后，可以生成完整证据包；它由代码控制客户端执行，显示为确定性控制，不是实际模型规划。普通离线工作线程不会领取live任务。

报告需要逐项复核事实、假设、时间线、建议与缺口。五项确认完成后，批准当前草稿内容，再提交本地模拟工单；修改内容使旧批准失效。相同内容重复提交返回相同工单。此入口没有冻结、退款、标签修改或资金工具。

不要在正式实验运行期间操作`runs/agent-value-upgrade-v1/service`里的方法版本或任务。浏览器验收使用SQLite备份隔离副本，撤下方法未改变原研究的冻结方法头。

## B0强代码对照

以下两个命令不调用模型，通过实际MCP读取全部可得资料：

```powershell
.\scripts\run_project.ps1 -m app.cli.world_benchmark --dataset runs/agent-value-world-v3 --fold frozen --output runs/world-b0-frozen-recheck-v1
.\scripts\run_project.ps1 -m app.cli.world_benchmark --dataset runs/agent-value-world-v3 --fold challenge --output runs/world-b0-challenge-recheck-v1
```

原正式B0结果保存在`runs/agent-value-b0-frozen-v1`与`runs/agent-value-b0-challenge-v1`。新命令产生新的复核运行，不能覆盖或挑选替代原结果。开发集强化后，通用提取函数未根据正式结果再改写。

## 真实模型实验的冻结与停止

原协议是[正式比较协议](AGENT_VALUE_BENCHMARK_PROTOCOL_V1.md)。当前整轮明确授权上限13元、项目28元，原8元授权及10元修订分别保留；24开发槽已用完，180正式与60记忆压力槽分别保留。每案最多6次模型请求、12次工具尝试。模型与原件范围不变，不把未使用的历史授权转到新材料上。

`world_upgrade propose/prepare`不发送模型请求；`run --live`同时要求有效的具体授权、来源/代码/协议指纹、次数和共享费用账本通过。前98个正式条件的源码冻结在Git提交`229fb0c775c561181bfc7cf331d94b08b11a56d4`。13/28财务修订在安全停止点形成提交`26b624ea6eba771bbf430116a416e6732225eadc`，只改变四份文件中的财务校验和统计上限；GitHub对应提交`d862af7`的树完全相同。剩余82个正式及60个记忆条件保留原问题、数据、方法快照、Skill和输出协议，只更新宿主代码指纹，原队列取消留痕。旧任务、授权、98个结果及原始源码不覆盖；没有重跑正式失败案件。

实际提供商调用的请求、对外响应、usage与初次错误分别落盘；不保存隐藏思维链。早期开发中9次响应在旧校验器前失败，未保存响应正文，这一缺口明确保留。三次传输失败费用未知，原预约和额外保守占用都保留，不记为0费用。

已知计费的空响应会终止该轮执行，失败案件计0分且不重试；可在保留停止回执后继续其余未执行案件。未知费用、模型/代码变化及预算问题必须停止检查，不能盲目重试。控制器续跑时会打印已完成行，这不是重新发送。

## 恢复证据

`app.cli.world_recovery_probe`只使用离线代码探针。给一个已有运行时快照和全新目录，先以`--exit-after-response`在响应落盘后退出86，再等租约过期后移除该参数恢复。冻结版实际进程实验见`runs/agent-value-world-process-recovery-v2`；维护后同样实测见`runs/agent-value-world-process-recovery-v3`：恢复代次2、回放1条响应、0次新增客户端请求、0次外部模型请求。

这是进程和日志机制的证据，不是新模型分数，也不代表任意生产网络故障已穷尽。

## 汇总与默认策略

分析器`app.evaluation.world_analysis.report`读取原数据、升级目录及两个B0目录，覆盖60×4主条件与20×3记忆压力条件。未满300个条件时状态明确为不完整，不选择默认策略。最终结果必须同时披露失败分母、Q分项、按情景家族聚类的区间、费用、延迟、未知占用与记忆污染/拒绝。

只有完整结果支持时才启用更复杂路径。B0接近最佳且没有安全错误时，产品默认采用代码；B2/B3需要超过预先冻结的增量门槛。不能按正式结果修改当前案例、提示、评分或重新运行失败案件选优。所有结论限于这个有限的内部模拟比较，真实欺诈捕获和分析师人效仍需另行研究。


## 无密钥便携核验

```powershell
python scripts/verify_agent_value_evidence.py
```

仅需Python标准库。检查300个条件记录、各组分母及Q、配对均值、费用/未知预约/风险占用、零请求条件、冻结默认策略、离线恢复及审阅声明。它不认证供应商、不重算未上传原件，不替代外部专家。压力条件8个遇到批次费用保护，其中5个0请求；另1个0请求来自操作复核错误，不能称60次均完成模型调查。

本轮已结束，1078次请求、264个注册槽、0请求在途；估算及风险合计12.9722100元、项目27.6490780元。当前源码在实验结束后有单独维护，研究输入与结果不重跑，原始模型分数仍属于冻结的两版源码。当前运行时检查不自动变成新模型成绩。
