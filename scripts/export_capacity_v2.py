"""Publish checked synthetic-development capacity evidence as small reviewable artifacts."""

import argparse
import json
import os
from pathlib import Path

from app.risk.capacity_v2 import load_plans
from app.risk.history import sha256_file
from app.risk.source_merchant_pack import write_json


ZH = {"neural": "纯深度学习", "rule_only": "纯规则", "reserve_20pct": "规则保留20%", "rule_first": "规则优先"}
EN = {"neural": "Neural ensemble", "rule_only": "Rule only", "reserve_20pct": "20% reserved", "rule_first": "Rule first"}
COLORS = {"neural": "#225b95", "rule_only": "#bc5353", "reserve_20pct": "#309b86", "rule_first": "#a87b29"}


def verified_evidence(root, directory):
    """Recheck existing verification bindings; never choose a list or open outcomes here."""
    root, directory = Path(root), Path(directory)
    protocol, manifest = load_plans(root, directory)
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    proof = json.loads((directory / "verification.json").read_text(encoding="utf-8"))
    required = {"status": "passed", "baseline_kind": "neural_v2_ensemble", "source_rows_checked": protocol["expected_rows"],
                "ranking_lists_checked": 12, "actual_review_database_unchanged": True, "actual_candidate_version": 2,
                "human_approved": False, "deployed": False, "final_test_evaluated": False}
    if any(proof.get(key) != value for key, value in required.items()):
        raise ValueError("checked_neural_capacity_evidence_required")
    for filename, key in (("report.json", "report_sha256"), ("protocol.json", "protocol_sha256"), ("plan-manifest.json", "plan_manifest_sha256")):
        if sha256_file(directory / filename) != proof[key]:
            raise ValueError("capacity_export_evidence_binding_differs")
    if sha256_file(root / "scripts/verify_capacity_v2.py") != proof["script_sha256"]:
        raise ValueError("capacity_export_verifier_changed")
    package = json.loads((directory / "research-package.json").read_text(encoding="utf-8"))
    if (package["status"] != "pending_review_unregistered_material" or package["actual_review_database_modified"]
            or package["verification_sha256"] != sha256_file(directory / "verification.json")
            or package["report_sha256"] != proof["report_sha256"]):
        raise ValueError("capacity_export_research_package_binding_differs")
    return protocol, manifest, report, proof


def comparison_table(report):
    lines = ["| 每日上限 | 方案 | 实际复核数 | 捕获欺诈标签数 | 误报数 | 召回率 | 新增捕获 | 挤出损失 | 净变动 | 空位 |",
             "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for budget in (5, 10, 20):
        for policy in ZH:
            m = report["metrics"][f"{budget}/{policy}"]
            lines.append(f"| {budget} | {ZH[policy]} | {m['reviews']:,} | {m['true_positives']} | {m['false_positives']:,} | {m['recall']:.2%} | {m['gained_tp']} | {m['lost_tp']} | {m['net_tp']:+d} | {m['unused_capacity']:,} |")
    return "\n".join(lines)


def plot_comparison(root, report, destination):
    cache = Path(root) / "tmp/mpl-capacity-v2"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache.resolve()))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "#f6f8fb", "axes.facecolor": "white"})
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5), gridspec_kw={"width_ratios": [1.25, 1]})
    x, width = np.arange(3), .19
    for offset, policy in enumerate(ZH):
        values = [report["metrics"][f"{b}/{policy}"]["true_positives"] for b in (5, 10, 20)]
        bars = axes[0].bar(x + (offset-1.5)*width, values, width, label=EN[policy], color=COLORS[policy])
        axes[0].bar_label(bars, fontsize=9, padding=3)
    axes[0].set_xticks(x, ["5 / day", "10 / day (primary)", "20 / day"])
    axes[0].set_ylabel("Fraud-labelled transactions captured")
    axes[0].set_ylim(0, max(m["true_positives"] for m in report["metrics"].values()) * 1.23)
    axes[0].set_title("Same daily capacity upper bound")
    axes[0].legend(ncol=2, fontsize=9, frameon=False, loc="upper left")
    policies = ("reserve_20pct", "rule_first", "rule_only")
    y = np.arange(len(policies))
    gains = [report["metrics"][f"10/{p}"]["gained_tp"] for p in policies]
    losses = [-report["metrics"][f"10/{p}"]["lost_tp"] for p in policies]
    axes[1].barh(y, gains, color="#309b86", label="Additional capture")
    axes[1].barh(y, losses, color="#bc5353", label="Displaced capture")
    for position, (policy, gain, loss) in enumerate(zip(policies, gains, losses)):
        axes[1].text(gain+2, position, f"+{gain}", va="center", color="#237665")
        axes[1].text(loss-2, position, str(loss), va="center", ha="right", color="#a03f3f")
    axes[1].set_yticks(y, [EN[p] + f"  (net {report['metrics']['10/'+p]['net_tp']:+d})" for p in policies])
    axes[1].invert_yaxis()
    axes[1].axvline(0, color="#6e7785", linewidth=.8)
    axes[1].set_xlim(min(losses)*1.22, max(gains)*2.0)
    axes[1].set_xlabel("Fraud-labelled transactions vs neural ensemble")
    axes[1].set_title("Primary budget: 10 reviews / day")
    axes[1].legend(loc="upper left", fontsize=9, frameon=False)
    for ax in axes:
        ax.grid(axis="y" if ax is axes[0] else "x", alpha=.15)
        ax.set_axisbelow(True)
    fig.suptitle("Neural ranking + unchanged candidate rule", fontsize=16, x=.06, ha="left", weight="bold")
    base = report["metrics"]["10/neural"]
    fig.text(.06, .02, f"2018 synthetic development data | {base['rows']:,} transactions, {base['positives']:,} fraud labels | No rule tuning or approval | Rule-only leaves unused seats", fontsize=9, color="#515d6d")
    fig.tight_layout(rect=(.025, .07, .99, .91), w_pad=2)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=170, bbox_inches="tight")
    plt.close(fig)


