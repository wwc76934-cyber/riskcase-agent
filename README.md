# 原始Git历史备份与上传说明

本项目通过已授权的单仓库GitHub连接上传，没有新增全账号CLI权限。本地原始仓库历史未被重写。

## 三个分支

- main：已验收V1文件快照，另补Linux并发兼容修复。
- codex/research-upgrades：后续开发快照，仍保留开发中/未验证的说明。
- codex/history-backup：本页、原始Git历史、增量修复历史及提交映射。

导入时，V1的327个文件与原始提交 1d0433b3cd2b4ac157a8a0891cf81c9f624b6360 的文件树完全一致；开发版354个文件与 b0eae06c7415017ab1ff3ede34934c2fbf59b08f 一致。

首次远程CI发现Windows通过而Linux目录重命名并发冲突报ENOTEMPTY，已补窄范围处理和4项回归。本机相关37项通过。修复后的开发文件树与原本地提交 2d4e7e0f2fbb1cd4009fedb81b01b8760fe5c100 完全一致；V1只加入对应兼容修复与测试，金融计算/模型结果没有改变。远程CI状态以Actions页面为准，不把历史本机通过当远程通过。

连接导入生成新的远端提交ID。原始提交的作者、时间、父子关系和对象保存在下面的bundle中；实验报告中的原始提交ID可据此恢复，而不是假定它们等于API导入提交ID。完整对应关系见source-heads.json。

## 恢复V1原始历史

下载本分支的riskcase-agent-history.bundle，在文件所在目录执行：

~~~powershell
git bundle verify .\riskcase-agent-history.bundle
git clone -b main .\riskcase-agent-history.bundle restored-riskcase-agent
cd restored-riskcase-agent
~~~

原始完整bundle：1992563字节，SHA-256：3662fc40ab41c95aed16edf97c9835a96ca5db35e3c03950a1310cb6680ecebf。

## 恢复后续开发与本次兼容修复

同时下载riskcase-agent-ci-fix.bundle，放在恢复目录的上一级。该增量包依赖原始bundle中的b0eae06提交，不能单独克隆。随后执行：

~~~powershell
git switch codex/phase01-foundation
git fetch ..\riskcase-agent-ci-fix.bundle HEAD
git merge --ff-only FETCH_HEAD
~~~

增量bundle：1780字节，SHA-256：bdffccccba2bf904d3144a466a62800c45323b21c7045de63c4da752d637a12a。

Windows可用Get-FileHash文件名 -Algorithm SHA256核对。两份bundle只包含已审查的Git跟踪文件与历史，不含原始交易库、模型权重、虚拟环境、访问令牌或API密钥。

## 后续普通Git开发

原本地库origin已指向本GitHub仓库，但没有设置原分支的远端跟踪，因为API导入提交ID与原历史不同。不要直接强制推送覆盖远端。需要普通Git工作流时，在仓库级凭据配置后另行克隆远端到新目录，保留原目录和本备份。
