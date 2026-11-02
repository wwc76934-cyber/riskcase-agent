"""Evidence-bound reporting; no method selection or adoption side effects."""
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.data import load_data
from app.risk.semantic_detection.evaluation import Ranking
from .semi_experiment import read


def run(source_root,experiment_run,audit_run,history_run,broad_run,tests_xml,output,*,training_resources,audit_resources):
    experiment_run,audit_run,history_run,broad_run,output=map(Path,(experiment_run,audit_run,history_run,broad_run,output))
    report,audit,protocol=map(read,(experiment_run/'report.json',audit_run/'report.json',experiment_run/'protocol.json'))
    if audit['status']!='Gate_D_controlled_experiment_independently_verified' or audit['full_model_replays']!=32:
        raise ValueError('All32 masked/diagnostic/tree instances must be replayed first')
    if audit['report_sha256']!=digest(experiment_run/'report.json') or audit['protocol_sha256']!=digest(experiment_run/'protocol.json'):
        raise ValueError('D audit belongs to different result/protocol bytes')
    xml=ET.parse(tests_xml).getroot();suites=[xml] if xml.tag=='testsuite' else list(xml.iter('testsuite'))
    tests={key:sum(int(s.attrib.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    if not tests['tests'] or tests['failures'] or tests['errors']:raise ValueError('Passing regression evidence required')
    resources={name:read(path) for name,path in [('training',training_resources),('audit',audit_resources)]}
    if any(r['status']!='completed' for r in resources.values()):raise ValueError('Successful training and replay resource receipts required')
    output.mkdir(parents=True,exist_ok=True)
    (_,_,_),(VX,vy,vmeta)=load_data(source_root)[0];ranking=Ranking(vmeta,vy)
    rows=[];channels=[];curves=[];losses={};scores={}
    for condition in protocol['conditions']:
        name=condition['name'];metrics=report['ensembles'][name]
        seed_eval=[report['seed_evaluations'][f'{name}/seed-{seed}'] for seed in (42,43,44)]
        records=[report['training_records'][f'{name}/seed-{seed}'] for seed in (42,43,44)]
        scores[name]=np.load(experiment_run/name/'ensemble.npy',allow_pickle=False)
        rows.append({'condition':name,'family':'fixed_masked_primary','TP':metrics['tp_daily10'],'AP':metrics['ap'],
            'Precision_at_daily10':metrics['precision'],'Recall_at_daily10':metrics['recall'],
            'seed_TP':','.join(str(e['metrics']['tp_daily10']) for e in seed_eval),
            'student_parameters':records[0]['student_parameters'],'train_CPU_seconds':sum(r['training_loop_CPU_seconds'] for r in records),
            'train_wall_seconds':sum(r['training_loop_wall_seconds'] for r in records),
            'inference_CPU_seconds':sum(e['inference_CPU_seconds'] for e in seed_eval),
            'inference_wall_seconds':sum(e['inference_wall_seconds'] for e in seed_eval),
            'Online_slots':metrics['channel']['Online Transaction']['reviewed']})
        for record in records:
            curves.extend({'condition':name,'seed':record['seed'],'phase':'primary',**row} for row in record['curves'])
        if condition['name'] in protocol['diagnostic_conditions']:
            losses[name]=records[0]['loss']
            diagnostic=read(experiment_run/'diagnostics'/name/'training.json')
            curves.extend({'condition':name,'seed':42,'phase':'fixed_extra_six',**row} for row in diagnostic['curves'])
    references={'masked_tree':experiment_run/'masked-tree-development-scores.npy',
        'full_supervised_broad':broad_run/'neural-ensemble.npy','full_supervised_mean':history_run/'mean/ensemble.npy',
        'frozen_residual':Path(source_root)/'runs/neural-v2-development-01/ensemble-development-scores.npy'}
    for name,path in references.items():
        score=np.load(path,allow_pickle=False);scores[name]=score;metrics=ranking.metrics(score,VX.channel.to_numpy())
        tree=report['masked_tree'] if name=='masked_tree' else {}
        rows.append({'condition':name,'family':'fixed_visible_tree' if name=='masked_tree' else 'full_supervision_context',
            'TP':metrics['tp_daily10'],'AP':metrics['ap'],'Precision_at_daily10':metrics['precision'],'Recall_at_daily10':metrics['recall'],
            'seed_TP':'','student_parameters':None,'train_CPU_seconds':tree.get('training_CPU_seconds'),
            'train_wall_seconds':tree.get('training_wall_seconds'),'inference_CPU_seconds':tree.get('inference_CPU_seconds'),
            'inference_wall_seconds':tree.get('inference_wall_seconds'),'Online_slots':metrics['channel']['Online Transaction']['reviewed']})
    for row in rows:
        name=row['condition'];score=scores[name];metrics=ranking.metrics(score,VX.channel.to_numpy())
        for channel,values in metrics['channel'].items():
            mask=VX.channel.to_numpy()==channel
            channels.append({'condition':name,'channel':channel,'reviewed':values['reviewed'],'TP':values['tp'],
                'FP':values['reviewed']-values['tp'],'Precision':values['precision'],'Recall':values['recall'],
                'within_channel_AP':float(average_precision_score(vy[mask],score[mask])) if vy[mask].sum() else None})
    frame=pd.DataFrame(rows);channel_frame=pd.DataFrame(channels);curve_frame=pd.DataFrame(curves)
    frame.to_csv(output/'semi-models.csv',index=False);channel_frame.to_csv(output/'semi-channels.csv',index=False)
    curve_frame.to_csv(output/'semi-training-curves.csv',index=False)
    write_json(output/'semi-loss-weights.json',losses);write_json(output/'semi-comparisons.json',audit['comparisons'])
    write_json(output/'semi-diagnostics.json',audit['diagnostics']);write_json(output/'semi-process-resources.json',resources)
    pd.DataFrame(audit['year_channel_label_mix']).to_csv(output/'semi-label-mix.csv',index=False)
    table=['| 条件 | 三seed各自TP | 集成TP | P@10 | R@10 | AP | Online名额 |','|---|---|---:|---:|---:|---:|---:|']
    for row in rows:
        table.append(f"| {row['condition']} | {row['seed_TP'] or '上下文/单树'} | {row['TP']} | {row['Precision_at_daily10']:.4f} | {row['Recall_at_daily10']:.4f} | {row['AP']:.6f} | {row['Online_slots']} |")
    weight_table=['| 损失 | γ | 原负系数 | 原正系数 | 固定全局尺度 | p=.5时正logit导数幅度 | p=.5时负logit导数幅度 |','|---|---:|---:|---:|---:|---:|---:|']
    for name,loss in losses.items():
        probe=next(p for p in loss['logit_gradient_probes'] if p['predicted_positive_probability']==.5)
        weight_table.append(f"| {name} | {loss['gamma']} | {loss['raw_negative_coefficient']:.6f} | {loss['raw_positive_coefficient']:.6f} | {loss['fixed_loss_scale']:.6f} | {probe['positive_abs_logit_gradient']:.6f} | {probe['negative_abs_logit_gradient']:.6f} |")
    comparison_table=['| 对照差：候选−基线 | 新增正例 | 挤出正例 | 净变化 | 配对日95% | 7日区块95% |','|---|---:|---:|---:|---|---|']
    for name,value in audit['comparisons'].items():
        if any('_vs_'+ref in name for ref in ('full_supervised_broad','full_supervised_mean','frozen_residual')):continue
        comparison_table.append(f"| {name} | {value['added_positive']} | {value['removed_positive']} | {value['net']:+d} | {value['paired_day_bootstrap_95']} | {value['block_sensitivity']['paired_circular_block_95']} |")
    diag_table=['| 损失（固定seed42） | 12轮TP | 18轮TP | 净变化 | 12轮AP | 18轮AP |','|---|---:|---:|---:|---:|---:|']
    for name,value in audit['diagnostics'].items():
        a,b=value['epoch12'],value['epoch18'];diag_table.append(f"| {name} | {a['tp_daily10']} | {b['tp_daily10']} | {b['tp_daily10']-a['tp_daily10']:+d} | {a['ap']:.6f} | {b['ap']:.6f} |")
    primary=frame.set_index('condition');bce=int(primary.loc['bce','TP'])
    consistency_names=[c['name'] for c in protocol['conditions'] if c['consistency_weight']]
    deltas={name:int(primary.loc[name,'TP'])-bce for name in consistency_names}
    improved=[name for name,delta in deltas.items() if delta>0]
    decision=(f"四个一致性条件相对bce的点估计净变化为{deltas}。"+
        (f"其中{improved}为正差，须结合逐seed、额外视图基线和配对区间判断；不把点估计当稳定采用证据。" if improved else
         "本轮没有一致性条件超过同架构bce，不据此宣称其他权重、数据或半监督方法普遍无效。"))
    visibility=report['visibility'];cost=['| 进程 | 实际CPU秒（线程合计） | 整进程峰值GiB |','|---|---:|---:|']
    for name,receipt in resources.items():cost.append(f"| {name} | {receipt['process_CPU_seconds_all_threads']:.2f} | {receipt['whole_process_peak_bytes']/1024**3:.2f} |")
    text=f'''# Gate D｜既有合成数据的可控标签隐藏研究

本轮完成27个冷启动主神经模型、4个固定延训诊断和1个可见标签强树，并独立重算每实例全部422,088笔开发分数。**这是回顾性可控实验；真实标签发现时间未验证，2018不是独立测试。** 默认检测器保持`neural_v2_ensemble`，没有接入调查Agent。

## 范围与标签可得性假设

只复用既有IBM固定合成快照；沿用受限私人研究范围，不声称数据商业使用或再分发权利已经核实。源available_at为空。人为约定标签在事件后7天可用，以2017-12-31T23:59:59为训练截点，再按固定交易ID哈希暴露10%；不按标签重选mask。时间是源本地合成日历，不随意推定时区。

训练841,892笔，其中按假设可暴露标签{visibility['eligible_by_assumed_delay']}笔；最终可见{visibility['visible_rows']}笔，正类{visibility['visible_positive']}、负类{visibility['visible_negative']}；未标注{visibility['unlabeled_rows']}笔。宿主在全部拟合结束后核验：隐藏部分实际含{audit['hidden_positive_simulator_audit_only']}个合成正例；这些真值没有传入训练worker，不能把未知当正常。逐年份、渠道的可见/隐藏结构见semi-label-mix.csv。

优化只加载train分区行，归一化/类别词表可使用训练池内无标签X；不将开发样本送入模型、教师或预处理更新。共享缓存的整文件指纹读取和内存映射不等于开发训练，实际worker只接收训练前缀。全部主拟合、延训和树结束并写入权重指纹后，才加载本轮开发X/y评分；既有B汇总指标此前已观察。主模型只接收-1/0/1标签向量；监督损失仅取0/1子集，一致性目标是停止梯度的软概率。范围详见TRAINING_SCOPE_NOTES.md。

## 固定输入、损失和扰动

使用B已完整监督验证的220维广覆盖历史统计网络，普通类别Embedding、宽64、两残差块、12轮、seed42/43/44、batch4096、AdamW及相同余弦。没有复用完整监督神经权重；复用的是已独立核验、不读取标签的训练X预处理及严格过去同用户卡统计。原始点特征仍保留用户级历史量。

标准Focal取α正=.25、γ=2；额外weight-matched Focal取α正=20/21，与BCE20保持20:1类别比，用于区分类别权重与难例调制。类均衡Focal用β=.9999的有效样本数，只依据可见类别数量。全局尺度在参考概率p=.5处匹配期望loss，**不声称网络初始输出恰为.5，也不声称各方法梯度/曲率相同**。

'''+ '\n'.join(weight_table)+'''

semi-loss-weights.json另保留p=.001/.01/.1/.5/.9处的正负导数。训练CSV记录实际正负损失、逐样本监督logit导数、batch归约后的导数总量、整网裁剪前范数和裁剪比例。logit导数不是整网参数梯度；标准α=.25正负比为1:3，不能将它的结果直接解释成与BCE20只有γ不同。

一致性用EMA教师衰减.99，前2轮权重0，再3轮升到预定.1或.5；不做置信阈值搜索。教师sigmoid是加权目标下的未校准模型分数，其均值不能当作真实隐藏欺诈率。所有主条件保留统一的网络Dropout.15。“无额外视图”仍有该模型噪声；额外视图只对内部上下文激活做.1 Dropout，使用独立RNG，不改交易金额、类别、时间或邻居身份。bce_view的λ=0控制用于区分普通正则化和一致性增量。

为匹配计算路径，λ=0条件也计算无梯度教师前向；这是计算路径匹配的监督对照，并非最高效率的监督训练实现。固定强树只拟合可见标签，另提供业务备选，容量/类别权重与神经网络不同，不作单一因素因果对照。

Gate A没有稳定IBM语义增益，C1没有准入新文本数据，因此本轮没有预训练语义分支。这是条件未满足，不伪造“有无LLM”的实验结果，也不把旧全监督权重带进低标签训练。

## 业务成绩

'''+ '\n'.join(table)+'''

![固定低标签比较](semi-top10.png)

主指标仍是全渠道合计每日10名额。表中full_supervised是此前B的完整监督上下文，标签暴露和计算路径不同；masked_tree只有固定seed42，不能拿它冒充三seed稳定性。完整分渠道AP、名额、TP/FP、Precision/Recall在semi-channels.csv。

## 新增、挤出与不确定性

'''+ '\n'.join(comparison_table)+'''

原冻结、全监督Mean和全监督广统计的全部比较保留在semi-comparisons.json。配对日bootstrap固定2000次，7日循环区块固定5000次；都是反复开发数据上的逐比较描述区间，未校正全部搜索。三个seed共享同一个mask，只检验初始化稳定性，不能代表标签获取策略的抽样不确定性。

## 一次有界收敛诊断

四个未增强损失的seed42各自从低标签12轮检查点继续6轮，固定最终学习率5e-5。它们全部在打开本轮开发评分之前完成，不按较好的开发指标挑选延训对象。原12轮权重与主结果保留；18轮单seed诊断不替换12轮三seed主结果。

'''+ '\n'.join(diag_table)+'''

![损失曲线与固定延训](semi-training.png)

![一致性与教师概率](semi-consistency.png)

曲线用于诊断数值稳定和有限预算趋势。延训仍下降不等于已经全局收敛；业务分数低也不能直接证明损失无效。本轮到预定18轮诊断结束，不继续搜索α、β、λ或更多初始化。

## 技术判断与边界

'''+decision+f'''

权重匹配Focal相对bce的净变化为{audit['comparisons']['focal_matched_vs_bce']['net']:+d}；它相对常规Focal的净变化为{audit['comparisons']['focal_matched_vs_focal']['net']:+d}。这两项对照与实际梯度/曲线共同阅读，避免把类别比变化全算作Focal机制效果。额外视图、EMA和λ的结果也分别保留，不只选有利配置。

本轮不支持真实未标注数据的正负比例假定、不证明真实标签延迟下效果、不提供新独立外部验证。现有观察只能作为方法取舍证据；即使点估计改善，也不升级默认检测器。

## 核验与成本

独立重建日期资格和哈希mask；核对可见类别数、冷启动状态、每轮真实样本/更新数/学习率/λ、损失系数和延训来源；31个神经实例及1个树各重算全部开发分数。神经最大误差{audit['max_full_NN_replay_abs_diff']:.9g}，树误差{audit['tree_replay_abs_diff']:.9g}。因果历史与220维特征的全量源重建继承B已审的**相同字节**，没有把继承证据冒称第二次独立特征重建。

'''+ '\n'.join(cost)+f'''

semi-models.csv给出各条件全部seed的训练循环CPU/墙钟、推理CPU/墙钟和参数；semi-training-curves.csv保留逐轮资源。CPU为线程合计，整进程峰值含数据/先前模型，不是隔离单模型内存。D复用B已构建统计矩阵，构建代价没有变成零；当前批量推理计时也不代表线上单请求p99。GPU/付费模型调用均为0。

本地回归{tests['tests']}项，失败{tests['failures']}、错误{tests['errors']}；XML SHA256 `{digest(tests_xml)}`。不冒充GitHub共享CI通过。来源协议、失败日志和复现命令分别见GATE_D_PROTOCOL.md、FAILURES.md与REPRODUCE.md。

方法定义参考：[Focal Loss](https://arxiv.org/abs/1708.02002)、[Class-Balanced Loss](https://arxiv.org/abs/1901.05555)、[Mean Teacher](https://arxiv.org/abs/1703.01780)。本轮是明确声明的表格数据受控变体，不声称复现这些论文的视觉任务成绩。
'''
    (output/'SEMI_SUPERVISED_RESULTS.md').write_text(text,encoding='utf8')
    os.environ.setdefault('MPLCONFIGDIR',str((experiment_run/'mpl-cache').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(12,6));ax.bar(range(len(rows)),frame.TP,color='#6a92b1')
    for i,row in enumerate(rows):
        if row['seed_TP']:ax.scatter([i]*3,[int(v) for v in row['seed_TP'].split(',')],color='black',s=15)
    ax.set_xticks(range(len(rows)),frame.condition,rotation=50,ha='right',fontsize=8)
    ax.set_ylabel('Captured positives / 365 daily Top10 queues');ax.set_title('Controlled mask on observed development: all registered conditions; no default upgrade')
    fig.tight_layout();fig.savefig(output/'semi-top10.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11,7))
    for ax,name in zip(axes.flat,protocol['diagnostic_conditions']):
        for seed in (42,43,44):
            selected=curve_frame[(curve_frame.condition==name)&(curve_frame.seed==seed)&(curve_frame.phase=='primary')]
            ax.plot(selected.epoch,selected.supervised_mean_loss,alpha=.7)
        diagnostic=curve_frame[(curve_frame.condition==name)&(curve_frame.phase=='fixed_extra_six')]
        ax.plot(diagnostic.epoch,diagnostic.supervised_mean_loss,'k--',label='seed42 fixed extra6')
        ax.set_title(name);ax.set_xlabel('Epoch');ax.set_ylabel('Reference-scaled supervised loss')
    axes.flat[0].legend(fontsize=8);fig.tight_layout();fig.savefig(output/'semi-training.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for name in consistency_names:
        selected=curve_frame[(curve_frame.condition==name)&(curve_frame.phase=='primary')].groupby('epoch').mean(numeric_only=True)
        axes[0].plot(selected.index,selected.unlabeled_mean_KL,label=name)
        axes[1].plot(selected.index,selected.unlabeled_mean_teacher_probability,label=name)
    axes[0].set_title('Unknown-row mean Bernoulli KL');axes[1].set_title('Teacher positive probability on unknown rows')
    for ax in axes:ax.set_xlabel('Fixed epoch');ax.legend(fontsize=7)
    fig.tight_layout();fig.savefig(output/'semi-consistency.png',dpi=160);plt.close(fig)
    result={'status':'Gate_D_controlled_complete_and_independently_verified','main_neural_instances':27,'diagnostic_continuations':4,
        'masked_tree_instances':1,'full_model_replays':32,'models':rows,'channel_metrics':channels,'visibility':visibility,
        'loss_definitions':losses,'consistency_vs_bce_net':deltas,'tests':tests,'tests_xml_sha256':digest(tests_xml),
        'source_report_sha256':digest(experiment_run/'report.json'),'audit_sha256':digest(audit_run/'report.json'),
        'protocol_sha256':digest(experiment_run/'protocol.json'),'resource_receipts':{str(p):digest(p) for p in (Path(training_resources),Path(audit_resources))},
        'pretrained_semantics_studied':False,'real_label_availability_verified':False,'independent_external_test':False,
        'default_detector_changed':False,'final_2019_read':False,'paid_calls':0}
    write_json(output/'semi-results.json',result);return result
