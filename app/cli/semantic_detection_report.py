"""Export measured research summaries; never invent unfinished experiment results."""
import argparse
import json
from pathlib import Path

from app.risk.semantic_detection.adapter import checked_run
from app.risk.semantic_detection.common import digest, write_json


def export(e1_path, e3_path, audit_path, bridge_path, output, e3_audit_path, history_audit_path):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    e1 = checked_run(e1_path)
    if e1.get('encoding_contract') != 'additive-v2':
        raise ValueError('Replacement pilot is not formal PRD E1 evidence')
    e3 = json.loads(Path(e3_path).read_text(encoding='utf-8'))
    audit = json.loads(Path(audit_path).read_text(encoding='utf-8'))
    bridge = json.loads(Path(bridge_path).read_text(encoding='utf-8'))
    e3_audit = json.loads(Path(e3_audit_path).read_text(encoding='utf-8'))
    history = json.loads(Path(history_audit_path).read_text(encoding='utf-8'))
    if e3['status'] != 'E3_bounded_complete' or e3.get('encoding_contract') != 'additive-v2' or audit['status'] != 'independent_E1_audit_passed' or bridge['status'] != 'challenger_catalog_verified':
        raise ValueError('Complete experiment, audit and bridge receipts required')
    if (e3_audit['status'] != 'independent_E3_audit_passed' or history['status'] != 'independent_history_audit_passed'
            or audit['run_report_sha256'] != digest(Path(e1_path) / 'report.json')
            or e3_audit['report_sha256'] != digest(e3_path) or history['e3_report_sha256'] != digest(e3_path)):
        raise ValueError('Independent audits must bind the same experiment reports')
    metadata = json.loads((Path(bridge_path).parent / 'model-metadata.json').read_text(encoding='utf-8'))
    if metadata['report_sha256'] != digest(Path(e1_path) / 'report.json') or metadata['release_id'] != bridge['release_id']:
        raise ValueError('Bridge belongs to another detector release')
    for name, expected in e3['artifacts'].items():
        path = (Path(e3_path).parent / name).resolve()
        if not path.is_relative_to(Path(e3_path).parent.resolve()) or digest(path) != expected:
            raise ValueError('E3 material changed: ' + name)
    summary = {'status': 'measured_research_delivery', 'scope': 'IBM synthetic, 2018 observed development',
               'final_2019_read': False, 'paid_calls': 0, 'default_changed': False,
               'e1': {name: {'seed_tp': [s['metrics']['tp_daily10'] for s in value['seeds']],
                              'seed_ap': [s['metrics']['ap'] for s in value['seeds']],
                              'ensemble': value['ensemble'],
                              'parameters_total': value['seeds'][0]['parameters_total'],
                              'parameters_trainable': value['seeds'][0]['parameters_trainable'],
                              'training_seconds': sum(s['train_seconds'] for s in value['seeds']),
                              'prediction_seconds': [s['prediction_seconds'] for s in value['seeds']],
                              'peak_process_bytes': max(s['whole_process_peak_working_set_bytes'] for s in value['seeds'])}
                      for name, value in e1['results'].items()},
               'historical_controls': e1['historical_controls'], 'e1_comparisons': e1['comparisons'],
               'semantic_phase2_admitted': e1['semantic_phase2_admitted'],
               'e3_masks': e3['masks'],
               'e3': {fraction: {'conditions': {name: {'seed_tp': [r['metrics']['tp_daily10'] for r in value['seeds']],
                                                      'ensemble': value['ensemble']}
                                              for name, value in group['conditions'].items()},
                                 'comparisons': group['comparisons']} for fraction, group in e3['results'].items()},
               'audit': audit, 'bridge': {'release_id': bridge['release_id'], 'cases': len(bridge['cases']),
                                         'generic_service_compatible': bridge['generic_service_compatible'],
                                         'specialized_fraud_contract_compatible': bridge['specialized_fraud_contract_compatible']},
               'e3_independent_audit': e3_audit, 'history': history,
               'sources_sha256': {'E1': digest(Path(e1_path) / 'report.json'), 'E3': digest(e3_path),
                                  'audit': digest(audit_path), 'bridge': digest(bridge_path),
                                  'E3_audit': digest(e3_audit_path), 'history_audit': digest(history_audit_path)},
               'limits': e1['limits'] + e3['limits']}
    write_json(output / 'results.json', summary)
    lines = ['# LLM语义增强与标签稀缺研究：实测交付', '',
             '所有下列检测指标来自2018已观察开发数据；没有新独立测试、2019调参、付费API或默认检测器替换。', '',
             '## E1：同数值输入、同训练期、5轮、三seed', '',
             '| 条件 | 三seed捕获 | 集成TP / 3650 | AP | 可训练 / 总参数 |', '|---|---|---:|---:|---:|']
    for name, value in summary['e1'].items():
        lines.append(f'| {name} | {value["seed_tp"]} | {value["ensemble"]["tp_daily10"]} | {value["ensemble"]["ap"]:.6f} | {value["parameters_trainable"]} / {value["parameters_total"]} |')
    for name, value in summary['historical_controls'].items():
        lines.append(f'| {name}（旧无渠道参照） | 原冻结三seed或树 | {value["metrics"]["tp_daily10"]} | {value["metrics"]["ap"]:.6f} | 见原研究 |')
    lines += ['', '所有条件保留相同数值与MCC/渠道分支；B1附加普通联合嵌入，B2附加冻结Qwen32维，B3对附加向量增加投影，B3_shuffled打乱类别对应并匹配B3容量。',
              'B0/B1不只有表示形式差异，参数量也不同；不能把其差异归给LLM。旧无渠道参照输入不同，本轮B1/B2及B3/shuffled承担直接对照。', '',
              '| 比较 | 新增正例 | 挤出正例 | 净变化 | 配对日重采样95%区间 |', '|---|---:|---:|---:|---|']
    for name, value in e1['comparisons'].items():
        interval = '[' + ', '.join(f'{x:.2f}' for x in value['paired_day_bootstrap_95']) + ']'
        lines.append(f'| {name} | {value["added_positive"]} | {value["removed_positive"]} | {value["net"]} | {interval} |')
    lines += ['', f'冻结语义后续组合准入：{e1["semantic_phase2_admitted"]}。区间仅为开发期逐项描述，不是多重比较调整后的独立显著性证明。', '',
              '## E3：完整训练期内隐藏标签，短预算研究', '',
              '4个标签预算 × 5条件 × 3seed，每次固定2轮。不同预算不共用隐藏标签作监督或类别权重。', '',
              '| 标签预算 | 实际负/正标签 | BCE | Focal | CB-Focal | 一致性 | 语义+一致性 |', '|---|---|---:|---:|---:|---:|---:|']
    for fraction, value in summary['e3'].items():
        mask = e3['masks'][fraction]
        counts = [value['conditions'][name]['ensemble']['tp_daily10'] for name in ['bce', 'focal', 'cb_focal', 'consistency', 'semantic_consistency']]
        lines.append(f'| {float(fraction):.0%} | {mask["negative"]} / {mask["positive"]} | ' + ' | '.join(map(str, counts)) + ' |')
    lines += ['', '上表是日top10集成捕获数；完整AP、月份/渠道、种子及区间见results.json。100%标签下无无标签样本，一致性项为0。',
              '短预算结果不能排除更长训练或其他超参数的收益，也不能用于宣称未来泛化。未按该结果偷偷改轮数、lambda或mask。', '',
              '## E2：独立轻量历史对照', '',
              ('E1未显示语义收益，因此不启动语义+图组合；另用相同2轮、B1类别信息及全部训练标签，独立检验历史关系。'
               if not e1['semantic_phase2_admitted'] else 'E1达到本轮语义准入；此处E2仍是独立历史对照，组合模型须另行实验。'), '',
              '| 方法 | 三seed捕获 | 集成捕获 | AP |', '|---|---|---:|---:|',
              f'| 匹配普通B1（E3全标签BCE） | 见E3明细 | {history["matched_flat"]["tp_daily10"]} | {history["matched_flat"]["ap"]:.6f} |']
    for mode, value in history['results'].items():
        lines.append(f'| {mode} | {value["seed_tp"]} | {value["metrics"]["tp_daily10"]} | {value["metrics"]["ap"]:.6f} |')
    lines += ['', '| 比较 | 新增正例 | 挤出正例 | 净变化 | 配对日重采样95%区间 |', '|---|---:|---:|---:|---|']
    for name, value in history['comparisons'].items():
        interval = '[' + ', '.join(f'{x:.2f}' for x in value['paired_day_bootstrap_95']) + ']'
        lines.append(f'| {name} | {value["added_positive"]} | {value["removed_positive"]} | {value["net"]} | {interval} |')
    lines += ['', f'训练边逐条核验{history["training_edges_checked"]:,}条，另用SQLite重建96个目标的前K笔身份及顺序。',
              '单跳、单头的历史注意力不是完整TGAT；mean为最近K笔池化，不是顺序RNN。增加分支也增加参数，不能把全部差异单独归因于边结构。配对变化、成本与资源见JSON。', '',
              '## 核验与接入', '',
              f'独立SQLite窗口排名、手工分数阈值AP及三个模型重载抽验均通过；可选{len(bridge["cases"])}案例来自无标签全量重算后队列。',
              '通用调查页面使用关键词计划、脚本模拟确认及源SQL核验，报告停在待审阅；不是真人或真实LLM效果实验。',
              '特化欺诈MCP合同仍只接受旧检测器，新候选明确保留独立身份。未改共享合同；特化接入需要独立集成PR。', '',
              '## 成本与限制', '',
              'CPU本地编码约1.2GB公开权重；已校验官方SHA。训练/推理时间、参数量与整进程峰值内存均保留。E1/E3有并发运行，时间不是隔离性能基准。',
              '官方MCC参考中5912冲突、5921缺失，均回退unknown；2026词典与预训练知识晚于交易日期，不声称当时线上可用。',
              '首次截断下载、首次MCP依赖收集失败及后续修复回执均保留。默认检测器与既有冻结研究保持独立。']
    (output / 'RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['e1', 'e3', 'audit', 'bridge', 'output', 'e3-audit', 'history-audit']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    export(args.e1, args.e3, args.audit, args.bridge, args.output, args.e3_audit, args.history_audit)


if __name__ == '__main__':
    main()
