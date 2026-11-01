"""Render measured attribution evidence; in-progress gates are explicitly named."""
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import torch

from app.risk.semantic_detection.common import digest, write_json


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |',
                      *['| '+' | '.join(map(str,row))+' |' for row in rows]])


def plot_cache():
    cache=Path(__file__).resolve().parents[4]/'runs/semantic-v21-initialization/mpl-cache'
    cache.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR',str(cache))


def render_a1(audit, raw_audit, geometry, research_root, output):
    plot_cache()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    audit,raw_audit,geometry,research_root,output=map(Path,(audit,raw_audit,geometry,research_root,output))
    output.mkdir(parents=True,exist_ok=True)
    report=json.loads((audit/'report.json').read_text(encoding='utf8'))
    raw=json.loads((raw_audit/'report.json').read_text(encoding='utf8'))
    geom=json.loads((geometry/'report.json').read_text(encoding='utf8'))
    counts=pd.read_csv(audit/'channel-population.csv',dtype={'year':str})
    for filename in ('channel-population.csv','channel-scores.csv','mcc-channel-scores.csv','rank-changes.csv'):
        shutil.copyfile(audit/filename,output/filename)
    shutil.copyfile(raw_audit/'raw-month-channel-counts.csv',output/'month-channel-counts.csv')
    names=list(report['models']);channels=['Chip Transaction','Online Transaction','Swipe Transaction']
    colors=['#2674a8','#df7935','#579b72']
    plt.rcParams.update({'font.size':10,'figure.dpi':150})
    fig,axes=plt.subplots(1,2,figsize=(13,5.5),layout='constrained')
    for ax,field,title in zip(axes,['reviewed','tp'],['Daily top-10 review allocation','Captured positives, same 3,650 slots']):
        bottom=np.zeros(len(names))
        for channel,color in zip(channels,colors):
            values=np.array([report['models'][n]['metrics']['channel'][channel][field] for n in names])
            ax.bar(np.arange(len(names)),values,bottom=bottom,color=color,label=channel.replace(' Transaction',''))
            bottom+=values
        ax.set_xticks(np.arange(len(names)),names,rotation=45,ha='right');ax.set_title(title)
        ax.set_ylabel('Slots' if field=='reviewed' else 'True positives');ax.spines[['top','right']].set_visible(False)
    axes[1].legend(loc='upper right',fontsize=8)
    fig.suptitle('2018 observed development | frozen artifacts replay, no new fitting',fontsize=12)
    fig.savefig(output/'channel-allocation.png');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    for ax,field,title in zip(axes,['transaction_share','positive_rate'],['Transaction shares are stable','Conditional label rates change sharply']):
        for j,(channel,color) in enumerate(zip(channels,colors)):
            rows=counts[counts.channel==channel].sort_values('year')
            ax.bar(np.arange(3)+(j-1)*.25,rows[field].to_numpy()*100,width=.25,color=color,label=channel.replace(' Transaction',''))
        ax.set_xticks(range(3),['2016 train','2017 train','2018 dev']);ax.set_title(title);ax.set_ylabel('Percent')
        ax.spines[['top','right']].set_visible(False)
    axes[0].legend(fontsize=8);fig.savefig(output/'year-channel-drift.png');plt.close(fig)
    population_rows=[[r.year,r.channel.replace(' Transaction',''),int(r.rows),int(r.positive),int(r.negative),f'{r.positive_rate*100:.4f}%',f'{r.transaction_share*100:.2f}%'] for r in counts.itertuples()]
    model_rows=[]
    for name in names:
        m=report['models'][name]['metrics'];c=m['channel']
        model_rows.append([name,*[f'{c[v]["reviewed"]} / {c[v]["tp"]}' for v in channels],m['tp_daily10'],f'{m["ap"]:.6f}'])
    pair=report['comparisons_vs_frozen']['B2']
    drift_rows=[[x['year_vs_2018'],f'{x["total_variation"]:.6f}',f'{x["cramers_v"]:.6f}',f'{x["p_unadjusted"]:.6g}'] for x in report['distribution_drift']]
    example_rows=[]
    for case in report['failure_examples'][:8]:
        old,new=case['displaced_positive'],case['admitted_online_negative']
        example_rows.append([case['day'],old['alias'],old['channel'].replace(' Transaction',''),old['ranks']['frozen_residual'],old['ranks']['B2'],new['alias'],new['ranks']['frozen_residual'],new['ranks']['B2']])
    scale=[]
    for name in ('B1','B2','B3'):
        for seed in (42,43,44):
            state=torch.load(research_root/'runs/semantic-e1-additive-v2'/name/f'seed-{seed}/model/model.pt',weights_only=True,map_location='cpu')
            weight=state['state_dict']['category.weight'][1:].numpy()
            norms=np.linalg.norm(weight,axis=1)
            scale.append({'model':name,'seed':seed,'median_auxiliary_norm':float(np.median(norms)),
                          'min_auxiliary_norm':float(norms.min()),'max_auxiliary_norm':float(norms.max())})
    same_mcc=sum(x['key'][0]==x['neighbors'][0]['key'][0] for x in geom['nearest5'])/geom['unique_texts']
    geometry_summary={'unique_texts':geom['unique_texts'],'dimensions':geom['dimensions'],
                      'nearest1_same_mcc_fraction':same_mcc,'nearest1_same_channel_fraction':geom['nearest1_same_channel_fraction'],
                      'channel_group_mcc_cv_balanced_accuracy':geom['channel_balanced_accuracy_grouped_by_mcc'],
                      'auxiliary_norms':scale}
    write_json(output/'a1-evidence.json',{'channel_audit':report,'raw_source_audit':raw,'embedding_initial_geometry':geometry_summary,
               'source_receipt_sha256':{'audit':digest(audit/'report.json'),'raw':digest(raw_audit/'report.json'),'geometry':digest(geometry/'report.json')}})
    text='''# 渠道偏置审计：Gate A1

本报告只复核旧权重，不训练新模型。统计期为2016、2017训练年及2018已观察开发年；不选取2019记录。原始事实库与标签库独立SQL计数与特征适配器完全一致；全部9组模型按各自真实配置重载，所有seed的开发分数复现误差均不超过1e-6。

## 样本和标签

'''+table(['年份','渠道','交易','正例','负例','正例率','交易占比'],population_rows)+'''

![年度渠道分布与标签率](year-channel-drift.png)

交易构成变化很小；标签条件分布变化很大。2016的Online有773个正例，2017为0，2018为28；2018的Chip有564个正例。不能把它称为交易渠道组成突然变化，也不能未经匹配实验将它全部归罪于LLM。

'''+table(['训练年份 vs 2018','渠道组成总变差','Cramer V','列联检验p（未校正）'],drift_rows)+'''

大样本下微小效应也可有小p值，须优先看效应量与实际名额损失。逐渠道正例率Fisher检验、六项Bonferroni校正、每月明细及用户/用户卡正例集中度见机器回执。2016 Online正例涉及201个用户、259个用户卡；头部用户仅占2.59%，不支持将该现象解释为单个异常用户驱动。标签只有二元事件关联，未证明结案或可得时间；不推断真实世界欺诈机制改变。

## 2018每日Top-10名额

'''+table(['模型','Chip 名额/TP','Online 名额/TP','Swipe 名额/TP','总TP','AP'],model_rows)+'''

![名额分配与捕获](channel-allocation.png)

B2将91.78%的名额分给Online；该渠道仅占当年交易11.98%、正例4.10%。3350个名额捕获25个正例、产生3325个FP。原模型745个Online名额捕获13个正例。B2多分配2605个Online名额只多捕获12个Online正例；与此同时Chip捕获83→5、Swipe捕获26→15，总捕获122→45。这里是按渠道总量的差额，不等于逐交易新增/挤出的数目。

逐交易比较：B2相对原模型新增正例'''+str(pair['added_positive'])+'，挤出正例'+str(pair['removed_positive'])+'，净变化'+str(pair['net'])+'；配对日95%描述区间'+str(pair['paired_day_bootstrap_95'])+'''。保留每日明细；区间未解决时间相关、多轮开发选择或多重比较问题。

## 同批交易的日内排名变化与失败样例

'''+table(['日期','挤出正例别名','正例渠道','原排名','B2排名','新增Online负例别名','原排名','B2排名'],example_rows)+'''

样例是同一日固定容量下的竞争证据，不能据此认定单笔交易的某字段造成了真实欺诈。完整模型分数、每笔日排名、同批输入行序哈希保留在本地回执；公开汇总只保留别名和必要的派生信息。

## 可核查明细

- `channel-scores.csv`：每个模型×年份×渠道的均值、1/10/50/90/95/99/99.9/100分位数。
- `mcc-channel-scores.csv`：相同分位数按MCC与channel组合展开，包含样本量和正例数；小组不当作稳定效果。
- `rank-changes.csv`：相同交易在原模型/B1/B2/B3及历史模型间的渠道名额变化、日排名相关、正例排名与新增/挤出数。
- `month-channel-counts.csv`：独立原始SQL的逐月计数；`a1-evidence.json`绑定来源、配置、分数重放、漂移检验和例子。

## 当前判断与后续准入

已找到“训练中Online是强风险线索、开发期该线索反转，固定每日容量进一步放大损失”的可核验证据。B0和B1同样大量选择Online，说明此现象不限于语义模型；B2较B1进一步失衡的机制仍需A2/A3匹配对照，不能由这张表作因果定论。

原冻结配置实际是engineered=false、include_channel=false、5轮、3seed，与此前报告一致。E1保留的普通类别分支宽度、联合附加分支与原模型不同；新A2保持原残差核心及共同初始化，逐项开关channel和附加表示。

Gate A1完成；Gate A整体尚须完成A2和A3后才能进入Gate B训练。默认检测器不变。
'''
    (output/'CHANNEL_AUDIT.md').write_text(text,encoding='utf8')
    root='''# 失败归因：V2.1

当前状态：Gate A1完成，Gate A2/A3的匹配训练尚待完成；不能宣称Gate A已全部通过。

1. 已独立核对原始标签与特征适配器：交易渠道组成相对稳定，但渠道条件标签率跨年反转。B2的Online名额3350与原模型745均复现。见[渠道审计](CHANNEL_AUDIT.md)。
2. 旧向量并未完全丢失类别语义：272条唯一文本中，最近邻属于相同MCC的比例为'''+f'{same_mcc:.2%}'+'''；最近邻属于相同channel为'''+f'{geom["nearest1_same_channel_fraction"]:.2%}'+'''。按MCC分组留出后，渠道线性分类平衡准确率五折为'''+str([round(x,4) for x in geom['channel_balanced_accuracy_grouped_by_mcc']])+'''（随机基线1/3）。MCC相似性和渠道可解码性可以同时存在；几何性质不等于欺诈预测增益。
3. E1普通可训练附加表与单位范数冻结语义表存在尺度差异：实际范数已记录在`a1-evidence.json`。A2统一附加向量L2与unknown策略，控制共同核心初始化，避免把尺度或类别分支容量差异误当预训练知识贡献。
4. 旧历史Mean=66超过同为2轮的平面B1=50，但低于原冻结模型122；训练预算、通道、参数量和用户卡邻域未完全匹配，尚不能证明历史架构的真实增量。Gate B将单独验证。

下一步按冻结的[执行协议](PROTOCOL.md)完成2×3匹配实验和有界语义诊断；不扩大Qwen模型，不替换默认检测器，不开启半监督混合实验。原有`docs/semantic-detection/`及运行产物均保留。
'''
    (output/'ROOT_CAUSE.md').write_text(root,encoding='utf8')
    return {'status':'A1_report_complete','gate_A_complete':False,'figures':2}


