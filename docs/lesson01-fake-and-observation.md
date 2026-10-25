# 第一课：Agent 怎样真正调用工具

本课只抓住一件事：**模型提出请求，程序执行工具，再把结果交回模型。**这几步连起来，才有实际行动。

## 一个具体例子

你说：“帮我读一下教学文件。”

| 步骤 | 发生什么 |
|---|---|
| 1 | 模型提出：“调用 read_file，读取这个文件。” |
| 2 | Python 程序检查路径，再真正读取文件 |
| 3 | 程序把文件内容或失败原因交回模型 |
| 4 | 模型收到结果后，继续回答或提出下一次查询 |

模型说“我要读文件”时，文件还没有被读。**工具才负责实际执行。**风控项目也是如此：模型提出查交易，查询程序返回真实数字。

## 三个词，用人话说明

- **FakeClient**：按我们写好的台词扮演模型。它不会思考，不调用付费接口。演练时，工具仍然真的执行。
- **消息历史**：Agent 给模型看的对话记录，包括用户要求、工具请求和工具返回结果。
- **Observation**：工具返回的结果。例如“读取成功，内容如下”或“文件不存在”。你可以直接把它理解为“工具回执”。

为什么要把结果交回模型？就像让同事查账，查完必须告诉他结果；只在后台查过、却没告诉他，他无法据此继续工作。

## 自己运行

在项目根目录 PowerShell：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m app.cli.protocol_demo
.\.venv\Scripts\python.exe -X utf8 -B -m app.cli.protocol_demo --missing
```

第一条读已有文件，应该看到 `tool success: True`。第二条故意读不存在的文件，应该看到 `tool success: False`。

两次都可能显示 `agent status: completed`。这里的 completed 只表示本轮回答结束，**不表示工具成功，更不表示金融任务完成**。所以后续项目要单独检查业务结果。

Fake 的结束回复也是预先写好的，不能据此证明真实模型理解了工具结果。

## 核心代码怎么找

先看 `app/cli/protocol_demo.py`：演练如何开始。再看 `app/agent/react_agent.py`：怎样收请求、执行工具、把结果放回对话。暂时不必读完客户端和网络代码。

<details>
<summary>有余力再看：JSON、调用编号与测试</summary>

JSON 是一种约定好的文本数据格式。例如工具参数 `{"path":"note.txt"}` 表示要读哪个文件。程序先把文本解析成 Python 字典，再检查参数；不能让模型直接执行任意命令。

工具调用编号 tool_call_id 用来把“哪次请求”和“哪份回执”配上，类似订单号。`fake.requests` 记录每次请求当时的内容，使用深拷贝，防止后面追加消息时改掉早先记录。

典型流程：第一次请求含 system、user；第二次还含 assistant 的工具请求和 tool 的回执。这里调用模型两次、工具一次。

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_lab03.py -k "pairs_tool_call_id or tool_failure_is_returned_to_model or fake_client_records_deep_snapshots"
```

更详细的原始技术记录保留在 [历史原稿](archive/lesson01-fake-and-observation.md)，不作为必须完成的问答作业。

</details>

下一课：[哪些是交易事实，哪些是答案](lesson02-data-contract-and-time.md)。
