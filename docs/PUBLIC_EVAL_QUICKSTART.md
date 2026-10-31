# 从干净检出复现两个公开评测

Python3.12，在仓库根目录创建虚拟环境，安装依赖。以下数据下载和本地训练/检索不调用大模型API，也不需要模型密钥。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dl.txt -r requirements-investigation.txt
```

新环境用`.\.venv\Scripts\python`替代下面的`python`；项目现有Windows环境也可使用`scripts/run_project.ps1`。原始文件进入Git忽略的`data/raw`；已存在的文件必须匹配固定大小和SHA-256，否则停止，不自动覆盖。

## 匿名真实来源检测

先读[下载前协议](EXTERNAL_DETECTION_PROTOCOL.md)，尤其是上游PCA未知范围、两天时间跨度和不能构造真实案件的限制。

```powershell
python -m app.cli.public_data creditcard --download
python -m app.cli.external_detection develop --output runs/my-external-detection
# 开发段选择和所有权重/预处理/代码指纹已冻结，再执行保留段
python -m app.cli.external_detection holdout --output runs/my-external-detection
```

`frozen-development.json`保存开发结果、候选选择、参数和产物哈希；`holdout.json`保存三个模型家族及全部神经种子结果。保留段命令拒绝覆盖已有评分，拒绝输入/模型/代码变化；重复训练必须新目录。公开证据中的耗时属于原机器，重新运行不会逐字相等，不应当作同一性能样本。

本轮原始ARFF约150.8MB，训练只使用前24小时144786笔。没有将数据源的完整规模解释为本模型训练量。

## 通用长期记忆检索

先读[固定协议](LONGMEMEVAL_RETRIEVAL_PROTOCOL.md)。本命令使用全部500题、最近会话与BM25对照，**不运行生成模型或付费judge**。

```powershell
python -m app.cli.public_data longmemeval_s --download
python -m app.cli.longmemeval_retrieval --output runs/my-longmemeval
```

原文件约277.4MB。结果保存在`summary.json`，逐题评分在本地`per-question.json`，失败保留`failure.json`。Runtime字段白名单从构造边界剥离答案信息；重复会话按实例时间过滤后再按逻辑ID去重。不用oracle文件评检索，不将会话召回写成问题回答正确率。

## 工程检查与产品运行

```powershell
python -m pytest tests/test_external_detection.py tests/test_longmemeval_retrieval.py tests/test_fraud_memory.py tests/test_fraud_bridge.py
```

公开数据评测与欺诈工作台的源数据合同分开。要运行同交易告警→调查→审阅记忆，使用[主线指南](FRAUD_WORKBENCH_QUICKSTART.md)，先准备IBM源演示包；不要把匿名ULB行替换成IBM用户，或把LongMemEval对话写进案件记忆。
