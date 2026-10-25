# 本地任务服务：怎么运行、怎么看结果

这一层解决的是“把调查作为一个可管理的任务”：提交后可以查询进度、取消、等待报告，进程重启后能识别已做完的步骤。它复用原来的金融工具与答案核验，不另写一套风险判断。

2026-10-22新增 `task_type='mixed'`，可选择频次、金额、商户和集中度组合，使用相同的任务生命周期与恢复边界。具体输入与限制见 [混合任务指南](MIXED_TASK_GUIDE.md)；原商户窗口接口保持可用。

只监听本机 `127.0.0.1`，适合个人研究与演示。默认使用固定脚本客户端，不调用模型；真实模式继续读取已有 Windows 凭据，沿用每任务最多6请求、估算1元、项目估算20元的预算。平台实际账单优先。

## 1. 环境与资料前提

已有本地数据时，默认案例来自 `config/service-cases-v1.json`。它只登记2018开发案例，不开放2019最终检测集。HTTP客户端只能提交案例ID，不能指定文件路径、SQL或命令。

新环境先用 Python 3.12 创建自己的环境，再安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-service.txt
```

完整机器学习/深度学习测试另需 `requirements-dl.txt`。干净仓库不含IBM原始文件及案例，服务源内演示须先按原数据链准备案例；服务夹具测试不需要下载资料或配置密钥。

当前工作区为保护原虚拟环境，把新增依赖放在 `.venv-service-addons/`；`scripts/run_project.ps1` 自动加载。新环境正常安装依赖即可，无需照搬这个目录。不要把虚拟环境或密钥提交到Git。

## 2. 启动接口和工作进程

在项目目录的第一个终端执行：

```powershell
.\scripts\run_project.ps1 -m app.cli.service serve
```

在第二个终端执行：

```powershell
.\scripts\run_project.ps1 -m app.cli.service worker
```

两者默认共用 `runs/service-v1/jobs.sqlite`。接口接收任务，工作进程领取并执行；关闭接口不会删除任务。接口首次启动生成本机访问令牌文件 `runs/service-v1/auth.token`，只显示文件位置，不显示令牌内容。它与硅基流动API Key不是同一种凭据。

## 3. 提交一份调查

在第三个终端执行以下示例。令牌只保留在本机变量，不复制到聊天或报告。

```powershell
$serviceHeaders = @{
    Authorization = 'Bearer ' + (Get-Content runs/service-v1/auth.token -Raw).Trim()
    'Idempotency-Key' = 'my-first-investigation-0001'
}
$serviceBody = @{
    case_id = 'development-467716'
    mode = 'offline'
    question = @{ short_hours = 168; long_hours = 720; min_records = 10 }
} | ConvertTo-Json -Depth 3
$serviceJob = Invoke-RestMethod http://127.0.0.1:8765/jobs -Method Post -Headers $serviceHeaders -ContentType application/json -Body $serviceBody
$serviceJob.id
Invoke-RestMethod "http://127.0.0.1:8765/jobs/$($serviceJob.id)" -Headers $serviceHeaders
```

输入含义：对这条预警所属用户，比较此前7天/30天商户集中程度；窗口覆盖、样本量、分母或引用不合格，就不能发布有效结论。它不是“判断这个人是不是骗子”。

同一个幂等键和同样输入返回同一任务，不再新增任务；改动输入必须换键，否则返回409。它不能阻止使用不同键重复提交，请妥善管理真实调用。

## 4. 查询、下载和取消

```powershell
Invoke-RestMethod "http://127.0.0.1:8765/jobs/$($serviceJob.id)/events" -Headers $serviceHeaders
Invoke-WebRequest "http://127.0.0.1:8765/jobs/$($serviceJob.id)/artifacts/report.md" -Headers $serviceHeaders -OutFile tmp/service-report.md
# 尚未完成时可以请求取消；这不是退款操作。
Invoke-RestMethod "http://127.0.0.1:8765/jobs/$($serviceJob.id)/cancel" -Method Post -Headers $serviceHeaders
```

可取材料限于 `report.md`、`trace.json`、`summary.json` 和真实/HTTP Mock运行的 `usage.json`。材料摘要改变后会拒绝下载或审阅。失败任务可能只有轨迹和摘要，没有成功报告。

| 状态 | 直白解释 |
|---|---|
| queued | 已存好，等工作进程领取 |
| running | 正在调查 |
| cancel_requested | 已收到取消；若正在等HTTP响应，可能还需等它返回 |
| cancelled | 已停止，不能发布成功报告 |
| awaiting_review | 答案已通过代码核验，等人阅读报告 |
| completed / rejected | 人接受/拒绝这份研究报告 |
| failed | 调查答案或协议没有通过，保留失败材料 |
| blocked | 恢复条件不安全，例如请求结果不确定、材料或代码改变 |

`awaiting_review` 不等于欺诈成立，更不等于规则已批准。报告审阅接口 `/jobs/{id}/review` 只接受 `accept_report` / `reject_report`，必须带当前 `expected_version`、`result_hash` 和审阅幂等键。它不修改既有金融候选审阅库，不执行冻结、赔付或规则部署。

## 5. 真正恢复了什么

例如已收到并保存前两次模型响应，随后进程退出：

1. 旧工作进程的租约到期后，新工作进程领取任务。
2. 从相同案例与相同程序重新走控制循环。
3. 前两次请求的消息和工具Schema摘要相同时，读取已保存响应，不再发HTTP请求；只读工具重新计算。
4. 后面的未执行步骤继续；预算账本仍使用同一个任务ID，原180秒任务期限不重新开始计算。

默认租约90秒，恢复代数最多5代。当前API请求超时最多45秒；租约不是后台无限续租线程。暂停过久、超出期限或丢失租约时保守停止。

另一种情况：请求已发出，进程却在响应落盘前退出。我们无法知道平台是否已扣费。系统把任务标为 `blocked`，保留预算占用，不自动重发。即使请求可能还没真正发出，只要只有意图记录，也按不确定处理。当前没有“强制继续并再次付费”的接口。

取消也是协作式的：不能保证撤销已经发出的HTTP，响应可能照常计费。若返回前已取消，会保存已收到的响应，但不发布成功报告。

## 6. 自己验证

```powershell
# 不用数据下载、不用密钥：接口、失败边界和真实进程退出/重启测试。
.\scripts\run_project.ps1 -m pytest -q -p no:cacheprovider tests/test_service.py tests/test_service_process.py
# 使用已有IBM开发案例，0次模型调用；目录必须是全新的。
.\scripts\run_project.ps1 -m scripts.service_stage_demo --directory runs/my-service-recovery-demo
```

第二条命令在保存第2次脚本响应后直接退出工作进程，再启动新进程，最后独立SQL复核源内证据。`acceptance.json` 记录结果，报告仍待人工阅读，不自动接受。

真实模式仅在准备好后，给接口和工作进程都加 `--allow-live`，提交时设 `mode='live'`。不需要重新输入已保存的API Key；不要在请求体中添加API Key。任务报告验证失败不会无限重试到成功。

## 目前边界

这是本地研究任务服务，尚无企业账号、角色权限、多人审阅分工、队列配额、分布式调度或对外TLS部署。SQLite与本地材料仍受主机文件权限保护；摘要能发现材料变化，不构成防管理员篡改的审计系统。没有运行多Agent、文档RAG、MCP或长期记忆。

GitHub Actions已配置Windows/Linux离线测试，但本地通过不等于远程CI已成功；首次推送后还须检查实际结果。
