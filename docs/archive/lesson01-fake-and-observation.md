# 第一个教学批次：Fake、消息历史与 Observation

目标：独立运行课程底座，并能解释“工具真的执行了，但模型是否收到结果”这一问题。本批不调用真实模型、不计算财务指标。

## 先预测，再运行

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -B -m app.cli.protocol_demo
.\.venv\Scripts\python.exe -B -m app.cli.protocol_demo --missing
```

运行前预测三个问题：第二次模型请求有哪些 role？`function.arguments` 为什么是 JSON 字符串？缺失文件时，`agent status` 和 `tool success` 是否相同？

## 输入、输出与数据流

输入为教学用户问题、预设的两个 assistant 回复、examples 下的合成文件。输出为两次请求的角色顺序、工具 Observation、Agent 状态、工具是否成功。这个演示不是金融研究报告。

```text
用户问题
  → Agent 把 user 消息加入历史
  → Fake.chat 保存第一次请求快照，返回 read_file 的 tool_call
  → Agent 把 assistant 消息加入历史
  → BasicTools 解析 JSON 参数、校验名称/类型/路径，调用 FileTool
  → FileTool 真实读取本地文件（或报文件不存在）
  → Agent 将结果序列化为带 tool_call_id 的 role=tool 消息
  → Fake.chat 保存第二次请求快照，返回预设结束文字
```

Fake 不推理、不读取 API，也不会根据 Observation 自动改变回复。它模拟模型回复，让工具和控制循环照常执行。因此它能验证协议和工程流程，不能证明真实模型会选择正确工具或理解金融资料。

## 核心概念

1. `messages` 是 Python 字典列表。HTTP 客户端会将整个请求转换为 JSON；`function.arguments` 本身按接口协议是一段 JSON 字符串，工具入口再用 `json.loads` 解析。
2. tool_call 只是执行请求，本地工具才产生真实结果。`tool_call_id` 将请求与返回结果配对。
3. Observation 进入下一次请求后，真实模型才有机会基于结果继续判断；本地执行成功本身不会自动告知模型。
4. Fake 的请求使用 `deepcopy` 保存快照，否则历史后续增长可能让“第一次请求记录”也看起来包含后来才产生的消息。
5. `AgentResult.messages` 保存最终历史；`fake.requests` 保存每次请求当时的历史。它们用途不同。

## 对照结果

第一次请求：`system -> user`。

第二次请求：`system -> user -> assistant -> tool`。

最终历史还多一个结束的 assistant 回复，因此共五条消息。工具调用本身不另算一次模型调用：此处两次模型调用、一次工具执行、两轮迭代。

正常模式：`agent status: completed`，`tool success: True`。缺失模式：仍为 `completed`，但 `tool success: False`；Observation 中错误类型为 `FileReadError`。这证明课程状态仅表示回答结束，金融业务验收必须单独判断。

## 自己验证

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_lab03.py -k "pairs_tool_call_id or tool_failure_is_returned_to_model or fake_client_records_deep_snapshots"
```

已有测试核查 ID 配对、失败回灌和深拷贝快照。无需为这段薄展示代码重复编写相同测试。

读代码顺序：`protocol_demo.py` → `fake_client.py` 的 `chat` → `react_agent.py` 的 `run` 与 `_execute_tool_calls` → `basic_tools.py` 的 `dispatch`。

请用自己的话解释：如果工具读取成功，但删掉 `_messages.append(observation)`，第二次请求缺少什么，真实模型为什么可能无法正确继续？反馈后才进入下一批的文档证据模型与财务计算。
