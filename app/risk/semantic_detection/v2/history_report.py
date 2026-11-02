"""Progress snapshots and evidence-bound reporting for the isolated history study."""
from datetime import datetime, timezone
import json
from pathlib import Path

from app.risk.semantic_detection.common import digest, write_json


def progress(history_run, output):
    history_run, output = Path(history_run), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summaries = {}
    hashes = {}
    for path in sorted(history_run.glob('*/summary.json')):
        summaries[path.parent.name] = json.loads(path.read_text(encoding='utf8'))
        hashes[path.parent.name] = digest(path)
    complete = list(history_run.glob('*/seed-*/result.json'))
    timestamp = datetime.now(timezone.utc).isoformat()
    rows = ['| 条件 | 已完成seed的TP | 集成TP | AP | Online名额 |', '|---|---|---:|---:|---:|']
    for name, record in summaries.items():
        metrics = record['ensemble']
        seeds = ', '.join(str(r['metrics']['tp_daily10']) for r in record['seeds'])
        rows.append(f'| {name} | {seeds} | {metrics["tp_daily10"]} | {metrics["ap"]:.6f} | {metrics["channel"]["Online Transaction"]["reviewed"]} |')
    text = f'''# Gate B进行中快照

生成时间UTC：{timestamp}。截至快照已完成{len(complete)}/38个注册训练实例；只列已经完成整组训练的条件。未完成架构、最终强统计挑战和完整独立重算均不预先填分。

这不是Gate B最终结论。原冻结模型TP122；所有新主条件固定12轮、seed42/43/44、主K6。mean-k12/24仅单seed敏感性。详见`GATE_B_PROTOCOL.md`与`GATE_B_ADDENDUM.md`。

'''+'\n'.join(rows)+'''

`*-self`是完全相同参数量、只重复当前表示的控制，不读取真实邻居。`statistics`是冻结14项历史统计的神经网络；它并不涵盖Mean读取的全部信息。另已保留14统计强树TP127，并登记最终220维广覆盖统计网络三seed及强树挑战，排在主训练结束后顺序执行。

已经观察的Mean215、time-decay196是开发期中间结果，不能据此忽略其余注册条件、宣布架构胜出或发布升级。2018已反复开发使用，配对日区间也不能把它变回独立测试。

新增图边严格早于目标、按用户卡隔离；原点特征仍保留已有用户级历史统计。这是回顾性的完整监督研究，源标签没有可核实available_at；不据此证明真实线上标签时点或半监督可用性。

后续在全部注册模型、事后强挑战、全量预测重载和来源核验完成后生成`HISTORY_RESULTS.md`。默认检测器仍为`neural_v2_ensemble`，调查Agent合同不变。
'''
    (output/'HISTORY_PROGRESS.md').write_text(text, encoding='utf8')
    result = {'status': 'Gate_B_in_progress_snapshot', 'generated_at_UTC': timestamp,
              'completed_registered_instances': len(complete), 'planned_registered_instances': 38,
              'completed_condition_summaries': summaries, 'summary_sha256': hashes,
              'main_training_protocol_sha256': digest(history_run/'protocol.json'),
              'default_detector_changed': False, 'model_efficacy_conclusion_final': False}
    write_json(output/'history-progress.json', result)
    return result