def export(root, directory):
    root, directory = Path(root), Path(directory)
    protocol, manifest, report, proof = verified_evidence(root, directory)
    destination = root / "docs/assets/capacity-v2-comparison.png"
    plot_comparison(root, report, destination)
    hashes = {name: sha256_file(directory / name) for name in (
        "protocol.json", "plan-manifest.json", "labels-opened.json", "report.json", "verification.json", "research-package.json")}
    summary = {"status": "verified_development_research", "baseline_kind": "neural_v2_ensemble", "split": "validation",
               "period": protocol["period"], "data_kind": protocol["data_kind"], "rows": protocol["expected_rows"],
               "positives": protocol["expected_positives"], "budgets": protocol["budgets"], "primary_budget": 10,
               "metrics": report["metrics"], "primary_monthly": report["primary_monthly"], "verification": proof,
               "candidate": {key: protocol["candidate"][key] for key in ("id", "version", "status", "package_hash", "database_sha256")},
               "research_review_status": "pending_review_unregistered_material", "actual_review_database_modified": False,
               "human_approved": False, "deployed": False, "final_test_evaluated": False,
               "source_artifact_sha256": hashes, "chart_sha256": sha256_file(destination),
               "export_script_sha256": sha256_file(Path(__file__)), "limits": protocol["limits"]}
    write_json(root / "docs/capacity-v2-results.json", summary)
    table = comparison_table(report)
    primary = report["metrics"]["10/reserve_20pct"]
    base = report["metrics"]["10/neural"]
    rule_only = report["metrics"]["10/rule_only"]
    rule_first = report["metrics"]["10/rule_first"]
    monthly = ["| 月份 | 纯深度学习捕获 | 保留20%捕获 | 新增捕获 | 挤出损失 | 净变动 |",
               "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for month, m in report["primary_monthly"]["reserve_20pct"].items():
        baseline = report["primary_monthly"]["neural"][month]["true_positives"]
        monthly.append(f"| {month} | {baseline} | {m['true_positives']} | {m['gained_tp']} | {m['lost_tp']} | {m['net_tp']:+d} |")
    monthly_table = "\n".join(monthly)
    body = f"""# 深度学习预警接回规则研究：同容量复核实验

本实验已完成12份无标签名单的冻结、2018开发标签核算和独立SQL复核。主要结果：预先约定每天最多复核10笔时，纯深度学习捕获{base['true_positives']}笔欺诈标签；给旧候选规则保留20%名额捕获{primary['true_positives']}笔，新增{primary['gained_tp']}笔、挤出{primary['lost_tp']}笔，净变动{primary['net_tp']:+d}笔。规则优先捕获{rule_first['true_positives']}笔，纯规则捕获{rule_only['true_positives']}笔。证据支持继续保留深度学习排序作为研究基线；当前证据不支持批准旧规则替换它。

## 这项工作解决什么问题

模型分数只能回答“哪些交易靠前”。调查Agent提出一条候选规则后，还要回答：“人工每天只能看这么多笔，规则找来的案件，会不会挤掉更值得看的案件？”这里把升级后的三随机种子深度学习集成接回原来的候选规则研究流程。规则与分数先生成名单，名单固定后才核算标签，最后保留可供人审阅的研究包。

标签阳性只代表该合成数据的欺诈标记。本文“捕获”指标签对照后的计数，不代表实际查证、资金追回或减少损失。

![同容量比较](assets/capacity-v2-comparison.png)

## 数据、模型和冻结口径

- 数据：IBM TabFormer合成信用卡交易，2018年开发集，共422,088笔、683笔欺诈标签、365个交易日。该年份已经用于模型选择，不能当作新的独立测试集。
- 排序基线：`neural_v2_ensemble`，来自`runs/neural-v2-development-01`，随机种子42、43、44分数逐元素平均。先检查三份分数与平均结果一致，再按原训练加载器的`transaction_id`排序对齐，逐行绑定422,088个ID。
- 候选：`case-467716-rule`的实际第2版，状态仍为待审阅。本实验只读该候选及其数据库。规则阈值与四个条件保持原样：金额大于32990个最小单位、完整30日历史、有30日历史、商户在30日历史中未出现。数据未证实具体币种，不能把阈值写成人民币。
- 预先固定每日上限5、10、20；主要口径为10。每日离线结束后在当天完整交易中排名，属于回溯式容量实验，不能描述成实时拦截。
- 名单库使用`neural_score`。旧分配器只在内存适配时沿用参数名`hgb_score`，其中装的是深度学习分数，所有持久化结果与报告均明确深度学习基线。

四个方案的实际行为：

1. **纯深度学习**：每天按集成分数由高到低取前K笔。
2. **纯规则**：只从规则命中交易中按金额取前K笔；不足K笔就空缺。因此比较的是相同容量上限，实际复核数可能更少。
3. **规则保留20%**：先从规则命中交易中按深度学习分数取`floor(K/5)`笔，再按深度学习分数补齐剩余名额，去重。
4. **规则优先**：先取规则命中交易；命中组和未命中组内均按深度学习分数排序。

所有方案的并列分数按冻结的交易ID哈希和ID处理，保证重跑名单一致。实际金融候选没有被登记新版本、批准或部署。

## 全部容量结果

新增捕获、挤出损失、净变动均相对相同K的纯深度学习名单。净变动=新增捕获−挤出损失；计数对象是交易。

{table}

主要口径的具体含义：保留20%方案把{primary['added_reviews']}笔原来没选中的交易换入，也把{primary['displaced_reviews']}笔原先选中的交易挤出。换入交易中有{primary['gained_tp']}笔带欺诈标签，换出交易中有{primary['lost_tp']}笔带欺诈标签，所以全年净变动为{primary['net_tp']}。仅看“新发现5笔”会漏掉6笔挤出损失。

每天5笔时保留20%比纯深度学习多捕获1笔；每天20笔时少6笔。这些是预先固定容量下的开发集敏感性结果，不能挑出“+1”的口径作为可靠提升或上线依据。本文没有估计真实业务收益，也没有作统计显著性承诺。

## 主要口径按月检查

{monthly_table}

月度拆分用于观察全年汇总是否掩盖得失，不据此重新挑选月份、阈值或分配比例。683笔阳性标签中，纯深度学习在每日10笔时仍漏掉561笔，复核名单中3528笔是阴性；当前系统的误报与漏报都必须明确展示。

## 验证证据

先固定协议、候选状态、代码和输入哈希；再写入无标签总体与12份名单，记录`labels_read=false`；随后只查询2018的`validation`标签并生成报告。独立校验器没有调用分配器：它用SQL窗口函数重新生成12份名单，比对ID、日期、排名；再逐行核对原始特征、规则判定、三种子平均分数与并列键，并独立计算整体和48项月度结果。

- 核对总体行数：{proof['source_rows_checked']:,}。
- 核对名单：{proof['ranking_lists_checked']}份，名单条目合计{proof['planned_review_entries']:,}。
- 月度核对：{proof['monthly_checks']}项。
- 实际候选数据库前后SHA-256一致，候选保持第2版待审阅。
- 本模块没有查询2019交易事实或标签，没有任何真实LLM请求。
- 研究包状态：`pending_review_unregistered_material`，只提供审阅材料。

这套SHA核验用于发现文件变化与保证研究来源一致，不能被描述成防恶意篡改存储或合规认证。

机器可读摘要：[capacity-v2-results.json](capacity-v2-results.json)。原始研究目录：`runs/capacity-neural-v2`（本地运行产物，不随Git发布大型数据）。

关键文件SHA-256：

| 文件 | SHA-256 |
| --- | --- |
"""
    for name, digest in hashes.items():
        body += f"| `{name}` | `{digest}` |\n"
    body += r"""
## 自己验证

已有本地模型与数据时，检查现有研究结果：

```powershell
.\scripts\run_project.ps1 -m app.cli.capacity_v2 verify --directory runs/capacity-neural-v2
.\scripts\run_project.ps1 -m scripts.export_capacity_v2 --directory runs/capacity-neural-v2
.\scripts\run_project.ps1 -m pytest -q -p no:cacheprovider tests/test_capacity_v2.py tests/test_export_capacity_v2.py
```

从头重做请使用一个不存在的新目录，并保持输入模型、实际候选与代码不变：

```powershell
.\scripts\run_project.ps1 -m app.cli.capacity_v2 all --directory runs/capacity-neural-v2-repeat
```

`all`依次冻结、生成名单、打开2018开发标签、SQL核验。已有目录与重复开封会拒绝，避免静默覆盖证据。如果输入哈希、候选状态或冻结代码改变，现有实验也会拒绝继续；应保留原实验并记录新的研究版本，不能把修改后的内容伪装成原先冻结结果。

小夹具测试包括：计划阶段禁止访问标签库、分数来源改变时拒绝、实际候选数据库改变时拒绝、名单改变时禁止开标签、重新封装哈希后错误排名仍被SQL识别、报告数字改变时核验失败。测试中的2019哨兵数据由我们自行构造，仅用于证明筛选隔离。

## 能诚实展示的能力与剩余限制

这项工作可以用于解释：深度学习输出怎样变成可重复的复核队列；Agent提出的规则怎样经过确定性计算、容量对比与人工审阅；为什么“新找到几笔”不等于“整体更好”。不采用一条未证明有效的候选规则，也是完整研究流程的有效产出。

结果只属于合成数据的反复使用开发集。数据的实际到账时刻、标签确认时刻、币种与真实机构分布没有被证实，不能声称线上时效性或跨机构泛化。没有真实审核员试用、业务损失评估或线上部署证据。当前研究包没有进入实际审批状态，保留此边界。
"""
    (root / "docs/CAPACITY_V2_REPORT.md").write_text(body, encoding="utf-8")
    example = "# 升级模型后的候选规则审阅摘要\n\n" + body.split("## 这项工作解决什么问题")[0].split("\n\n", 1)[1]
    example += "\n" + table + "\n\n证据与复现方法：[专项报告](../docs/CAPACITY_V2_REPORT.md)。本材料仅对应2018合成开发集；实际候选第2版保持待审阅，无批准或部署。\n"
    (root / "examples/capacity-v2-comparison.md").write_text(example, encoding="utf-8")
    return {"status": "exported", "baseline_kind": "neural_v2_ensemble", "primary_neural_tp": report["metrics"]["10/neural"]["true_positives"],
            "primary_reserved_net_tp": primary["net_tp"], "source_report_sha256": hashes["report.json"]}


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=root / "runs/capacity-neural-v2")
    args = parser.parse_args()
    print(json.dumps(export(root, args.directory), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
