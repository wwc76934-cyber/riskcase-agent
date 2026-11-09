"""Render aggregate Track A reports from completed independent development receipts."""
import argparse
import json
from pathlib import Path

from app.risk.behavior_semantic.study import sha


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def score_table(receipt):
    rows = ['| 模型 | 开发 AP | Top 1% TP/K | Top 1% Precision | Top 1% Recall | F1 | 排除近似序列 AP |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, family in receipt['ensembles'].items():
        m = family['development']
        sensitivity = family['strata']['without_detected_near_train']['AP']
        rows.append(f"| {name} | {m['AP']:.4f} | {m['topK_TP']}/{m['topK']} | {m['topK_precision']:.4f} | "
                    f"{m['topK_recall']:.4f} | {m['F1']:.4f} | {sensitivity:.4f} |")
    return '\n'.join(rows)


def model_table(receipt):
    rows = ['| 模型 / seed | AP | 训练秒 | 重载＋推理秒 | 参数或树节点 | 重放最大误差 |',
            '| --- | ---: | ---: | ---: | --- | ---: |']
    for name, value in receipt['models'].items():
        seconds = value['training_wall_seconds']
        timing = f'{seconds:.1f}' if seconds is not None else '未记录'
        if value['training_timing_scope'].startswith('continuation'):
            timing += '（续跑片段）'
        sizes = ', '.join(f'{k}={v}' for k,v in value['parameter_size'].items() if v is not None)
        rows.append(f"| {name} | {value['development']['AP']:.4f} | {timing} | "
                    f"{value['reload_and_inference_wall_seconds']:.2f} | {sizes} | {value['score_replay_max_abs_error']:.2g} |")
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser()
    for name in ('a2','a3','analysis','bank','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    a2,a3,analysis,bank = [read(getattr(args,name)) for name in ('a2','a3','analysis','bank')]
    if (a2['instances'] != 21 or a3['instances'] != 14 or a2['test_predictions_generated'] or a3['test_predictions_generated']
            or analysis['final_test_predictions_generated'] or analysis['A2_source_sha256'] != a2['development_results_sha256']
            or analysis['A3_source_sha256'] != a3['development_results_sha256']):
        raise ValueError('Independent development receipts do not match the analysis')
    comparisons = ['| 对照差异 | ΔAP | 配对序列簇 95% 区间 | 证据范围 |','| --- | ---: | ---: | --- |']
    for name,value in analysis['comparisons'].items():
        lo,hi = value['paired_exact_sequence_cluster_95']
        scope = '单 seed 诊断' if value.get('single_seed_diagnostic_not_stability_proof') else '三 seed 集成'
        comparisons.append(f"| {name} | {value['AP_difference']:+.4f} | [{lo:+.4f}, {hi:+.4f}] | {scope} |")
    rare = ['| 模型 | 频次分组 | 样本 | 正例 | AP |','| --- | --- | ---: | ---: | ---: |']
    for name in ('qwen','random','tfidf_svd','gru'):
        f = (a2 if name == 'gru' else a3)['ensembles'][name]
        for key in ('unseen','rare_1_5','rare_6_20'):
            m = f['strata'][key]
            ap = f"{m['AP']:.4f}" if 'AP' in m else '未定义'
            rare.append(f"| {name} | {key} | {m['rows']} | {m['positives']} | {ap} |")
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'A2_BASELINES.md').write_text('\n\n'.join([
        '# Track A A2 基线开发报告', '21 个模型已独立重载并重放开发分数。Precision 和 Recall 使用静态前 1% 审核队列，F1 使用开发拟合阈值。',
        score_table(a2), model_table(a2), '数值 LR 和森林使用 Amount 与四个发布 RFM。TF-IDF、GRU 和 Transformer 只使用行为，组合 LR 使用两类输入。序列神经模型使用末尾 256 槽。']),encoding='utf8')
    (args.output/'A3_SEMANTICS.md').write_text('\n\n'.join([
        '# Track A A3 语义对照开发报告', score_table(a3), model_table(a3), '\n'.join(comparisons), '\n'.join(rare),
        f"冻结 Qwen 离线编码耗时 {bank['wall_seconds']:.1f} 秒，CPU {bank['CPU_seconds']:.1f} 秒，编码器参数 {bank['encoder_parameters']:,}。事件向量在推理阶段已预计算。",
        '分组频次以训练末尾 256 个事件窗口为准，组间可以重叠。三种子为同一开发集上的训练稳定性，未增加独立正例。原始分数分布、完整指纹和阈值见独立重放收据。']),encoding='utf8')
    (args.output/'A4_REVIEW_PACKET.md').write_text('\n\n'.join([
        '# Track A A4 证据与最终评分检查', f"开发规则产生的待复核类别为 `{analysis['A4_status']}`。",
        '根据同输入 GRU、随机冻结表和同文本 TF-IDF/SVD 判断预训练贡献。数值加 TF-IDF 用来判断更强、但输入不同的研究对照。时间与顺序消融分别从间隔移除和可见后缀打乱得到，均为单种子诊断。',
        '人工复核项目包括曲线收敛、排除高相似序列后的差值、三个相同种子的差值及低频组正例数。按复核后的 A4 结果冻结模型范围和全部来源指纹，再提交最终评分许可检查。',
        '最终测试保持封存。仅 18 个测试正例，一次捕获相当于 5.56 个百分点召回。报告只能解释匿名发布快照上的静态分类。',
        '证据 SHA256', *[f'- `{getattr(args,name).name}` `{sha(getattr(args,name))}`' for name in ('a2','a3','analysis','bank')]]),encoding='utf8')


if __name__ == '__main__':
    main()
