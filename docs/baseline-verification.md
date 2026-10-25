# 独立基线验收

日期：2026-10-14。原仓库 HEAD：`f860138975228a74b55ad7bc7c9c69f4f025129f`。

- 经审查白名单导入 17 个源码、测试和依赖文件；逐文件 SHA-256 与复制前清单全部一致。
- 新建独立 Git 仓库，分支 `codex/phase01-foundation`；不继承课程 remote。
- 新建 Python 3.12.0 虚拟环境，`include-system-site-packages = false`。
- 按原始 requirements.txt 安装依赖；`pip check` 返回 `No broken requirements found.`。
- 新项目离线测试：`49 passed in 2.26s`。
- 测试设置 `PYTHONDONTWRITEBYTECODE=1`，使用 `python -B -m pytest -q -p no:cacheprovider`。
- API 调用次数：0。模型费用：0 元。

这只证明课程基线在独立环境中可运行，不证明金融分析能力或真实模型效果。

复制与 Git 初始化均未覆盖既有目标目录。首次初始化工作目录误指向父讨论目录，被权限拒绝；随后在新项目子目录正确初始化，未改变课程仓库。

## 第一个教学批次验收

- `python -B -m app.cli.protocol_demo`：第二次请求包含工具消息；`tool success: True`；两次模型调用均由 Fake 提供。
- `python -B -m app.cli.protocol_demo --missing`：FileReadError 回灌；`tool success: False`，Agent 仍为 `completed`，符合教学预期。
- 原有协议相关测试：`3 passed, 25 deselected in 0.45s`。
- 本批只新增展示入口与合成夹具，没有改变课程控制循环。
- 提交前复核：课程仓库仍干净且 HEAD 不变；新仓库 remote 为空。
- Git 身份仅在新仓库本地复用课程已有身份，不修改全局配置。
