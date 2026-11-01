"""Publish read-only supplementary evidence without altering original Gate A results."""
import json
import os
from pathlib import Path
import shutil

from app.risk.semantic_detection.common import digest, write_json


def run(research_root, output):
    root, output = Path(research_root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    sources = {'composition': 'semantic-v21-composition-01', 'clusters': 'semantic-v21-clusters-01',
               'capacity': 'semantic-v21-capacity-01', 'label-census': 'semantic-v21-raw-label-census-01',
               'row-sample': 'semantic-v21-raw-sample-02'}
    records, fingerprints = {}, {}
    for name, folder in sources.items():
        path = root/'runs'/folder/'report.json'
        records[name] = json.loads(path.read_text(encoding='utf8'))
        fingerprints[name] = digest(path)
        shutil.copyfile(path, output/f'source-{name}.json')
    census, composition, clusters = records['label-census'], records['composition'], records['clusters']
    if census['status'] != 'all_selected_2016_2018_raw_labels_verified' or census['selected_records'] != 1263980:
        raise ValueError('Full raw-source label census is required')
    online = next(r for r in composition['decomposition'] if r['channel'] == 'Online Transaction')
    share = online['composition_component']/online['total_rate_change']
    rows = ['| 渠道 | 训练正率 | 按开发MCC组合标准化 | 开发正率 | MCC组合TV | 组合分量/百分点 | 单元内残差/百分点 |',
            '|---|---:|---:|---:|---:|---:|---:|']
    for r in composition['decomposition']:
        rows.append(f'| {r["channel"]} | {r["train_rate"]:.6%} | {r["development_mix_with_training_cell_rates"]:.6%} | {r["development_rate"]:.6%} | {r["MCC_mix_total_variation"]:.6f} | {r["composition_component"]*100:.6f} | {r["within_cell_rate_component"]*100:.6f} |')
    cluster_rows = ['| 维度 | Qwen ARI/NMI | 随机 ARI/NMI |', '|---|---:|---:|']
    for dimension, record in clusters['results'].items():
        q, r = (record['brand_sector_probe'][k] for k in ('qwen', 'random'))
        cluster_rows.append(f'| {dimension} | {q["ARI"]:.6f} / {q["NMI"]:.6f} | {r["ARI"]:.6f} / {r["NMI"]:.6f} |')
    text = '''# Gate A补充：原始标签、MCC组合、文本覆盖与容量

这些是不新增欺诈模型训练的补充诊断，在A主实验之后完成。原28个匹配/筛查实例、原指标、向量和失败记录保持原样。它们补强和限定归因，不是新的候选选择或独立测试。

## 原始CSV与提取结果的一致性

先对128笔按年份、渠道、标签分层的交易，独立核对用户、用户卡、时间、渠道、MCC、商户、金额和标签，128笔全部一致。随后对全部1,263,980笔选定2016–2018记录，按物理source_row回查原始压缩CSV，逐笔核对年份、渠道与标签，全部一致。

因此，2017 Online/Swipe正例均为0这一现象，也存在于这些记录的原始标签中，不能直接归咎于本轮提取时把标签弄错。核对不证明原模拟器机制正确，也不独立证明没有遗漏任何应入选的源记录。非选定行只按字节跳过；未解码或评测2019记录。

来源归档SHA256：`e9f589a0958f40d60f81b1a2e8428db86e00c05755caf44fb055827976c0efa2`。首次128笔审计的卡号命名空间比较错误及修正记录见`FAILURES.md`，未改变数据或模型。

## 渠道占比稳定，不等于渠道内部完全没有组合变化

'''+'\n'.join(rows)+f'''

固定训练期各MCC×渠道单元的经验正率，以开发期MCC构成重新加权：

`总正率变化 = (开发MCC权重×训练单元正率 − 训练正率) + (开发正率 − 开发MCC权重×训练单元正率)`。

Online从0.766523%下降到0.055362%；仅把MCC组合换成开发期构成，标准化正率已降到0.378341%。组合项为−0.388182个百分点，约占总下降的{share:.1%}；同MCC单元内的剩余变化为−0.322979个百分点。虽然MCC组合总变差只有0.013701，少量高训练正率单元的权重变化仍可产生较大影响。

**这是一种经验率的算术标准化，不是因果份额。** 稀疏单元正率的估计不稳定，残差仍混有其他特征、主体构成、时间和标签机制变化。不能把这54.6%解释成已经识别的真实欺诈机制，也不能根据汇总正率就宣称完整特征条件下的concept drift。

![MCC组合标准化](source-composition.png)

## 语义覆盖的边界

当前109个MCC中107个有核实字典描述。5912存在本次允许来源的码表歧义，5921未纳入允许字典，保留unknown_description，没有猜写。2018这两码合计28,847笔、44个正例，分别约占交易6.83%、正例6.44%。这不是训练期未见类别：底层普通类别编码仍能识别它们；缺少的是可靠的附加文字含义。当前结果不能单独识别缺失描述造成的性能影响。

对无channel的107个已知类别，固定K8聚类；对32个航空、租车、酒店品牌MCC，固定K3、seed731、n_init20，与行业标签比较：

'''+'\n'.join(cluster_rows)+'''

Qwen向量有明显产业结构，随机向量没有。这仅确认有限模板的语义结构可解释，不是完整行业评测，也不证明预训练知识对欺诈有独立价值。诊断没有使用欺诈标签，未把这些聚类器计入28个欺诈训练实例；完整簇中心近邻见`source-clusters.json`。

## 审核容量仍有多少空间

2018有683个正例，174天没有正例；5天正例超过10个，单日最多16个。即使预先知道答案，固定每日10名额最多捕获666个，而非683个。因此现有模型与容量上限之间仍有空间；不能把负结果解释成审核容量已经完全饱和。

这个666使用评测标签计算，只是不可部署的数学上限，不是新主指标、训练目标或候选基线。原主指标仍是每日Top-10累计真实捕获数。

## 复现和证据

命令见`REPRODUCE.md`。五份`source-*.json`保留原报告字节，`source-diagnostics.json`记录其SHA256。没有新增交易或身份上传，没有训练新LLM，没有改写原Gate A成绩。
'''
    (output/'SOURCE_DIAGNOSTICS.md').write_text(text, encoding='utf8')
    os.environ.setdefault('MPLCONFIGDIR', str((root/'runs/semantic-v21-initialization/mpl-cache').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4.8))
    channels = ['Chip', 'Online', 'Swipe']
    for index, (field, label) in enumerate([('train_rate', 'Train2016-17'),
              ('development_mix_with_training_cell_rates', '2018 MCC mix / train cell rates'), ('development_rate', 'Observed2018')]):
        ax.bar([i+(index-1)*.24 for i in range(3)], [r[field]*100 for r in composition['decomposition']], width=.24, label=label)
    ax.set_xticks(range(3), channels); ax.set_ylabel('Positive rate (%)')
    ax.set_title('Descriptive MCC-composition standardization; not causal attribution')
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(output/'source-composition.png', dpi=160); plt.close(fig)
    for name in ('ROOT_CAUSE.md', 'CHANNEL_AUDIT.md'):
        path = output/name
        if path.exists():
            previous = path.read_text(encoding='utf8')
            if '[SOURCE_DIAGNOSTICS.md]' not in previous:
                path.write_text(previous+'\n\n## 原始来源与组合变化补充核验\n\n见[SOURCE_DIAGNOSTICS.md](SOURCE_DIAGNOSTICS.md)：全部1,263,980笔选定2016–2018记录的源标签核对通过；MCC组合标准化进一步区分组成变化与单元内残差，同时列明语义字典覆盖和不可部署的审核容量上限。原A主实验成绩不变。\n', encoding='utf8')
    result = {'status': 'supplementary_source_diagnostics_published', 'original_gate_A_results_unchanged': True,
              'source_report_sha256': fingerprints, 'new_fraud_model_training': False, 'final_2019_read': False}
    write_json(output/'source-diagnostics.json', result)
    return result
