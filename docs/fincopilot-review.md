# FinCopilot 参考项目代码核查

核查日期：2026-10-14。用户提供 13 张方案截图，并要求仅负责风控主项目；第二个 TradingAgents 项目由其他 Agent 负责。本科项目不纳入选型。

参考仓库：[1Adalt/finco-pilot](https://github.com/1Adalt/finco-pilot)，本次固定提交 `df39fb08ce3d2444568af9d62b5a34bbb5b0f122`。只读获取源码与样例，没有安装其依赖、执行仓库代码或复用其实现。本报告不是全仓安全审计。截图和 README 的意向、源码存在、实际计算、外部系统接入和业务效果分别判断。

## 判断

适合参考职责分层、统一结果合同、审批状态和证据归档。当前核查到的实现仍以初赛 Mock/规则原型为主，不能将“4 Agent + 6 Skill + 4 MCP”等组件名称当作生产能力证明。作者截图也明确说明 MCP 是 Mock，真实 Runtime 部署尚未完成。

根目录为四角色版本，仓库另有推荐的三角色版本。因此不能按截图认定当前只存在四角色实现。两版关键计算与连接仍有模拟内容。

## 源码证据

| 主题 | 固定提交中的可核查实现 | 对我们的启发 |
|---|---|---|
| MCP 接入 | `mcp/connectors.py` 的 `call()` 构造 JSON-RPC 数据后直接返回 `_mock_response()`；签名 header 未发送 | 本地函数、HTTP 适配和真实 MCP 会话要分别声明；协议名不等于接通系统 |
| RAG/LLM | `agents/risk_analyzer.py` 注明 RAG/LLM 为模拟；关联字段数满足条件即返回固定历史案例与 0.78 相似度 | 案例必须来自真实索引并有来源，模型调用与 usage 可核验 |
| Skill 计算 | `skills/fraud_detection.py` 用字段名是否出现在字符串中匹配规则；相似度函数固定返回 0.65，而命中阈值为 >0.7 | 检测基线应有类型化条件、独立样本与标签评测 |
| 风险分数 | 手工加权截到 100；confidence 使用常数 | 明确为启发式分数，不能称校准概率或真实置信度 |
| 审批/执行 | `disposition_executor.py` 对部分 action 检查 approved 布尔值，再生成 success 和 rollback_token | token 不代表已经回滚；审批必须绑定用户、case、版本和一次具体操作 |
| 审计 | `compliance_audit.py` 生成截断摘要并返回 `immutable=True`，未看到由此形成防重写存储或外部锚定 | 摘要是完整性校验材料，不是数字签名或防篡改证明；不宣称法规认证 |
| 案例复盘 | 固定返回 lessons 和 `knowledge_updated=True` | 必须有实际写入/索引版本和来源，避免虚假成功 |
| 重试 | BaseSkill 捕获异常立即重试，未见文中承诺的指数退避；对返回 success=False 的结果直接结束 | 重试区分可重试异常、业务失败与写操作幂等 |

链接示例：[风险分析源码](https://github.com/1Adalt/finco-pilot/blob/df39fb08ce3d2444568af9d62b5a34bbb5b0f122/agents/risk_analyzer.py)、[连接器](https://github.com/1Adalt/finco-pilot/blob/df39fb08ce3d2444568af9d62b5a34bbb5b0f122/mcp/connectors.py)、[审计模块](https://github.com/1Adalt/finco-pilot/blob/df39fb08ce3d2444568af9d62b5a34bbb5b0f122/skills/compliance_audit.py)。

## 一个可复算的金额问题

三角色版本 `v2-3agent/agents/risk_detective.py` 的 `_aggregate()` 把整个 raw 输入放入每个事件，`_assess()` 随后对每个事件的 transactions 求和。同一笔交易可能重复计算。

本次读取仓库欺诈样例，独立对其 JSON 求和：3 笔交易合计 267,000；4 个事件均携带同一份 raw，按该函数逻辑得到 1,068,000。该结果来自独立算术与静态数据流核查，没有运行整个参考系统。交易涉及金额也不应直接命名为预期损失，除非另有损失定义与估计。

我们的设计必须用稳定 transaction_id 去重；实体名与实体 ID 不混作不同主体；金额、币种、重复与窗口边界有数值测试。

## 采纳与舍弃

采纳：独立职责、类型化输入输出、证据链、人工复核、幂等、失败记录、运行轨迹。

改造：四个业务阶段可以先由确定性工作流管理，调查环节使用一个工具型 Agent；验证器独立执行。多 Agent 后续仅在相同数据与预算下证明增益后引入。

不纳入首版：保险理赔、征信系统、舆情/投诉多源集成、真实冻结/赔付、法规自动裁定。50,000 元等截图阈值仅为作者示例，不作为本项目业务或监管规则。

案例检索可在积累经人工核验的历史 case 后加入；写入应版本化，不能用未经复核的模型结论污染知识库。MCP 和 OpenTelemetry 按接口与观测需求逐步接入，不以技术名称计成果。
