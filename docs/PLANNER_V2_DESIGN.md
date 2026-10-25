# 第二版调查计划：原文约束、明确默认值与受限纠正

第一版的主要问题是：明确的简称被反复要求澄清，以及“只查一项”被错误地扩大为其他几项。第二版改进的是需求理解和交互合同；交易真伪仍由检测研究和人工流程判断，不能把范围翻译准确率当成欺诈识别率。

## 一句话如何变成计划

以“正金额占比不查，只比较笔数占比”为例，模型先输出：

```json
{"decision":"execute","reason_code":"supported_scope","selections":[
  {"check":"amount_share","action":"exclude","quote":"正金额占比不查"},
  {"check":"count_share","action":"include","quote":"只比较笔数占比"}
]}
```

程序检查这些引用是否逐字出现在规范化后的问题中，检查项是否受支持、有没有重复、有没有偷偷增加参数，再把 `include` 行编译成原来的检查列表。原文片段让审阅者能看到“系统依据哪句话选了这一项”，并不保证模型一定理解正确。

程序另外检查能够明确识别的“只查”和否定约束。例如如果模型在上题中额外选择频次，系统会拒绝这个计划，给模型一次明确错误码的纠正机会。第一次答案和纠正结果同时保存，不能只报告纠正后的成绩。

## 默认口径与不能替换的要求

五项、窗口、阈值仍保持原研究合同：一小时至少4笔、30天金额新高、30天新商户，以及7天相对30天的笔数/正金额Top1占比，集中度每窗口至少10笔。简称可以采用默认值；用户明确指定其他窗口或阈值时，整请求拒绝，不能偷偷改回默认口径。

第二版提示将这两种情况分开讲清楚，并使用通用示例说明组合、否定、全集减项、含糊问题和不支持要求。示例属于开发材料，独立验收题由另一位作者编写；开发方不得读取验收题逐题调参。

“付款之前是否连刷”只是在描述交易的时间关系，不是要求系统付款。反过来，“查频次并执行付款”包含金融执行要求，整个请求停止。相互矛盾而没有明确改口的范围需要澄清。

## 模型能做什么

- 只收到固定政策、通用开发示例和当前问题；不收到交易事实、case路径、标准答案或欺诈标签。
- 每题最多2次请求：第一次翻译，必要时一次格式或合同纠正；HTTP层不自动重试。
- 不开放任何工具；不能执行SQL、命令、查其他用户或批准金融动作。
- 计划必须经过显式范围确认，再由固定代码查询、计算并生成有引用的报告。

程序的词法检查只是有限范围的约束，不是通用提示注入防御或完整语义证明。权限边界仍是固定案例、固定工具参数、预算和确认状态机。

## 三种第二版模式

`keyword` 是改进后的纯规则入口；`model` 是有原文引用和程序校验的模型入口；`hybrid` 同时得到关键词计划，若两者不同就明确要求澄清，不使用并集扩大调查范围。

`hybrid` 可能少犯错，也可能不必要地停止，所以不能事先宣称它更好。第一版 `v1_keyword`、`v1_model` 与第二版都能用同一冻结题集比较。两版的共享程序检查也有变化，成绩差异不能全部归因于提示或模型能力。

## 免费先运行一题

在仓库根目录运行，输出目录必须不存在：

```powershell
.\scripts\run_project.ps1 -m app.cli.planner_v2 single --text "只查频次，不查金额" --method keyword --directory runs/my-planner-v2-check
```

真实模型需显式指定 `--method model --live`，使用已保存凭据并计入既有累计费用上限。首轮原始响应、纠正状态、原文引用和最终范围保存在运行目录。离线脚本 `ScriptedPlanner` 仅验证接线，不能作为真实模型成绩。

## 冻结比较与独立核验

CLI的 `prepare` 接受作者独立维护的题集，支持顶层 `tasks` 列表，单题包含 `id`、`question.text`、`gold.decision/checks`、`family`，没有split则统一记作acceptance。标准答案仅由host评分，不传给模型。

```powershell
.\scripts\run_project.ps1 -m app.cli.planner_v2 prepare --questions <题集路径> --directory runs/planner-v2-acceptance --methods v1_keyword v1_model keyword model
.\scripts\run_project.ps1 -m app.cli.planner_v2 run --directory runs/planner-v2-acceptance --method keyword --workers 2
.\scripts\run_project.ps1 -m app.cli.planner_v2 run --directory runs/planner-v2-acceptance --method model --live --workers 2
.\scripts\run_project.ps1 -m app.cli.planner_v2 audit --directory runs/planner-v2-acceptance
```

冻结代码、题目、政策和来源指纹后才运行。已有、失败、仅保存开始记录而中断的执行单元均不自动重跑。核验将缺失/失败留在总题数中，复算首次与最终范围是否完全正确、多查/漏查、纠正次数、请求数及费用。

自动评测为了区分语义和数字问题，会模拟接受所有合法执行计划，然后用固定代码取证；即使计划语义错了也不拿标准答案偷偷修正。每份数字报告另由源SQL核验，明确标记 `SIMULATED_CONFIRMATION_NOT_HUMAN_REVIEW`。这不证明真人会同意计划，也不证明实际业务节约了时间。

## 接入接口

`app.risk.planner_v2.route(client, NaturalQuestion, method=...)` 的主要字段兼容第一版。新增 `native_plan`、`response_records`、`guard_constraints`、`contract_repair_attempted` 和混合模式的冲突记录。

`proposal(question, route, case_key=..., runtime=...)` 绑定问题、案例、代码、第二版政策、中文范围、固定查询，以及逐项默认值和已识别排除条件。原来的确定性取证不需要模型继续参与。

回归测试覆盖原文引用、重复或非法项、额外SQL/参数、否定与只查、原子拒绝、相邻检查的局部窗口、保存第一次错误、最大两次请求、工具调用拒绝、混合冲突、冻结篡改、缺失单元和源SQL报告核验。实际模型指标由冻结验收报告单独记录；本设计说明不预先承诺指标提升。
