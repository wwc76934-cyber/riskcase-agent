"""Do not promote a category-prior point estimate to new-business-text evidence."""
import os
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.data import load_data
from .semi_experiment import read


def run(source_root,experiment_run,audit_run,parent,output,training_resources,audit_resources):
    experiment_run,audit_run,parent,output=map(Path,(experiment_run,audit_run,parent,output))
    result,audit,protocol=read(experiment_run/'report.json'),read(audit_run/'report.json'),read(experiment_run/'protocol.json')
    if audit['status']!='low_label_prior_supplement_independently_verified' or audit['full_new_model_replays']!=9 or audit['report_sha256']!=digest(experiment_run/'report.json'):
        raise ValueError('Complete independent supplement replay required')
    (_,_,_),(VX,vy,vmeta)=load_data(source_root)[0]
    original=read(parent/'report.json');rows=[];channels=[];curves=[];all_scores={}
    for name in ['ordinary_learned32']+protocol['representations']:
        if name=='ordinary_learned32':
            records=[read(parent/'bce'/f'seed-{s}'/'training.json') for s in (42,43,44)]
            evaluations=[original['seed_evaluations'][f'bce/seed-{s}'] for s in (42,43,44)]
            score=np.load(parent/'bce/ensemble.npy',allow_pickle=False)
        else:
            records=[result['records'][f'{name}/seed-{s}'] for s in (42,43,44)]
            evaluations=[result['evaluations'][f'{name}/seed-{s}'] for s in (42,43,44)]
            score=np.load(experiment_run/name/'ensemble.npy',allow_pickle=False)
        metrics=result['ensembles'][name];all_scores[name]=score
        trainable=records[0].get('initialization',{}).get('trainable_parameters',records[0]['student_parameters'])
        rows.append({'representation':name,'TP':metrics['tp_daily10'],'P10':metrics['precision'],'R10':metrics['recall'],'AP':metrics['ap'],
            'seed_TP':','.join(str(e['metrics']['tp_daily10']) for e in evaluations),'total_parameters':records[0]['student_parameters'],
            'trainable_parameters':trainable,'Online_slots':metrics['channel']['Online Transaction']['reviewed'],
            'train_CPU_seconds':sum(r['training_loop_CPU_seconds'] for r in records),'train_wall_seconds':sum(r['training_loop_wall_seconds'] for r in records),
            'inference_wall_seconds':sum(e['inference_wall_seconds'] for e in evaluations)})
        for record in records:curves.extend({'representation':name,'seed':record['seed'],**c} for c in record['curves'])
        for channel,values in metrics['channel'].items():
            selected=VX.channel.to_numpy()==channel
            channels.append({'representation':name,'channel':channel,'slots':values['reviewed'],'TP':values['tp'],'FP':values['reviewed']-values['tp'],
                'within_channel_AP':float(average_precision_score(vy[selected],score[selected])) if vy[selected].sum() else None})
    table=['| 表示 | 三seed TP | 集成TP | AP | P@10 | R@10 | 可训练参数 | Online名额 |','|---|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:table.append(f"| {r['representation']} | {r['seed_TP']} | {r['TP']} | {r['AP']:.6f} | {r['P10']:.4f} | {r['R10']:.4f} | {r['trainable_parameters']} | {r['Online_slots']} |")
    comparisons=['| Qwen相对 | 新增正例 | 挤出正例 | 净变化 | 配对日95% | 7日区块95% |','|---|---:|---:|---:|---|---|']
    positive_all=True
    for name,value in audit['comparisons'].items():
        interval=value['block_sensitivity']['paired_circular_block_95']
        positive_all &= value['net']>0 and interval[0]>0
        comparisons.append(f"| {name} | {value['added_positive']} | {value['removed_positive']} | {value['net']:+d} | {value['paired_day_bootstrap_95']} | {interval} |")
    ordinary=[e['metrics']['tp_daily10'] for e in [original['seed_evaluations'][f'bce/seed-{s}'] for s in (42,43,44)]]
    qwen=[result['evaluations'][f'qwen_frozen32/seed-{s}']['metrics']['tp_daily10'] for s in (42,43,44)]
    seed_deltas=[a-b for a,b in zip(qwen,ordinary)];positive_all &= min(seed_deltas)>0
    conclusion=("本受限开发比较达到预先要求的正面候选条件，但仍不是新独立验证、丰富业务文本或语言知识的普遍独立价值证明。" if positive_all else
        "本轮未获得同时超过普通Embedding、同维随机向量及同文本TF-IDF的稳定增量证据；保留负结果，不扩大模型或继续IBM语义搜索。")
    resources={name:read(path) for name,path in [('training',training_resources),('audit',audit_resources)]}
    if any(r['status']!='completed' for r in resources.values()):raise ValueError('Successful resource receipts required')
    output.mkdir(parents=True,exist_ok=True)
    frame=pd.DataFrame(rows);frame.to_csv(output/'low-label-prior-models.csv',index=False)
    pd.DataFrame(channels).to_csv(output/'low-label-prior-channels.csv',index=False);pd.DataFrame(curves).to_csv(output/'low-label-prior-curves.csv',index=False)
    write_json(output/'low-label-prior-comparisons.json',audit['comparisons'])
    text=f'''# D补充｜低标签类别先验结果

{conclusion}

原D32实例保持原状。本补充在原D开发评分产生前公开登记，再于原32实例独立核验后训练9个新实例。三种冻结向量各seed42/43/44、12轮；普通可训练Embedding直接引用原D bce三个固定模型。相同83,057可见标签、91正例、相同训练X预处理/220维历史统计、相同BCE20/样本次序/下游初始化/预算，无一致性或额外视图。未复用完整监督权重。

## 全部结果

'''+ '\n'.join(table)+'''

![固定类别先验比较](low-label-prior.png)

Qwen只复用A已审的无channel原描述缓存，前32维L2后冻结；没有新LLM生成、编码、下载或更大模型。TF-IDF使用完全相同文本、训练类别词表的1/2gram与32维SVD；随机向量固定seed20261102。未知类别行均为零。三个冻结条件可训练参数相同，普通Embedding略多；全部非auxiliary权重从同一seed的普通冷初始化复制，冻结表在训练后仍逐字等于输入表。

## 新增、挤出与不确定性

'''+ '\n'.join(comparisons)+f'''

Qwen相对普通Embedding的同seed TP差为{seed_deltas}。全部条件和三个seed列出，不按结果选模板、维度或seed。逐日与区块区间是已观察开发期描述区间，未校正所有搜索；同一个mask也不代表标注策略抽样稳定性。

## 证据与边界

9个新实例各重算全部422,088个开发分数，最大误差{audit['max_full_replay_abs_diff']:.9g}；原普通模型由D32独立核验绑定。Qwen/随机表从原始cache/固定RNG重建，TF-IDF从同一文本独立重算；核对冻结表、可训练容量、相同冷初始化、每轮真实91正例与学习率/步数。

MCC描述是既有类别ID的确定函数，没有新增逐交易业务事实。此处检验91可见正例时的类别先验，不能替代C2丰富业务文本研究。现代字典与预训练模型用于历史合成数据是回顾性比较，不证明当年线上文本可得。7日标签延迟是假设，原available_at未知，真实未标注未当正常。2018已观察，2019不评测，没有新独立测试。

全部训练CPU、墙钟、推理与参数在low-label-prior-models.csv；整进程资源回执保留。原D bce为路径匹配也有无梯度教师计算，不是最低成本监督实现。输出只用于研究，默认仍为neural_v2_ensemble，不进入调查Agent合同。

模型正面候选条件要求：相对三个控制的集成净增均为正且7日区块描述区间下界>0，并且Qwen相对普通模型三个同seed差均>0。即使达到，仍需新的独立来源/协议后验证才能考虑升级；不声称完成普遍的LLM性能提升。
'''
    (output/'LOW_LABEL_PRIOR_RESULTS.md').write_text(text,encoding='utf8')
    os.environ.setdefault('MPLCONFIGDIR',str((experiment_run/'mpl-cache').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(9,5));ax.bar(frame.representation,frame.TP,color='#789eb8')
    for i,row in enumerate(rows):ax.scatter([i]*3,[int(v) for v in row['seed_TP'].split(',')],color='black',s=18)
    ax.set_ylabel('Captured positives / 365 daily Top10 queues');ax.set_title('Fixed low-label category priors; observed development')
    ax.tick_params(axis='x',rotation=20);fig.tight_layout();fig.savefig(output/'low-label-prior.png',dpi=160);plt.close(fig)
    record={'status':'low_label_prior_complete_and_independently_verified','new_model_replays':9,'models':rows,
        'qwen_vs_ordinary_same_seed_TP_differences':seed_deltas,'all_prespecified_positive_conditions_met':bool(positive_all),
        'source_report_sha256':digest(experiment_run/'report.json'),'audit_sha256':digest(audit_run/'report.json'),
        'protocol_sha256':digest(experiment_run/'protocol.json'),'registration_commit':protocol['registration_commit'],
        'resources':resources,'default_detector_changed':False,'independent_external_test':False,'new_business_text_data':False,'paid_calls':0}
    write_json(output/'low-label-prior-results.json',record);return record
