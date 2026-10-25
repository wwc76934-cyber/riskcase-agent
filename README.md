# 原始Git历史备份

本仓库通过已授权的单仓库GitHub连接上传，没有新申请全账号CLI权限。

- `main`：已验收V1，共327文件，原始提交 `1d0433b3cd2b4ac157a8a0891cf81c9f624b6360`。
- `codex/research-upgrades`：后续开发快照，共354文件，原始提交 `b0eae06c7415017ab1ff3ede34934c2fbf59b08f`。
- 两个远端快照的完整文件树SHA均与对应本地提交一致。

连接导入产生新的远端提交ID；原始提交作者、时间、父子关系和全部历史保存在本分支的Git bundle中。没有重写原本地仓库历史。实验报告引用的原始提交ID可从bundle恢复，不能误以为它们就是本次API导入提交ID。

## 恢复原始历史

下载本分支的 `riskcase-agent-history.bundle`，在文件所在目录执行：

```powershell
git bundle verify .\riskcase-agent-history.bundle
git clone -b main .\riskcase-agent-history.bundle restored-riskcase-agent
cd restored-riskcase-agent
git switch codex/phase01-foundation
```

最后一条切到包含后续升级的原开发分支；只需要V1可停留在main。

bundle大小：1992563字节。SHA-256：

```text
3662fc40ab41c95aed16edf97c9835a96ca5db35e3c03950a1310cb6680ecebf
```

Windows可运行 `Get-FileHash .\riskcase-agent-history.bundle -Algorithm SHA256` 核对。此备份只包含已经审查的Git跟踪文件与历史，不含原始交易库、模型权重、虚拟环境、访问令牌或API密钥。

## 后续Git使用

原本地库origin已指向本GitHub仓库，但没有给原分支设置远端跟踪，因为API导入的提交ID与原始历史不同。不要直接强制推送覆盖远端。若需要普通Git日常开发，可在仓库级凭据配置完成后另行克隆远端到新目录，保留原目录与本备份。

详见 `source-heads.json` 的本地/远端提交映射。