def render_gate_a(run_path,matched_audit,embedding_audit,a1,output,tests_xml):
    plot_cache()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import xml.etree.ElementTree as ET
    run_path,matched_audit,embedding_audit,a1,output=map(Path,(run_path,matched_audit,embedding_audit,a1,output))
    report=json.loads((run_path/'report.json').read_text(encoding='utf8'))
    audit=json.loads((matched_audit/'report.json').read_text(encoding='utf8'))
    embedding=json.loads((embedding_audit/'report.json').read_text(encoding='utf8'))
    if audit['status']!='Gate_A_matched_independently_verified' or audit['report_sha256']!=digest(run_path/'report.json'):
        raise ValueError('Independent matched audit required')
    if report['screen_confirmation'] is not None or audit['total_models']!=28:
        raise ValueError('This measured negative-outcome report requires its registered 28-run outcome; do not reuse its conclusions for another run')
    suites=list(ET.parse(tests_xml).getroot().iter('testsuite'))
    tests={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    if tests['failures'] or tests['errors'] or tests['skipped']:raise ValueError('Passing non-skipped research checks required')
    results=report['results'];rows=[]
    for channel in ('nochannel','channel'):
        for mode in ('original','learned','semantic'):
            name=f'{channel}-{mode}-32';r=results[name];m=r['ensemble'];seed=r['seeds'][0]
            rows.append([channel,mode,str([s['metrics']['tp_daily10'] for s in r['seeds']]),m['tp_daily10'],f'{m["ap"]:.6f}',
                         m['channel']['Online Transaction']['reviewed'],seed['total_parameters'],seed['trainable_parameters']])
    figure,axes=plt.subplots(1,2,figsize=(11,4.8),layout='constrained')
    for ax,channel in zip(axes,('nochannel','channel')):
        for i,(mode,color) in enumerate(zip(('original','learned','semantic'),('#65727e','#2c8f75','#dc873f'))):
            record=results[f'{channel}-{mode}-32']
            ax.bar(i,record['ensemble']['tp_daily10'],color=color,width=.55)
            ax.scatter(i+np.array([-.12,0,.12]),[s['metrics']['tp_daily10'] for s in record['seeds']],color='#172b42',s=23,zorder=3)
        ax.axhline(122,color='#647586',linestyle='--',linewidth=1,label='Frozen reference:122')
        ax.set_xticks(range(3),['Original categories','Learned auxiliary','Frozen Qwen32']);ax.set_title(channel)
        ax.set_ylim(0,155);ax.set_ylabel('Captured positives at daily top-10');ax.spines[['top','right']].set_visible(False)
    axes[1].legend(loc='upper right',fontsize=8)
    figure.suptitle('Matched Gate A: 2018 observed development | bars=3-seed ensemble, dots=each seed')
    figure.savefig(output/'matched-channel-matrix.png');plt.close(figure)
    fig,ax=plt.subplots(figsize=(8.5,4.5),layout='constrained')
    for j,(mode,color) in enumerate(zip(('learned','semantic','random'),('#2c8f75','#dc873f','#7386a7'))):
        values=[results[f'channel-{mode}-{d}']['seeds'][0]['metrics']['tp_daily10'] for d in (32,128,256)]
        ax.bar(np.arange(3)+(j-1)*.25,values,width=.25,label=mode,color=color)
    ax.set_xticks(range(3),['32 dimensions','128 dimensions','256 dimensions']);ax.set_ylabel('Captured positives, daily top-10')
    ax.set_title('Single seed42 screens | same 5 epochs and channel inputs');ax.legend(loc='upper center',ncol=3)
    ax.set_ylim(0,115);ax.spines[['top','right']].set_visible(False)
    fig.savefig(output/'semantic-dimension-screen.png');plt.close(fig)
    screen_rows=[]
    for name,r in results.items():
        if name in {f'{c}-{m}-32' for c in ('channel','nochannel') for m in ('original','learned','semantic')}:continue
        m=r['seeds'][0]['metrics'];screen_rows.append([name,m['tp_daily10'],f'{m["ap"]:.6f}',m['channel']['Online Transaction']['reviewed']])
    geometry_rows=[]
    for d,modes in embedding['dimensions'].items():
        for name,m in modes.items():
            geometry_rows.append([d,name,f'{m["nearest1_same_mcc_fraction"]:.2%}',
                f'{np.mean(m["channel_balanced_accuracy_MCC_grouped_CV"]):.2%}',
                f'{m["industry_same_channel_different_mcc_NN_accuracy"]:.2%}'])
    text='''# Gate A：失败归因与有限语义研究结论

状态：Gate A已完成。主结论是在本数据和协议中停止扩大LLM模型，进入独立的充分监督历史建模。2018是反复观察的开发集，本轮不接触2019记录；没有独立未来效果声明。

## 为什么审核名额偏向Online

[原始渠道审计](CHANNEL_AUDIT.md)已复现B2的3350个Online名额与原模型745个名额。训练期Online正例773/100845，Chip正例128/617040；开发期Online正例28/50576、Chip正例564/310348。交易渠道占比接近不变，而渠道与标签的关联跨年发生强变化。此处是观测到的条件标签率变化，不能单由汇总判定完整特征条件下的概念漂移、真实欺诈机制变化或标签生成变化。

新2×3对照使用同样的12个原始数值特征、原残差核心、五轮、同三seed、训练期拟合的预处理；输入组内共同核心初始化完全相同。无channel条件同时删除类别通路与文本通路中的channel，附加向量范数及unknown策略统一。原无channel残差重训分数与原冻结分数最大差为'''+str(report['original_residual_reproduction_max_abs_diff'])+'''。

'''+table(['输入组','表示','seed TP','集成TP','AP','Online名额','总参数','可训练参数'],rows)+'''

![匹配渠道实验](matched-channel-matrix.png)

启用channel后，原类别编码122→52，普通附加Embedding131→71，语义Embedding126→55；Online名额分别745→3070、736→2885、730→3075。模型更容易学习训练期强相关的渠道线索，该线索在开发期的有限每日资源竞争中造成明显代价。这一失衡在没有LLM的模型中同样出现，不能把原模型与B2的全部差距归给预训练语义。

两输入组的类别通路和必要参数随channel改变；这是三seed受控建模对照，不是对每一内部机制的完全因果识别。原E1的普通/语义分支还存在向量尺度、类别分支宽度差异；本轮控制它们，不倒写原负结果，也不单独声称尺度因素的贡献已经被识别。

AP和业务主指标在此处不一致：例如原类别编码启用channel后AP略高，日Top-10捕获却122→52。因此不能改用AP挑选更好看的模型。所有分数均为未校准的排序信号，不当作欺诈概率。

## 语义表示为什么没有稳定增量

无channel时，语义相对普通附加Embedding为126−131=−5，配对日95%描述区间'''+str(report['comparisons']['nochannel_semantic_vs_learned']['paired_day_bootstrap_95'])+'''；有channel时55−71=−16，区间'''+str(report['comparisons']['channel_semantic_vs_learned']['paired_day_bootstrap_95'])+'''。三个seed均未出现语义超过同输入普通Embedding的TP增量。

'''+table(['维度','几何对照','最近邻同MCC','按MCC留出渠道平衡准确率均值','三行业同渠道/不同MCC近邻准确率'],geometry_rows)+'''

行业诊断只覆盖已核实的航空、租车、酒店三组，共92条唯一文本；排除相同MCC并匹配channel，不将有限诊断当作完整行业基准。五折训练/验证MCC无交叉。向量确实保留了行业/MCC关系，也能解码channel；这不意味着这些静态关系能预测当前数据中的欺诈标签。

对于这里只有MCC、channel短描述的输入，预训练并未新增交易行为或客户业务事实。普通类别表已能表达类别身份；预训练产业相似性与欺诈排序相关性不是一回事。不能据此否定更充分且合法可得文本上的语言知识价值。

独立词表审计还确认：2018没有训练期未见的MCC/channel联合键（有无channel两组均为0条、0正例）。本轮没有检验语言模型面向词表外新类别的迁移优势，不能把已见类别查表任务的负结果外推为所有语义场景的结论。

## 有界维度、文本和随机对照

![维度筛查](semantic-dimension-screen.png)

'''+table(['seed42条件','TP','AP','Online名额'],screen_rows)+'''

128维语义74低于同维可训练87和随机79；256维语义73低于可训练91和随机82。行业抽象、描述打乱、类别对应打乱和随机冻结结果全部保留。这里都是同预算的单seed机制筛查，不作为稳定性或显著性证据；没有候选同时超过预先规定的普通/随机对照，故追加确认未触发，不继续增大IBM上的Embedding模型。

字典复核修正了通用行业范围端点（航空3000–3350、租车3351–3441、酒店3501起）；实际训练词表不含受修正端点影响的类别。六套已生成文本逐条比对完全不变，旧bank和旧manifest原样保留，等价回执另存，未重写向量或训练结果。

## 复核、成本与局限

独立审计重载全部'''+str(audit['total_models'])+'''个模型，重新计算含分数ties的AP、原堆式日Top-10、训练期预处理统计和词表；检查无channel模型对channel替换不变，三seed均值及原残差重现均通过。最大重载差'''+str(audit['reload_max_abs_diff'])+'''；本轮研究检查'''+str(tests['tests'])+'''项通过。详见`gate-a-results.json`与`matched-results.json`。模型/分数/训练曲线和首次失败日志留在独立本地runs目录，未上传交易或权重。

28个训练实例包括18个三seed匹配模型、10个单seed筛查；不把几何诊断的渠道分类器或单元测试计入正式欺诈模型成绩。Qwen固定权重只冻结编码432条唯一类别文本，保留完整向量后切32/128/256维；原32维缓存重现误差约6e-8。训练、推理耗时及整进程峰值在每模型回执中；部分诊断和其他进程重叠，不作为隔离硬件基准。

2018多轮用于开发，配对日区间是描述性、逐比较结果，未解决选择偏差、时间依赖与多重比较。现代字典和预训练模型晚于数据年份。普通附加Embedding131略高于原模型122也不足以直接批准默认替换。

## Gate B准入

Gate A已提供渠道失衡及语义无稳定收益的可复核证据。继续使用非LLM的raw/no-channel普通可训练类别核心，冻结更充分预算后，对比历史Mean、时间衰减Mean、GRU、轻量Transformer、单跳Attention、每个架构的等参数当前特征控制、确定性历史统计及强平面/冻结基线。新邻居严格按(user_id,card_id)隔离；原点特征既有的用户级统计来源另行明示。

不将原两轮Mean的66对弱平面50直接描述成优于原冻结122。Gate C仅在后续独立数据可行性核验后启动文本实验；Gate D最后。默认检测器继续为neural_v2_ensemble。
'''
    (output/'ROOT_CAUSE.md').write_text(text,encoding='utf8')
    shutil.copyfile(run_path/'report.json',output/'matched-results.json')
    shutil.copyfile(embedding_audit/'report.json',output/'embedding-diagnostics.json')
    shutil.copyfile(matched_audit/'report.json',output/'matched-independent-audit.json')
    complete={'status':'Gate_A_complete','Gate_B_admitted':True,'larger_IBM_semantic_model_admitted':False,
              'formal_matrix_models':18,'single_seed_screens':10,'confirmation_models':0,'tests':tests,
              'receipts':{name:{'path':str(path).replace('\\','/'),'sha256':digest(path)} for name,path in {
                  'matched':run_path/'report.json','independent_matched':matched_audit/'report.json',
                  'embedding_geometry':embedding_audit/'report.json','channel':a1/'report.json','tests':Path(tests_xml)}.items()},
              'daily10_ensemble_matrix':{name:results[name]['ensemble']['tp_daily10'] for name in results if len(results[name]['seeds'])==3},
              'no_independent_external_test':True,'default_detector_changed':False,'final_2019_read':False,'paid_calls':0}
    write_json(output/'gate-a-results.json',complete)
    return complete
