# Gate 4 模型能力矩阵

2026-11-10。用项目本机凭据调用硅基流动只读模型目录一次，返回 HTTP 200，生成请求为零。目录只提供可见性证据。严格 Schema、工具调用和业务表现按候选独立实测。普通推理费率以下均为人民币每百万文本 Token。

| 顺序 | Model ID | 可见性与官方证据 | 输入 / 输出 | 风险与下一步 |
|---|---|---|---:|---|
| 1 | `Qwen/Qwen3.8-27B` | 项目凭据目录可见。官方使用指南说明支持工具调用及 256K 上下文。 | 3 / 12 | 优先实测严格 Schema 与不可得后改查。 |
| 2 | `Pro/zai-org/GLM-5.1` | 官方工具调用示例使用该 ID。项目凭据此次目录未返回该 ID。 | 待账户核实 | 跳过此 ID，待目录和报价可复核后重审。 |
| 3 | `deepseek-ai/DeepSeek-V4-Flash` | 项目凭据目录可见。官方模型中心列出工具调用与 1024K 上下文。 | 3 / 9（全天保守上界） | 同案同合同验证严格 Schema 和实际改查。独立批次。 |
| 备选 | `doubao-seed-2-1-lite-260915` | 方舟普通 API 模型表列出工具与结构化输出，账户可用性待核实。 | 0.8 / 2.7 | 单独核对标准在线推理权限及合同兼容性。 |
| 备选 | `doubao-seed-2-1-pro-260915` | 方舟官方结构化输出和工具示例均使用该快照，账户可用性待核实。 | 6 / 30 | 单独核对标准在线推理权限及合同兼容性。 |

[硅基流动模型目录接口](https://docs.siliconflow.cn/docs/api/models-get) · [硅基流动定价](https://www.siliconflow.cn/pricing) · [Qwen 工具与上下文说明](https://docs.siliconflow.cn/docs/usercases/use-siliconcloud-in-yan) · [硅基流动 Function Calling](https://docs.siliconflow.cn/docs/userguide/guides/function-calling) · [硅基流动 Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post)

[火山方舟模型表](https://docs.volcengine.com/docs/ark/model-list?lang=zh) · [方舟标准定价](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh) · [方舟严格输出示例](https://docs.volcengine.com/docs/ark/structured-output-beta?lang=en) · [方舟 Function Calling](https://docs.volcengine.com/docs/ark/function-calling?lang=en)

方舟 Coding Plan 使用其编程工具入口。此研究的方舟候选使用普通 Chat API 按量计费。 [Coding Plan 配置说明](https://docs.volcengine.com/docs/ark/coding-plan-personal-ai-zcode?lang=en)
