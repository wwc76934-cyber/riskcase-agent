"""Dimension and industry geometry probes; category labels only, no fraud labels."""
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import normalize

from app.risk.semantic_detection.common import digest,write_json
from app.risk.semantic_detection.semantics import UNKNOWN
from .representations import industry_title


def run(bank,glossary_path,output):
    bank,output=Path(bank),Path(output);output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((bank/'manifest.json').read_text(encoding='utf8'))
    glossary=json.loads(Path(glossary_path).read_text(encoding='utf8'))
    spec,=[s for s in manifest['specifications'] if s['name']=='channel-original']
    vectors=np.load(bank/'channel-original.npy',allow_pickle=False)
    if digest(bank/'channel-original.npy')!=spec['vectors_sha256']:
        raise ValueError('Original full vector bank changed')
    rows=[spec['texts'].index(t) for t in sorted(set(spec['texts']))]
    keys=[spec['keys'][i] for i in rows]
    texts=[spec['texts'][i] for i in rows]
    mcc=np.array([m for m,_ in keys]);channel=np.array([c for _,c in keys])
    valid=channel!=UNKNOWN
    industry=[]
    groups={'Airlines, passenger air transportation','Car rental agencies','Lodging, hotels and motels'}
    for m,_ in keys:
        title=glossary['entries'].get(m,{}).get('title',UNKNOWN)
        coarse=industry_title(m,title)
        industry.append(coarse if coarse in groups else 'not_in_three_brand_sectors')
    industry=np.array(industry);brand=industry!='not_in_three_brand_sectors'
    results={}
    for dimension in (32,128,256):
        real=normalize(vectors[rows,:dimension])
        random=normalize(np.random.default_rng(731).normal(size=real.shape))
        modes={}
        for name,table in [('qwen',real),('random',random)]:
            cosine=table@table.T;np.fill_diagonal(cosine,-np.inf)
            nearest=np.argsort(-cosine,axis=1)[:,:5]
            accuracy=[];fold_records=[]
            folds=StratifiedGroupKFold(n_splits=5,shuffle=True,random_state=731)
            for a,b in folds.split(table[valid],channel[valid],mcc[valid]):
                selected=np.flatnonzero(valid);a,b=selected[a],selected[b]
                overlap=len(set(mcc[a])&set(mcc[b]))
                if overlap:raise ValueError('Repeated MCC in held-out diagnostic fold')
                classifier=LogisticRegression(C=1.,max_iter=1000).fit(table[a],channel[a])
                accuracy.append(float(balanced_accuracy_score(channel[b],classifier.predict(table[b]))))
                fold_records.append({'training_rows':len(a),'validation_rows':len(b),'MCC_overlap':overlap})
            # Harder industry test: same channel, different MCC, only three verified brand sectors.
            group_nn=[];same_values=[];different_values=[]
            for i in np.flatnonzero(brand&valid):
                eligible=np.flatnonzero(brand&valid&(channel==channel[i])&(mcc!=mcc[i]))
                if len(eligible):
                    j=eligible[np.argmax(cosine[i,eligible])]
                    group_nn.append(bool(industry[i]==industry[j]))
                    a=eligible[industry[eligible]==industry[i]];b=eligible[industry[eligible]!=industry[i]]
                    same_values.extend(cosine[i,a].tolist());different_values.extend(cosine[i,b].tolist())
            modes[name]={'nearest1_same_mcc_fraction':float(np.mean(mcc==mcc[nearest[:,0]])),
                         'nearest1_same_channel_fraction':float(np.mean(channel==channel[nearest[:,0]])),
                         'channel_balanced_accuracy_MCC_grouped_CV':accuracy,'fold_checks':fold_records,
                         'industry_same_channel_different_mcc_NN_accuracy':float(np.mean(group_nn)) if group_nn else None,
                         'industry_probe_rows':len(group_nn),
                         'industry_within_mean_cosine':float(np.mean(same_values)) if same_values else None,
                         'industry_between_mean_cosine':float(np.mean(different_values)) if different_values else None,
                         'nearest5_examples':[{'key':keys[i],'text':texts[i],
                            'neighbors':[{'key':keys[j],'text':texts[j],'cosine':float(cosine[i,j])} for j in nearest[i]]}
                            for i in range(len(keys))] if name=='qwen' else None}
        results[str(dimension)]=modes
    # Exact classifier-independent comparison of old broad endpoints with corrected rules.
    equivalence=[]
    for s in manifest['specifications']:
        if s['mode']!='industry':continue
        from .representations import texts_for
        equivalence.append({'name':s['name'],'actual_texts_equal':texts_for(s['keys'],glossary,channel=s['channel'],mode='industry')==s['texts']})
    if not all(x['actual_texts_equal'] for x in equivalence):
        raise ValueError('Industry source correction changes observed text; bank cannot be reused')
    report={'status':'A3_category_geometry_complete','unique_original_texts':len(keys),'dimensions':results,
            'industry_scope':'Only three verified brand sectors; excludes same MCC and matches channel; not a full industry taxonomy benchmark',
            'industry_source_correction_equivalence':equivalence,'bank_manifest_sha256':digest(bank/'manifest.json'),
            'fraud_labels_used':False,'transaction_rows_used':False,'paid_calls':0,
            'limits':['Category geometry is not fraud performance','Fold spread is descriptive, not an independent confidence interval',
                      'Industry probe has small category counts and a declared limited taxonomy']}
    write_json(output/'report.json',report)
    return report