def complete(source_root, history_run, broad_run, history_audit, broad_audit, tree_run, output, tests_xml, *, resource_run, primary_resources, case_run):
    """Render only after registered fitting and every independent replay have passed."""
    import os
    import shutil
    import xml.etree.ElementTree as ET
    import numpy as np
    import pandas as pd
    from sklearn.metrics import average_precision_score
    from app.risk.semantic_detection.evaluation import Ranking
    from .audit_history import block_interval
    from .history_data import load_with_cards

    history_run, broad_run, output = map(Path, (history_run, broad_run, output))
    history_audit, broad_audit, tree_run = map(Path, (history_audit, broad_audit, tree_run))
    read = lambda p: json.loads(Path(p).read_text(encoding='utf8'))
    primary, broad = read(history_run/'report.json'), read(broad_run/'report.json')
    audit, baudit, tree = read(history_audit/'report.json'), read(broad_audit/'report.json'), read(tree_run/'report.json')
    if read(broad_run/'protocol.json')['main_history_protocol_sha256'] != digest(history_run/'protocol.json') or read(tree_run/'protocol.json')['history_protocol_sha256'] != digest(history_run/'protocol.json'):
        raise ValueError('Posthoc controls belong to a different frozen primary protocol')
    if primary['status'] != 'Gate_B_complete' or audit['status'] != 'Gate_B_independently_verified' or baudit['status'] != 'posthoc_broad_controls_independently_verified':
        raise ValueError('Full training and both independent audits required')
    if audit['full_model_replays'] != 38 or baudit['full_model_replays'] != 5:
        raise ValueError('All 43 newly trained model instances must be independently replayed')
    if audit['report_sha256'] != digest(history_run/'report.json') or baudit['source_report_sha256'] != digest(broad_run/'report.json') or baudit['primary_audit_sha256'] != digest(history_audit/'report.json'):
        raise ValueError('Independent audits refer to different experiment bytes')
    cases = read(Path(case_run)/'report.json')
    if cases['model_score_sha256']['mean'] != digest(history_run/'mean/ensemble.npy'):
        raise ValueError('Case gallery belongs to different Mean scores')
    for baseline,value in cases['comparisons'].items():
        verified = audit['comparisons']['mean_vs_'+baseline]
        if (value['added_positive'],value['displaced_positive'],value['net']) != (verified['added_positive'],verified['removed_positive'],verified['net']):
            raise ValueError('Case aggregation differs from complete independent queue audit')
    xml = ET.parse(tests_xml).getroot()
    suites = [xml] if xml.tag == 'testsuite' else list(xml.iter('testsuite'))
    tests = {key: sum(int(s.attrib.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
    if tests['failures'] or tests['errors'] or not tests['tests']:
        raise ValueError('Passing regression report required')
    (_, _, _), (VX, vy, vmeta) = load_with_cards(source_root)[0]
    output.mkdir(parents=True, exist_ok=True)
    rows, channels, curves, long_intervals = [], [], [], []
    scores = {}

    def add(name, metrics, records=None, family='registered', train_seconds=None, predict_seconds=None, parameters=None, peak=None):
        records = records or []
        durations, unusual = [], []
        for record in records:
            values = [float(c['epoch_seconds']) for c in record['curves'] if 'epoch_seconds' in c]
            if values:
                median = float(np.median(values)); durations.extend(values)
                for c in record['curves']:
                    if c['epoch_seconds'] > 10*median:
                        unusual.append({'condition': name, 'seed': record['config']['seed'], 'epoch': c['epoch'],
                            'epoch_seconds': c['epoch_seconds'], 'within_seed_median_epoch_seconds': median,
                            'meaning': 'Observed long wall-clock interval; not assigned to active compute or a specific OS cause'})
        long_intervals.extend(unusual)
        row = {'condition': name, 'family': family, 'seeds': len(records),
               'seed_TP': ','.join(str(r['metrics']['tp_daily10']) for r in records),
               'TP': metrics['tp_daily10'], 'Precision_at_daily10': metrics['precision'],
               'Recall_at_daily10': metrics['recall'], 'AP': metrics['ap'], 'FP': metrics['false_positives'],
               'parameters_each': records[0]['parameters'] if records else parameters,
               'training_seconds_sum': sum(r.get('training_seconds', r.get('train_seconds', 0)) for r in records) if records else train_seconds,
               'prediction_seconds_sum': sum(r.get('prediction_seconds', r.get('inference_seconds', 0)) for r in records) if records else predict_seconds,
               'whole_process_peak_bytes': max((r.get('whole_process_peak_bytes', r.get('peak_process_bytes', 0)) for r in records), default=peak),
               'Online_slots': metrics['channel']['Online Transaction']['reviewed'],
               'median_observed_epoch_seconds': float(np.median(durations)) if durations else None,
               'long_wall_intervals_over_10x_seed_median': len(unusual)}
        rows.append(row)
        for channel, values in metrics['channel'].items():
            mask = VX.channel.to_numpy() == channel
            ap = float(average_precision_score(vy[mask], scores[name][mask])) if vy[mask].sum() else None
            channels.append({'condition': name, 'channel': channel, 'within_channel_AP': ap,
                'reviewed': values['reviewed'], 'TP': values['tp'], 'FP': values['reviewed']-values['tp'],
                'precision': values['precision'], 'recall': values['recall']})
        for record in records:
            for c in record['curves']:
                curves.append({'condition': name, 'seed': record.get('seed', record.get('config', {}).get('seed')), **c})

    for name, record in primary['references'].items():
        if name == 'frozen_residual': path = Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy'
        elif name == 'strong_tree': path = Path(source_root)/'runs/hgb-raw-nochannel-v2-development-01/development-scores.npy'
        else: continue
        scores[name] = np.load(path)
        add(name, record['metrics'], family='frozen_reference')
    for name, record in primary['results'].items():
        scores[name] = np.load(history_run/name/'ensemble.npy')
        add(name, record['ensemble'], record['seeds'], family='single_seed_sensitivity' if name.startswith('mean-k') else 'registered')
    scores['posthoc_tree14'] = np.load(tree_run/'development-scores.npy')
    add('posthoc_tree14', tree['metrics'], family='posthoc', train_seconds=tree['train_seconds'], predict_seconds=tree['inference_seconds'], peak=tree['peak_process_bytes'])
    scores['posthoc_broad_neural'] = np.load(broad_run/'neural-ensemble.npy')
    add('posthoc_broad_neural', broad['results']['neural_ensemble'], [broad['results'][f'neural-seed-{s}'] for s in (42, 43, 44)], family='posthoc')
    scores['posthoc_broad_tree'] = np.load(broad_run/'tree-development-scores.npy')
    add('posthoc_broad_tree', broad['results']['tree']['metrics'], family='posthoc', train_seconds=broad['results']['tree']['train_seconds'], predict_seconds=broad['results']['tree']['inference_seconds'])
    frame, channel_frame = pd.DataFrame(rows), pd.DataFrame(channels)
    frame.to_csv(output/'history-models.csv', index=False)
    channel_frame.to_csv(output/'history-channels.csv', index=False)
    pd.DataFrame(curves).to_csv(output/'history-training-curves.csv', index=False)
    gate_rows = []
    for name, condition in audit['conditions'].items():
        for record in condition['seed_replays']:
            for label, values in record['gate_probe'].items():
                gate_rows.append({'condition': name, 'seed': record['seed'], 'evaluation_label_group': label, **values})
    pd.DataFrame(gate_rows).to_csv(output/'history-activation-probes.csv', index=False)
    for source, name in [(history_run/'report.json', 'history-registered-results.json'), (history_audit/'report.json', 'history-independent-audit.json'),
                         (broad_run/'report.json', 'history-posthoc-broad-results.json'), (broad_audit/'report.json', 'history-posthoc-independent-audit.json'),
                         (tree_run/'report.json', 'history-posthoc-tree14-results.json')]:
        shutil.copyfile(source, output/name)
    modes = ('mean', 'decay', 'gru', 'transformer', 'attention')
    table = ['| 条件 | seed TP | 集成TP | Precision@10 | Recall@10 | AP | Online名额 |', '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        table.append(f'| {r["condition"]} | {r["seed_TP"] or "—"} | {r["TP"]} | {r["Precision_at_daily10"]:.3%} | {r["Recall_at_daily10"]:.3%} | {r["AP"]:.6f} | {r["Online_slots"]} |')
    comparison_rows = ['| 历史模型 | 对照 | 新增正例 | 挤出正例 | 净变化 | 配对日95% | 7日区块95% |', '|---|---|---:|---:|---:|---|---|']
    for name, value in audit['comparisons'].items():
        mode, baseline = name.split('_vs_', 1)
        comparison_rows.append(f'| {mode} | {baseline} | {value["added_positive"]} | {value["removed_positive"]} | {value["net"]:+d} | {value["paired_day_bootstrap_95"]} | {value["block_sensitivity"]["paired_circular_block_95"]} |')
    for name, value in baudit['comparisons'].items():
        mode, baseline = name.split('_vs_', 1)
        comparison_rows.append(f'| {mode} | posthoc {baseline} | {value["added_positive"]} | {value["removed_positive"]} | {value["net"]:+d} | {value["paired_day_bootstrap_95"]} | {value["block_sensitivity"]["paired_circular_block_95"]} |')
    paired = Ranking(vmeta, vy)
    architectures_vs_mean = {}
    for mode in modes[1:]:
        value = paired.compare(scores['mean'], scores[mode])
        value['block_sensitivity'] = block_interval(list(value['daily_net'].values()))
        value['matched_seed_TP_differences'] = [b['metrics']['tp_daily10']-a['metrics']['tp_daily10']
            for a,b in zip(primary['results']['mean']['seeds'],primary['results'][mode]['seeds'])]
        architectures_vs_mean[mode] = value
        comparison_rows.append(f'| {mode} | mean | {value["added_positive"]} | {value["removed_positive"]} | {value["net"]:+d} | {value["paired_day_bootstrap_95"]} | {value["block_sensitivity"]["paired_circular_block_95"]} |')
    mean = next(r for r in rows if r['condition'] == 'mean')
    flat = next(r for r in rows if r['condition'] == 'flat')
    mself = next(r for r in rows if r['condition'] == 'mean-self')
    gate_table = ['| 条件 | 评测标签分组 | 上下文门均值 | 上下文/当前范数 | Attention有效邻居数 | 最近一笔权重 |', '|---|---|---:|---:|---:|---:|']
    for mode in modes:
        for label in ('0', '1'):
            group = [r for r in gate_rows if r['condition'] == mode and r['evaluation_label_group'] == label]
            if not group: continue
            def mean_field(name):
                return f'{np.mean([r[name] for r in group]):.4f}' if all(name in r for r in group) else '—'
            gate_table.append(f'| {mode} | {label} | {mean_field("context_gate_fraction")} | {mean_field("context_to_target_norm")} | {mean_field("attention_effective_neighbors")} | {mean_field("attention_newest_weight")} |')
    cost = ['| 条件 | 每模型参数 | 全seed训练秒 | 全seed推理秒 | 整进程峰值GiB |', '|---|---:|---:|---:|---:|']
    fmt = lambda x: '—' if x is None else f'{x:.2f}'
    for r in rows:
        peak = r['whole_process_peak_bytes']
        marker = '†' if r['long_wall_intervals_over_10x_seed_median'] else ''
        cost.append(f'| {r["condition"]} | {r["parameters_each"] or "—"} | {fmt(r["training_seconds_sum"])}{marker} | {fmt(r["prediction_seconds_sum"])} | {fmt(peak/1024**3 if peak else None)} |')
    resources = {}
    for name in ('broad-statistics', 'audit-history', 'audit-broad'):
        path = Path(resource_run)/(name+'.resources.json')
        receipt = read(path)
        if receipt['status'] != 'completed': raise ValueError('Resource-observed stage did not complete')
        resources[name] = receipt
    if Path(primary_resources).is_file():
        receipt = read(primary_resources)
        if receipt['status'] != 'completed': raise ValueError('Observed primary CLI failed')
        resources['primary_process'] = {'status': 'observed_CLI_completed', 'measurement': 'CPU within explicit primary CLI wrapper',
            'exact_total_process_CPU_seconds': receipt['process_CPU_seconds_all_threads'],
            'largest_observed_peak_bytes': receipt['peak_whole_process_bytes'], 'original_receipt': receipt}
    else:
        resources['primary_process'] = read(Path(primary_resources)/'report.json')
        if resources['primary_process']['status'] != 'observed_process_exited' or resources['primary_process']['final_observation']['exit_code'] != 0:
            raise ValueError('Original primary process did not finish successfully')
    resource_table = ['| 步骤/进程 | 实际进程CPU秒（各线程合计） | 整进程峰值GiB |', '|---|---:|---:|']
    resource_table.append(f'| 38模型主进程（含数据/图） | {resources["primary_process"]["exact_total_process_CPU_seconds"]:.2f} | {resources["primary_process"]["largest_observed_peak_bytes"]/1024**3:.2f} |')
    for name in ('broad-statistics', 'audit-history', 'audit-broad'):
        receipt = resources[name]
        resource_table.append(f'| {name} | {receipt["process_CPU_seconds_all_threads"]:.2f} | {receipt["peak_whole_process_bytes"]/1024**3:.2f} |')
    text = f'''# Gate B：完整监督历史行为研究

全部38个注册实例、5个明确事后登记的强统计挑战已经训练并逐模型重载。Mean在本开发数据捕获{mean['TP']}，强平面{flat['TP']}，相同参数当前表示控制{mself['TP']}，原冻结开发版122。所有架构与负结果一并列出；不把中间最优值包装成最终独立验证。

## 数据、预算与身份

训练2016–2017：841,892笔、967正例；2018已观察开发：422,088笔、683正例、365天、3650名额。2019不参与本轮选型或评测。原冻结参考是只在2016–2017训练的开发版三seed残差集成，不能用已在2018再训练的发布权重来评2018。

新模型均从头训练；raw/no-channel残差核心加普通可训练MCC32，无LLM、无半监督。主K6事先固定，各模型12轮、seed42/43/44、4096批、BCE正例权重20、AdamW、固定余弦；不依开发期早停。最后一轮更新开始LR约6.6185e-5，结束后调度到5e-5。等轮数/更新数不等于等FLOPs，成本单列。

## 主成绩与全部控制

'''+'\n'.join(table)+'''

![业务捕获与seed](history-top10.png)

单seed K12/24仅与K6的seed42作敏感性阅读，不能与三seed集成混为等预算比较，也没有拿它们倒选主K。原E2的Mean66、Attention51是不同输入、结构、邻域身份与两轮预算下的旧研究值，本轮不把差距单归因于训练轮数。

## 新增捕获与挤出损失

'''+'\n'.join(comparison_rows)+f'''

全部为逐比较的开发期描述区间。7日循环区块固定5000次、seed1217，作为事后统一时间相关敏感性，未据结果选区块长度。它们不校正架构/历次开发选择，也不能创造独立测试；三seed不是三个独立数据集。99%描述区间保留在审计JSON，不挑较好看的区间报告。

具体案例的聚合见HISTORY_CASES.md：Mean相对原冻结参考新增114、挤出21，净93；相对等参数self新增103、挤出25，净78。每个对照按预先固定哈希顺序各取新增正例、挤出正例、新增负例6条，共72条，均检查严格过去及同用户卡。案例计数已与本次完整独立队列核验逐项对上。

逐笔源时间、金额与历史事实只保存在本机case run，不上传GitHub；仓库仅保留聚合与SHA256。观察到某种历史差异不等于已经识别模型得分原因，标签也仅是合成基准的评测标签，不能包装成真实调查结案。

## 渠道排序与强统计解释

`history-channels.csv`逐条件给出各渠道审核名额、TP/FP、Precision/Recall，以及在该渠道全部交易内部计算的AP。分渠道AP不是重新给每渠道10名额，主业务队列始终所有渠道合计每日10。配额占比不是要求与交易占比机械相等，须结合捕获和误报看排序失衡是否改善。

![渠道名额与捕获](history-channels.png)

14项确定统计没有涵盖Mean全部输入，因此看到Mean215后追加一次14统计强树、再登记220维广覆盖统计的三seed门控网络和固定强树。后者覆盖所有24个已预处理数值/缺失指标的均值、标准差、最近值、当前差值和训练MCC频率，始终同用户卡、严格过去last6。

这5个是保守的事后挑战，不伪称预注册。广统计网络参数略多、树的损失/容量不同，作为更强业务备选；只能说历史模型是否超出这些已明确实现的统计对照，不能声称超出任何可能的手工统计。全部结果保留，之后不再扩特征或搜索树参数。

## 实现与独立核验

全部训练图边、推理图边逐条检查严格早于目标、同用户同卡、无同刻互见；K24图对6/12/24取前缀，源SQL另行重建邻域与14项统计。原卡号已按user:card序号命名，图还显式按(user_id,card_id)隔离。原核心的用户级历史统计没有改成卡级，不能声称全部输入都只包含卡级事实。

GRU只pack有效历史并按旧到新顺序；Transformer屏蔽padding且全冷启动回退点模型；Mean、衰减、Attention也先将非法槽置零。等参数控制重复当前表示，不读邻居、实际邻居数或age。单元检查覆盖padding投毒、冷启动、初始化与控制参数匹配。

本轮GRU/Transformer显式编码的是顺序，Transformer用固定正弦位置，没有额外连续时间编码；衰减Mean使用固定86400秒尺度，Attention使用固定log1p(age/3600)偏置。它们都是有界对照，不是完整TGAT，不能从这里宣称完整时序图架构的普遍优劣。

主审计对38模型各重算全部422,088个分数、三seed均值、原堆式每日队列与含ties的AP；追加审计重算5个统计模型，并对全部1,263,980行220维特征及训练期scaler独立重建。主重载最大误差{audit['max_full_replay_abs_diff']:.9g}。图、源数据、模型和报告均以SHA256绑定。只读gate/attention探针按宿主评测标签分组，标签没有进入模型；它们是激活诊断，不是因果解释。

## 历史表示的诊断边界

'''+'\n'.join(gate_table)+'''

表内跨seed简单平均，完整逐seed/样本量在`history-activation-probes.csv`。只含实际有历史的目标，冷启动回退不混进门均值。上下文门不是可相加的得分贡献，范数比也不是风险原因；Attention有效邻居数是`1/sum(w²)`，不等于实际可用邻居数。

Mean先对每笔历史做训练得到的非线性编码，再平均；与先对原输入做均值/方差再建模不同。强统计对照用于检验这类表示是否超出已明确实现的普通统计，不预设神经结构胜出。衰减与Attention还加入了不同的时间偏好；门/权重集中现象至多提供可检验线索，不能仅凭最终TP宣称过拟合、梯度消失或注意力失效。

## 训练曲线与计算成本

![训练收敛](history-training.png)

`history-training-curves.csv`保存每seed每轮训练损失、学习率、样本数、训练耗时；主实例还保存正负损失、正负logit导数幅度、裁剪前梯度。logit导数是未除batch规模的逐样本导数，不能当作整网参数梯度范数。12轮是冻结的有限预算，不宣称各架构都已达到全局最优；仍有下降趋势时不把较低业务分数直接称为架构无效或已证明过拟合。

'''+'\n'.join(cost)+f'''

† 原始墙钟含一次长停顿。`history-long-wall-intervals.json`逐项保留超过本seed轮时中位数10倍的区间；不删除原始时间、不把整晚间隔冒充实际算力，也不根据日志臆断具体休眠/挂起原因。优化器学习率按epoch步进，曲线和权重均原样保留。

'''+'\n'.join(resource_table)+f'''

CPU秒是各线程合计，不等于墙钟秒。本次原主进程从启动时间累计的CPU由只读OS观察器在退出后获取；复现也可从起点用显式CLI外层记录主流程CPU（回执注明范围）。后续每个独立步骤在原CLI外记录CPU和结束峰值。宽统计神经网络与树共用该步骤峰值，不是每个模型独占；OS观察峰值在退出后内存信息不可得时是最终峰值下界。环境版本见ENVIRONMENT.json，资源回执见history-process-resources.json。

所有推理耗时对应每seed全部422,088个开发目标，包含研究批处理流程和上下文预处理；不是线上单请求p99或端到端SLA。图构建与核验另计{primary['graph']['seconds']:.2f}秒，图数组{primary['graph']['arrays_bytes']/1024**2:.2f}MiB。整进程峰值自启动累计，含数据/图和先前模型；不是隔离的单模型内存，不能直接据它比较架构。部分只读诊断、本地回归与主训练重叠，不伪称隔离硬件基准。

## 技术决定

本轮只回答此来源、切分、输入和预算下的历史信息与表示比较。若某架构只超过弱旧两轮值、没有超过冻结参考或强控制，保留研究身份。即使在已反复使用的2018明显改善，也只形成候选证据，不能当作新外部留出成功。

源标签available_at全为空，当前完整监督是回顾性比较，不证明2017末已可取得所有训练标签。Gate C接下来仅做独立来源、合法性、文本生成与时点可用性审查；满足条件再开语义实验。Gate D需可靠标签可得性定义，不能把真实未标注直接作负例。

默认保持neural_v2_ensemble；不改调查Agent合同，不覆盖旧模型、旧RESULTS或失败。若申请实际接入，必须独立集成PR并经用户明确批准。

本地回归：{tests['tests']}项、失败{tests['failures']}、错误{tests['errors']}、跳过{tests['skipped']}；对应XML SHA256 `{digest(tests_xml)}`。这不冒充GitHub共享CI通过。复现见REPRODUCE.md，首次失败与事后登记见FAILURES.md及GATE_B_ADDENDUM.md。
'''
    (output/'HISTORY_RESULTS.md').write_text(text, encoding='utf8')
    os.environ.setdefault('MPLCONFIGDIR', str((output.parent.parent/'runs/semantic-v21-initialization/mpl-cache').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    names = ['frozen_residual', 'flat', 'statistics']+[n for m in modes for n in (m, m+'-self')]+['posthoc_tree14', 'posthoc_broad_neural', 'posthoc_broad_tree']
    selected = frame.set_index('condition').loc[names]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(range(len(names)), selected.TP, color=['#5e81ac' if n.endswith('-self') else '#81a1c1' for n in names])
    for i, name in enumerate(names):
        points = str(selected.loc[name, 'seed_TP'])
        if points: ax.scatter([i]*len(points.split(',')), [int(p) for p in points.split(',')], color='black', s=13, zorder=3)
    ax.set_xticks(range(len(names)), names, rotation=55, ha='right', fontsize=8)
    ax.set_ylabel('Captured positives / 365 daily Top10 queues'); ax.set_title('Fixed-development comparison: bars=ensemble, dots=seeds; no unseen-test claim')
    fig.tight_layout(); fig.savefig(output/'history-top10.png', dpi=160); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    labels = ['frozen_residual', 'flat', 'statistics', *modes, 'posthoc_broad_neural', 'posthoc_broad_tree']
    for ax, field, title in zip(axes, ['reviewed', 'TP'], ['Review slots by channel', 'Captured positives by channel']):
        bottom = np.zeros(len(labels))
        for channel, color in [('Chip Transaction', '#5e81ac'), ('Online Transaction', '#d08770'), ('Swipe Transaction', '#a3be8c')]:
            values = channel_frame[channel_frame.channel == channel].set_index('condition').loc[labels, field].to_numpy()
            ax.bar(range(len(labels)), values, bottom=bottom, color=color, label=channel.split()[0]); bottom += values
        ax.set_xticks(range(len(labels)), labels, rotation=55, ha='right', fontsize=8); ax.set_title(title)
    axes[0].legend(fontsize=8); fig.tight_layout(); fig.savefig(output/'history-channels.png', dpi=160); plt.close(fig)
    fig, axes = plt.subplots(2, 3, figsize=(12, 7))
    for ax, mode in zip(axes.flat, ('flat', *modes)):
        for name, color in [(mode, '#5e81ac'), (mode+'-self', '#d08770')]:
            if name not in primary['results']: continue
            for seed in primary['results'][name]['seeds']:
                ax.plot([v['epoch'] for v in seed['curves']], [v['loss'] for v in seed['curves']], color=color, alpha=.65)
        ax.set_title(mode); ax.set_xlabel('Fixed epoch'); ax.set_ylabel('Training weighted BCE')
    fig.suptitle('Blue=history (flat panel=current); orange=exact-capacity self control; no epoch selection')
    fig.tight_layout(); fig.savefig(output/'history-training.png', dpi=160); plt.close(fig)
    result = {'status': 'Gate_B_complete_and_independently_verified', 'registered_models': 38, 'posthoc_models': 5,
              'full_model_replays': 43, 'models': rows, 'within_channel': channels, 'architectures_vs_mean': architectures_vs_mean,
              'case_gallery_sha256': digest(Path(case_run)/'report.json'), 'case_count': len(cases['cases']),
              'source_reports': {str(p): digest(p) for p in (history_run/'report.json', history_audit/'report.json', broad_run/'report.json', broad_audit/'report.json', tree_run/'report.json')},
              'tests': tests, 'tests_xml_sha256': digest(tests_xml), 'default_detector_changed': False,
              'independent_external_test': False, 'final_2019_read': False, 'paid_calls': 0}
    write_json(output/'history-results.json', result)
    write_json(output/'history-process-resources.json', resources)
    write_json(output/'history-long-wall-intervals.json', {'threshold': 'Over10x within-seed median actual epoch duration', 'intervals': long_intervals, 'raw_curves_unchanged': True})
    return result
