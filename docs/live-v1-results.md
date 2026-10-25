# 第十二批：硅基流动客户端与费用记录

后续更新（2026-10-18）：已发现两次真实运行，认证成功但报告未通过；本页保留 2026-10-17 的模拟基线。当前状态见 [真实调用与格式修复](live-v2-format-repair.md)。

日期：2026-10-17。状态：**客户端和 HTTP Mock 已验证；没有配置本地密钥，真实模型调用仍为 0。** 讲解和操作见 [第十二课](lesson12-real-model-and-budget.md)。

本批接的是硅基流动中国站提供的 `Pro/zai-org/GLM-5.1`，地址固定为 `https://api.siliconflow.cn/v1/chat/completions`。没有使用课程客户端默认的 BigModel 官方地址，没有从原课程仓库读取或复制密钥。

实现提交：`f7326bcb906efa0908d2179163498dbd8307bc8a`。

| 验证项 | 实际结果 |
|---|---|
| 新增测试 | 31 个通过 |
| 全仓测试 | 238 个通过；保留原有 joblib 物理核心识别警告，已回退逻辑核心 |
| 预检 | case_ready=true，credential_present=false，real_requests_sent=0 |
| 模拟完整运行 | completed_validated；5 次模拟 HTTP 请求、4 次工具查询 |
| 事实复核 | 45 条投影记录和 4 条工具 Observation 经原库 SQL 独立复核 |
| usage 复算 | 5 次模拟用量，总估算 0.099840 元；这不是实际支出 |
| 2019 最终测试、标签 | 本批没有评估最终测试 case，没有打开标签库 |

模拟运行目录：`runs/http-mock-case-v1-0cf30ac14b9f`。

本地证据：summary、trace、report、policy、usage、case 投影，以及 `verification.json`、`usage-verification.json`。原始运行目录按既有约定被 Git 忽略；文档记录位置与摘要，不把临时运行结果当成源代码。

summary SHA-256：`1f83043268d4b3268f38121573a99e3a12e087deba5d251d21e6e5cf4154fd55`。
case facts SHA-256：`b7d0c9f1be4c6134df73c275acac793d627c50ee4102f6ddc764dc228d579f83`。
report SHA-256：`d4ef23a9a45f9cf51b8b5d5d07c877ef7ee12682d10f9074f47a1944064e3c78`。

本轮复用同一条冻结预警，不能算新增独立调查任务。模拟器每次提供的 2048 输入、128 输出 token 是测试数据，不是 tokenizer 实测结果。对真实模型的工具选择、JSON 遵循、参数兼容性及调查成功率均未下结论。

当前控制：最多 6 请求、无自动重试、输入/响应体积限制、超时与运行截止检查、缺 usage 保留估算、跨运行共享本地真实费用账本、工具/引用/结论校验。每轮本地估算阈值 1 元、项目 20 元，不是供应商费用硬上限或真实余额。

后续顺序：先完成一条有真实 usage 的小规模模型冒烟；再冻结开发/保留调查任务，比较同权限固定流程，记录工具选择错误、结论被拒绝、缺口处理、请求成本与成功率；然后进入候选规则与离线重放。暂不加入多 Agent 或新检测模型。
