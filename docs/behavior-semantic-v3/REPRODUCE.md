# 安全审计复现

本轮只运行源文件下载、解包、静态聚合与门控测试，训练次数/付费API/数据上传均为0。原始数据及工具仅在本机runs，不提交Git。首次网络权限失败、加密tar失败及零字节文件保留；后续成功另存02/03。

从工作区父目录用本机既有Python运行scripts/behavior_v3/download_official_sources.py；它绑定作者76bf718提交，只向官方来源下载到behavior-semantic-fraud/runs/behavior-v3-source-01。已存在文件时拒绝覆盖。工具下载脚本get_portable_tools.py逐一核验官方release SHA，只展开便携组件，不执行安装。

解密成员只允许显式名称输出到新文件，先核对目录、成员101085533字节和SHA；不递归解压路径。动态pickle加载禁止。

在独立子进程运行（输入指向已核对的解密成员，输出必须是新目录）：

```powershell
python -I -S -B scripts/behavior_v3/static_pickle_audit.py --input runs/behavior-v3-source-01/quarantine-decrypted-02.pkl --output runs/new-static-audit
python -B -m pytest -q -p no:cacheprovider tests/behavior_semantic
```

符号解析器仅支持本次文件需要的opcode，未知即停止；不调用pickle携带的GLOBAL、REDUCE、NEWOBJ，不实例化pandas/numpy对象。-I -S是Python导入隔离，不是操作系统网络隔离，不能拿它允许动态反序列化。输出只有聚合统计，不能输出原始行为或身份映射到远端。

尚无训练命令或“复现成绩”；许可未明确、用户与绝对时间字段缺失、特征时点未审定，训练门关闭。基线协议为待正式冻结方案，不能把方案写成已实施实验。
