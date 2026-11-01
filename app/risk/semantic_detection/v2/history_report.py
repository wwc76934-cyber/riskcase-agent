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
