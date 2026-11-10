# Gate 4.1 实际源码复现与差异审查

审核基点为私有仓库草稿 PR #6 的 `8092dae`。以下 GitHub 镜像提交只复制相应本地 Git 对象中的调查代码及测试，保留先后顺序。镜像父提交与本地不同，因此提交号不同。

| 本地版本 | GitHub 镜像 | 对应实现 |
|---|---|---|
| `85c63f4` | [`026c1a2`](https://github.com/wwc76934-cyber/riskcase-agent/commit/026c1a2c2c6926b7e03fd8f1aa9b693904eac22b) | 首轮 Qwen、DeepSeek 候选及测试 |
| `efb47ab` | [`c1d2068`](https://github.com/wwc76934-cyber/riskcase-agent/commit/c1d2068279b1c28ebe9028e934d154812bf53677) | 等价 Schema 实现 |
| `f3fa1c2` | [`d2e1ac2`](https://github.com/wwc76934-cyber/riskcase-agent/commit/d2e1ac26e445315241678a0d50f60e96c198f2bb) | Qwen 终稿复验入口 |
| `603caed` | [`e11cf4c`](https://github.com/wwc76934-cyber/riskcase-agent/commit/e11cf4c2bfe75d4479f09c614586cd5146c92f3a) | 单阶段复验和超时诊断 |
| `c411dfa` | [`df3d31d`](https://github.com/wwc76934-cyber/riskcase-agent/commit/df3d31dc64e00be10c3cfb9f5347e7f5e4c3f91c) | Qwen 完整开发验收 |
| `259621d` | [`b93a07a`](https://github.com/wwc76934-cyber/riskcase-agent/commit/b93a07a8e284c90f78ed112f6d797f38020013e0) | B1、B4 真实兼容性 |
| `65b1f34` | [`3be4abb`](https://github.com/wwc76934-cyber/riskcase-agent/commit/3be4abb5398d59307ab1267463101cf48e88d6a8) | 正式首段冻结控制器 |
| `4d22de7` | [`d9d5463`](https://github.com/wwc76934-cyber/riskcase-agent/commit/d9d5463c301c368e02a3dea59b60fc649636ba2d) | 续跑及后续准备 |

`85c63f4` 中实际的 Gate4Policy、Gate4ProviderClient、开发运行器和测试已在第一个镜像提交中核对。首轮 Qwen 和 DeepSeek 的本机提案具有相同应用源码指纹。首轮结果对应此阶段。后续等价 Schema、终稿复验和完整验收分别在后续镜像提交中保留。当前续跑控制器的版本包含已审核超时槽位的保留与继续规则。

逐文件比对现行调查代码、测试和锁定依赖，以及首轮镜像的六个文件，相关 Git 内容对象一致。工作流在换行归一后逐字一致，沿用现有 Ubuntu、Windows 完整回归及依赖校验。环境入口为 `requirements-ci.txt`，引用 `requirements-dl.txt`、`requirements-investigation.txt`、`requirements-service.txt`。本机本轮核对为 Python 3.12.0、httpx 0.28.1、jsonschema 4.26.0、MCP 1.26.0、pytest 9.1.1、pydantic 2.14.1。

Git 对象验证针对仓库内容。历史运行的应用指纹还包括工作区实际换行。两次正式控制器的原始字节已另外保存为 Base64，并在原应用树中重算并匹配各自冻结指纹。Base64 可通过 Python `base64.b64decode(path.read_bytes())` 还原，核对还原内容的 SHA256。早期候选的原工作区指纹保留在运行提案，GitHub 镜像验证其 Git 源码对象。数据、协议、调度、候选与正式汇总的 SHA256 已整理。原始模型响应、评测内容、授权明细、追踪号和运行账本留在本机。 [指纹清单](evidence/g4/fingerprints.json)

GitHub Actions 的新提交回执另行核验。旧 Gate 3 保持 `WAIVED_BY_USER_FOR_RESEARCH_CONTINUATION`。 [正式计划](G4_FORMAL_EXECUTION_PLAN.md) · [动态规划专项](G4_DYNAMIC_PLANNING_AUDIT.md) · [下一批提案](G4_NEXT_TRANCHE_PROPOSAL.md) · [账本核对](G4_LEDGER_RECONCILIATION.md)

