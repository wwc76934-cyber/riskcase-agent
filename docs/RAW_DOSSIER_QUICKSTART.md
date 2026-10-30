# 用自己的合成案卷运行调查

这个入口与评测分开：接收 `snapshot` 和 `question`，不接收金标准，也不计算隐藏正确率。默认对小案卷取齐原件，再用模型理解和提出待审阅主张；可显式选择动态取证。

当前仅支持明确标记的合成短文本案卷，不支持直接上传个人金融资料或任意长度PDF。例子在 `examples/raw-dossier.json`；其中金额、编号和文本均为合成材料。

## 免费准备和检查

```powershell
.\scripts\run_project.ps1 -m app.cli.dossier_review prepare --input examples/raw-dossier.json --directory runs/my-dossier
.\scripts\run_project.ps1 -m app.cli.dossier_review status --directory runs/my-dossier
```

`prepare` 固定输入、Skill、代码和策略，创建待执行任务，不取模型凭据、不发送资料、不产生模型费用。每个任务使用新目录。输入只允许顶层 `snapshot`、`question`；`snapshot` 内包含案件范围/截止时间/事件日期、分析师提供的订单金额（分）和带来源角色的原始文档。示例有身份映射、清算、退款操作、退款台账，以及带有效日期的合成规程。

模型必须自己理解正文，角色标签不提供扣款数、净额或结论。范围、时间和版本在宿主过滤。修改正文后需要相应更新可信规程目录的正文绑定哈希；不能把随意添加的规程文本当可执行指令。

## 显式付费执行

下面的命令会把已准备的合成案卷发送到硅基流动，并产生费用。只有在你明确决定执行时才运行；让助手代执行仍需要项目约定的具体批次授权。

```powershell
.\scripts\run_project.ps1 -m app.cli.dossier_review run --directory runs/my-dossier --live --max-estimated-cny 0.50
```

需要已有本地模型凭据；不会从命令行接收或输出密钥。`--live` 与费用上限缺一不可，单任务最多6次请求、12次工具尝试，最多估算1元，并复用项目20元总账。价格与令牌预留是本地估算，不是供应商账单。准备和状态命令不会运行模型。

已结束、失败或阻塞的任务不会因为再次运行命令而自动重试；结果不明的外部请求也不会盲目重发。请求、响应、费用、检查点和结果保存在任务目录。新输入需另建目录、重新授权，不能拿旧授权重复试错。

## 免费审阅已保存结果

```powershell
.\scripts\run_project.ps1 scripts/open_dossier_review.py --source runs/my-dossier --directory runs/my-dossier-review
```

复制结束后的结果到独立工作台，原始执行记录保留。页面会给出令牌文件位置；输入本地令牌后选择任务，核对每项主张旁的原文行、规程及缺失材料，再保存/批准草稿并提交本地模拟队列。没有资金或账户操作。

也可以直接审阅项目已有的真实模型结果，不花新模型费用：

```powershell
.\scripts\run_project.ps1 scripts/open_dossier_review.py --source runs/stage5-live-v4/raw-10/all_context --directory runs/my-saved-model-review
```

界面里的“引用已检查”不意味着语义已被自动证明；四项事实、规程和缺失材料均需要明确人工确认。浏览器自动化验收只证明流程有效，不替代真正的人工审阅。
