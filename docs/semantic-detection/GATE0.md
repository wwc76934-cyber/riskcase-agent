# Gate 0：可行性与隔离审计

日期：2026-11-01。依据用户PRD开展零付费本地研究。默认检测器及调查Agent保持原样。

## 来源与分支

PRD给出的252fe35c8099910006122d2f603835396d8a380c在本机对象库不存在，不能声称从它创建。本研究从实际当前0332db5创建独立worktree `semantic-detector-v2`、分支 `codex/llm-semantic-detector`。原工作目录未提交调查文件不复制、不修改。共享源码及历史产物只读。

新增所有权：app/risk/semantic_detection、app/cli/semantic_detection_*.py、config/semantic-detection、tests/semantic_detection、docs/semantic-detection、requirements-semantic-detection.txt。大型缓存、模型、来源PDF与运行回执放新worktree的忽略目录runs。无共享接口变更。

## 字段准入

- 数值输入严格复用MODEL_COLUMNS中的12个数值，训练专用有符号log、中位数/IQR及缺失指示，输出24维。不使用标识符、标签、Agent记忆/报告或事后错误字段。
- MCC：训练期109值，按四位数字格式验证；仅映射官方词典明确标题。码值不解释为连续数值，也不从商户ID推断真实名称。
- Channel：训练期Chip Transaction 617040笔、Online Transaction 100845笔、Swipe Transaction 124007笔。只使用其明确渠道含义，未知值回退unknown。训练期两字段无缺失。
- 两字段来自既有交易特征，但没有完整入库合同，不能声称历史线上授权前可得性已证实。

## 类别释义

Visa官方Merchant Data Standards Manual，实际下载为October 2026版（不能沿用搜索缓存的2025日期），SHA256 `62839c2c559ccb59914241315765860015ac6ffc446274b3b03d57399638fa5a`。来源：https://by.visa.com/content/dam/VCOM/download/merchants/visa-merchant-data-standards-manual.pdf 。原始PDF保留本地，派生表记录每项PDF页码。

检查发现PDF第75页同时将Drug Stores and Pharmacies与Package Stores标为5912，图像与文本一致。提取器拒绝冲突码；5912及该来源缺失的5921一并回退unknown，不自行将后者猜成酒类。其余107种训练MCC有明确标题。来源标题不代表合成商户真的属于对应品牌实体。

释义和预训练模型均晚于交易日期，仅作现代知识支持的回顾性研究，不声称2016年已拥有这些知识。原始论文同样不替本项目证明字段语义与时间可得性。

## 编码器与资源预算

Qwen/Qwen3-Embedding-0.6B，官方模型卡Apache-2.0，固定revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`。官方来源：https://huggingface.co/Qwen/Qwen3-Embedding-0.6B 。tokenizer与权重逐文件哈希进入缓存。CPU float32、最多4线程、batch8、max_length128，最后有效token池化；先全向量L2，再截前32维并L2，使用模型声明的可变维度能力。低维选择是资源取舍，不证明1024维无增益。

仅训练期唯一(MCC,channel)文本与显式unknown参与编码，不传交易、标签或用户ID；重复文本只编码一次。权重约1.2GB磁盘、float32参数约2.4GB内存，另需激活和训练数据内存。无GPU租用、无付费API、无交易上传。

## 论文适配结论

已阅读10页并视觉核查架构、损失和实验设置。论文使用类均衡Focal、Dropout视图的对称KL、时序注意力与门控残差；实验6:4划分，测试节点无标签参与优化。本项目禁止这种训练/开发混合：2016–2017训练，2018只开发评分，2019完全不选取记录。2018历史已观察，不是独立新留出。

初版先做E1。轻量邻域和损失机制可以专项测试，但不得拿单元测试当图模型或半监督实际效果。E4须有E1–E3积极证据才启动。

## 已冻结E1协议

config/semantic-detection/e1.json在任何E1指标产生前写入：5个条件、3seed、每个5轮，固定末轮，三seed均值为主；不选最好seed或最好轮次。B0分别训练MCC/渠道嵌入；B1同信息联合类别嵌入；B2冻结Qwen32维；B3加32维投影；B3_shuffled打乱类别—向量对应但匹配B3参数。

B1/B2直接检验预训练表示，B3/shuffled补充检验语义对应；不同条件冻结/可训练参数差异明示。不能由“额外输入和容量”直接归因语义。历史冻结无渠道残差与强树是另外两项参照，不伪称与本轮完全匹配输入。

图/语义组合准入预先规定：B2或B3相对B1的配对日重采样差异下界>0；B3还需超过shuffled。该门槛是开发取舍，不是经过多重比较校正的独立显著性证明。

## 验收

Gate0通过，可进入E1。任何无增益、模型下载/训练失败都保留记录。未知释义未填造；初始下载遇沙箱网络及TLS链问题后改用正常系统证书下载，未禁用TLS。共享Agent当前特化fraud合同只接受neural_v2_ensemble；新候选必须单独验证接入，不能冒用旧检测器身份。

## 设计复核：正式协议改为附加特征

实现复核发现首轮将普通类别表示替换为语义向量，不完全满足PRD“作为附加特征”的要求；且5912/5921同为unknown时会损失原码值区别。该首轮只作为replacement先导实验，结果与原日志保留，不能充当正式E1。基于它的半监督运行中止，已有结果与design-review-stop.json保留。

正式协议分别为 `e1-additive-v2.json` 和 `e3-additive-v2.json`，在正式指标前冻结。所有条件保留相同12数值及MCC/渠道可训练分支；B1再附加32维普通联合类别嵌入，B2附加32维冻结Qwen向量，B3附加投影后的向量，shuffled附加对应打乱的向量。原类别码信息不会因未知语义而丢失。

新测试明确要求语义未知时原类别分支仍可区分类别并收到梯度；先导模型保留兼容加载，但禁止导出为正式challenger。后续正式报告只引用additive-v2运行，保留该设计错误与修复，不改写首轮结果。
