"""Export a readable research comparison from an independently verified run."""

import argparse
import json
from pathlib import Path

from app.risk.history import sha256_file


def export(directory,destination):
    report_path=directory / "report.json"
    proof=json.loads((directory / "verification.json").read_text())
    protocol=json.loads((directory / "protocol.json").read_text(encoding="utf-8"))
    if proof["status"]!="passed" or proof["report_sha256"]!=sha256_file(report_path) or proof["protocol_sha256"]!=sha256_file(directory / "protocol.json"):
        raise ValueError("Current independent verification required before export")
    plan_path=directory / "plan-manifest.json"
    plan=json.loads(plan_path.read_text())
    if proof["plan_manifest_sha256"]!=sha256_file(plan_path) or sha256_file(directory / "ranking-inputs.sqlite")!=plan["population_sha256"] or sha256_file(directory / "planned-reviews.sqlite")!=plan["plans_sha256"]:
        raise ValueError("Frozen plan material changed before export")
    report=json.loads(report_path.read_text(encoding="utf-8"))
    labels={"hgb":"树模型排序","rule_only":"规则单独筛选","reserve_20pct":"规则保留20%名额","rule_first":"规则优先"}
    lines=["# 同审核预算下的规则比较","","IBM TabFormer 合成数据；2018 年回顾性开发实验，数据此前已被使用。不是独立保留验证、真实业务收益或线上处置。",
        "","## 主口径：每天最多10笔","","| 方案 | 审核量 | 捕获 | 正常审核 | 新增捕获 | 挤出损失 | 净捕获变化 | 空置名额 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for policy in protocol["policies"]:
        m=report["metrics"][f"10/{policy}"]
        lines.append(f"| {labels[policy]} | {m['reviews']} | {m['true_positives']} | {m['false_positives']} | {m['gained_tp']} | {m['lost_tp']} | {m['net_tp']:+d} | {m['unused_capacity']} |")
    lines.extend(["","新增捕获与挤出损失都相对同档预算的树模型名单计算。规则单独筛选不补非命中记录，可能空置名额；它受到同一容量上限约束，不保证实际审核量相同。",
        "","## 三档预算完整结果","","| 每日上限 | 方案 | 审核量 | 捕获 | 正常审核 | 查准率 | 召回率 | 净捕获变化 |","|---:|---|---:|---:|---:|---:|---:|---:|"])
    for budget in protocol["budgets"]:
        for policy in protocol["policies"]:
            m=report["metrics"][f"{budget}/{policy}"]
            precision=f"{m['precision']:.2%}" if m["precision"] is not None else "未定义"
            recall=f"{m['recall']:.2%}" if m["recall"] is not None else "未定义"
            lines.append(f"| {budget} | {labels[policy]} | {m['reviews']} | {m['true_positives']} | {m['false_positives']} | {precision} | {recall} | {m['net_tp']:+d} |")
    lines.extend(["","## 每日10笔的月度净变化","","| 月份 | 树模型捕获 | 规则单独筛选净变化 | 保留20%净变化 | 规则优先净变化 |","|---|---:|---:|---:|---:|"])
    monthly=report["primary_monthly"]
    for month in sorted(monthly["hgb"]):
        lines.append(f"| {month} | {monthly['hgb'][month]['true_positives']} | {monthly['rule_only'][month]['net_tp']:+d} | {monthly['reserve_20pct'][month]['net_tp']:+d} | {monthly['rule_first'][month]['net_tp']:+d} |")
    lines.extend(["","月份和半年的分解只是描述，不能当作未见样本验证。政策、阈值和预算均在这轮读取标签前冻结；没有根据本轮结果调整到最好。",
        "","规则只提供候选；没有自动批准、部署、冻结账户或交易。未知真实审核成本、损失和标签可得时点，不能换算利润。2019 年最终检测测试仍未评估。",
        "",f"协议 SHA-256：`{proof['protocol_sha256']}`。",f"报告 SHA-256：`{proof['report_sha256']}`。",
        f"独立核验：{proof['source_rows_checked']} 条源内记录、{proof['ranking_lists_checked']} 份排序名单、{proof['monthly_checks']} 项月度统计。","本材料为冻结运行的派生汇总，不替代当前人工审阅状态。"])
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return {"destination":str(destination),"report_sha256":proof["report_sha256"]}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,required=True);parser.add_argument("--destination",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(export(args.directory.resolve(),args.destination.resolve()),indent=2))
