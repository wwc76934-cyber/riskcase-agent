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

Git 对象验证针对仓库内容。历史运行的应用指纹还包括工作区实际换行。两次正式控制器的原始字节已另外保存为 Base64，并在原应用树中重算并匹配各自冻结指纹。Base64 可通过 Python `base64.b64decode(path.read_bytes())` 还原，核对还原内容的 SHA256。首轮候选原工作区的 242 个 Python 文件已按原换行重建，聚合指纹与两份候选提案完全一致。正式首段的 246 个 Python 文件也已完成同样校验。替换为首次续跑控制器的原始字节后，聚合指纹与续跑提案一致。数据、协议、调度、候选与正式汇总的 SHA256 已整理。原始模型响应、评测内容、授权明细、追踪号和运行账本留在本机。 [指纹清单](evidence/g4/fingerprints.json)

GitHub Actions 的新提交回执另行核验。旧 Gate 3 保持 `WAIVED_BY_USER_FOR_RESEARCH_CONTINUATION`。 [正式计划](G4_FORMAL_EXECUTION_PLAN.md) · [动态规划专项](G4_DYNAMIC_PLANNING_AUDIT.md) · [下一批提案](G4_NEXT_TRANCHE_PROPOSAL.md) · [账本核对](G4_LEDGER_RECONCILIATION.md)

## 全树复核与重建

从远端草稿分支获取 Git 对象后，对 85c63f4 的 242 个文件核对到 222 个 Git 对象完全一致、20 个仅有 CRLF 差异。现行 4d22de7 对应的 246 个文件中，227 个 Git 对象完全一致、19 个仅有 CRLF 差异。两处比较均没有其他内容差异。

在包含镜像历史的 Git 工作区运行以下命令，可不联网核对规范 Git 内容、逐文件原始 SHA256 和聚合冻结指纹。追加 `--output` 指向新目录可以重建字节副本。

```powershell
python scripts/verify_gate4_smoke_source.py docs/evidence/g4/smoke-source-85c63f4.json
python scripts/verify_gate4_smoke_source.py docs/evidence/g4/formal-source-65b1f34.json
```

[候选源码清单](evidence/g4/smoke-source-85c63f4.json) · [正式首段源码清单](evidence/g4/formal-source-65b1f34.json)
## 双平台完整回归

源码同步提交 2a10ea3c3af82ea4230d9a83742b914f8f640ff3 的 Ubuntu、Windows 均通过 1,270 项全量测试，各自另通过 50 项 V3 专项。两平台均有一条警告。Windows 全量测试耗时 944.56 秒，Ubuntu 为 310.79 秒。 [GitHub Actions 回执](https://github.com/wwc76934-cyber/riskcase-agent/actions/runs/42789372088)
