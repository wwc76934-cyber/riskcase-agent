"""Training-only worker; receives masked targets, never hidden or development labels.

This file is preparation until the experiment coordinator verifies Gate B and
freezes the new protocol. No data are loaded and no job is started on import.
"""
from dataclasses import asdict
import copy
import hashlib
import math
from pathlib import Path
import time

import numpy as np
import torch
from threadpoolctl import threadpool_limits

from app.risk.neural_model import seed_training
from app.risk.semantic_detection.common import digest,peak_working_set_bytes,write_json
from .matched import Config
from .semi_supervised import CalibratedLoss,MaskedBroadModel,batch_objective,consistency_schedule,update_teacher


def state_fingerprint(state):
    result=hashlib.sha256()
    for name in sorted(state):
        value=state[name].detach().cpu().contiguous()
        result.update(f'{name}\0{value.dtype}\0{tuple(value.shape)}\0'.encode())
        result.update(value.numpy().tobytes())
    return result.hexdigest()


def fit(frame,statistics,visible_labels,prep,condition,seed,output,*,protocol_sha256,
        resume_checkpoint=None):
    """Exactly12 main epochs or one fixed12->18 own-model diagnosis.

    Statistics are TRAINING rows only. Held-out feature arrays and labels are not
    accepted by this worker. The coordinator owns artifact/partition checks.
    """
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    begin=time.monotonic();begin_cpu=time.process_time()
    raw_labels=np.asarray(visible_labels)
    if raw_labels.shape!=(len(frame),) or not np.isin(raw_labels,[-1,0,1]).all() or len(statistics)!=len(frame) or statistics.ndim!=2:
        raise ValueError('Training-only aligned arrays required')
    labels=raw_labels.astype(np.int8,copy=True)
    if seed not in (42,43,44):raise ValueError('Frozen matched initialization seeds required')
    if set(condition)!={'name','loss','consistency_weight','view_probability'}:
        raise ValueError('Fixed condition fields required')
    if condition['consistency_weight'] not in (0.,.1,.5) or condition['view_probability'] not in (0.,.1):
        raise ValueError('Only declared bounded consistency/view settings allowed')
    criterion=CalibratedLoss(condition['loss'],labels)
    seed_training(seed,2)
    config=Config(representation='learned',include_channel=False,seed=seed,threads=2)
    if prep.config.representation!='learned' or prep.config.include_channel:
        raise ValueError('Ordinary no-channel category preparation required')
    model=MaskedBroadModel(config,prep,statistics.shape[1])
    initial=state_fingerprint(model.state_dict());teacher=copy.deepcopy(model).eval()
    for parameter in teacher.parameters():parameter.requires_grad_(False)
    tensors=prep.transform(frame);target=torch.from_numpy(labels)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=12,eta_min=.00005)
    order_rng=torch.Generator().manual_seed(seed);view_rng=torch.Generator().manual_seed(seed+7103)
    label_sha=hashlib.sha256(labels.tobytes()).hexdigest()
    first,last=1,12;curves=[]
    if resume_checkpoint is not None:
        previous=torch.load(resume_checkpoint,weights_only=True,map_location='cpu')
        if previous['epoch']!=12 or previous['condition']!=condition or previous['seed']!=seed:
            raise ValueError('Only own frozen twelve-epoch representative may resume')
        if previous['masked_label_vector_sha256']!=label_sha or previous['protocol_sha256']!=protocol_sha256 or previous['initial_cold_state_sha256']!=initial:
            raise ValueError('Diagnostic source/mask/initialization/protocol differs')
        if seed!=42 or condition['consistency_weight'] or condition['view_probability']:
            raise ValueError('Diagnosis limited to four unaugmented supervised loss representatives')
        model.load_state_dict(previous['state_dict']);teacher.load_state_dict(previous['teacher_state_dict'])
        optimizer.load_state_dict(previous['optimizer']);scheduler.load_state_dict(previous['scheduler'])
        torch.set_rng_state(previous['torch_rng']);order_rng.set_state(previous['order_rng']);view_rng.set_state(previous['view_rng'])
        if any(abs(g['lr']-.00005)>1e-12 for g in optimizer.param_groups):raise ValueError('Diagnosis uses the fixed final learning rate')
        first,last=13,18
    preparation_seconds=time.monotonic()-begin;preparation_cpu=time.process_time()-begin_cpu
    loop_start=time.monotonic();loop_cpu=time.process_time()
    with threadpool_limits(limits=2):
        for epoch in range(first,last+1):
            model.train();teacher.eval();order=torch.randperm(len(frame),generator=order_rng)
            epoch_start=time.monotonic();epoch_cpu=time.process_time()
            learning_rate=float(optimizer.param_groups[0]['lr'])
            weight=consistency_schedule(condition['consistency_weight'],epoch)
            accum={key:0. for key in ('scaled_loss_sum','raw_loss_sum','positive_loss_sum','negative_loss_sum',
                'positive_logit_gradient_sum','negative_logit_gradient_sum','positive_batch_reduced_gradient_sum',
                'negative_batch_reduced_gradient_sum','unknown_KL_sum','teacher_unknown_positive_mass','student_unknown_positive_mass','batch_objective_sum')}
            positive_count=negative_count=unknown_count=steps=clipped=0;max_norm=0.
            for start in range(0,len(frame),4096):
                positions=order[start:start+4096];batch_y=target[positions]
                numeric=tuple(value[positions] for value in tensors)
                stat=torch.from_numpy(np.array(statistics[positions.numpy()],dtype=np.float32,copy=True))
                optimizer.zero_grad(set_to_none=True)
                logits=model(*numeric,stat,view_probability=condition['view_probability'],view_generator=view_rng)
                # Kept for every condition, including weight0, to match the teacher computation budget.
                with torch.no_grad():teacher_logits=teacher(*numeric,stat)
                loss,supervised,consistency=batch_objective(logits,batch_y,teacher_logits,criterion,weight)
                if not torch.isfinite(loss):raise ValueError('Nonfinite controlled semi-supervised objective')
                loss.backward();norm=float(torch.nn.utils.clip_grad_norm_(model.parameters(),5.,error_if_nonfinite=True))
                max_norm=max(max_norm,norm);clipped+=int(norm>5.)
                optimizer.step();update_teacher(teacher,model,.99)
                known=batch_y>=0;unknown=~known;known_y=batch_y[known]
                nknown=int(known.sum());nunknown=int(unknown.sum())
                with torch.no_grad():
                    values=criterion.values(logits[known],known_y)
                    raw=criterion.values(logits[known],known_y,scaled=False)
                    gradients=criterion.logit_gradient_magnitude(logits[known],known_y)
                    positive=known_y==1;negative=known_y==0
                    accum['scaled_loss_sum']+=float(values.sum());accum['raw_loss_sum']+=float(raw.sum())
                    for tag,mask in (('positive',positive),('negative',negative)):
                        accum[tag+'_loss_sum']+=float(values[mask].sum())
                        gradient=float(gradients[mask].sum());accum[tag+'_logit_gradient_sum']+=gradient
                        accum[tag+'_batch_reduced_gradient_sum']+=gradient/max(nknown,1)
                    accum['unknown_KL_sum']+=float(consistency)*nunknown
                    accum['teacher_unknown_positive_mass']+=float(teacher_logits[unknown].sigmoid().clamp(1e-6,1-1e-6).sum())
                    accum['student_unknown_positive_mass']+=float(logits[unknown].sigmoid().sum())
                    accum['batch_objective_sum']+=float(loss)
                positive_count+=int(positive.sum());negative_count+=int(negative.sum());unknown_count+=nunknown;steps+=1
            if positive_count!=criterion.positive or negative_count!=criterion.negative or unknown_count!=int((labels<0).sum()):
                raise ValueError('Actual visible class exposure differs from frozen mask')
            known_total=positive_count+negative_count
            row={'epoch':epoch,'learning_rate':learning_rate,'consistency_weight':weight,
                'rows':len(frame),'visible_positive_rows':positive_count,'visible_negative_rows':negative_count,
                'unlabeled_rows':unknown_count,'optimizer_steps':steps,
                'supervised_mean_loss':accum['scaled_loss_sum']/known_total,
                'raw_unscaled_supervised_mean_loss':accum['raw_loss_sum']/known_total,
                'positive_mean_scaled_loss':accum['positive_loss_sum']/positive_count,
                'negative_mean_scaled_loss':accum['negative_loss_sum']/negative_count,
                'positive_mean_abs_logit_derivative_unreduced':accum['positive_logit_gradient_sum']/positive_count,
                'negative_mean_abs_logit_derivative_unreduced':accum['negative_logit_gradient_sum']/negative_count,
                'positive_batch_reduced_abs_logit_derivative_total':accum['positive_batch_reduced_gradient_sum'],
                'negative_batch_reduced_abs_logit_derivative_total':accum['negative_batch_reduced_gradient_sum'],
                'unlabeled_mean_KL':accum['unknown_KL_sum']/max(unknown_count,1),
                'unlabeled_mean_teacher_probability':accum['teacher_unknown_positive_mass']/max(unknown_count,1),
                'unlabeled_mean_student_probability':accum['student_unknown_positive_mass']/max(unknown_count,1),
                'mean_batch_objective':accum['batch_objective_sum']/steps,'max_unclipped_parameter_gradient_norm':max_norm,
                'clipped_step_fraction':clipped/steps,'epoch_wall_seconds':time.monotonic()-epoch_start,
                'epoch_process_CPU_seconds':time.process_time()-epoch_cpu}
            curves.append(row)
            if epoch<=12:scheduler.step()
            checkpoint={'epoch':epoch,'condition':condition,'seed':seed,'config':asdict(config),'dimensions':statistics.shape[1],
                'state_dict':model.state_dict(),'teacher_state_dict':teacher.state_dict(),'optimizer':optimizer.state_dict(),
                'scheduler':scheduler.state_dict(),'torch_rng':torch.get_rng_state(),'order_rng':order_rng.get_state(),'view_rng':view_rng.get_state(),
                'masked_label_vector_sha256':label_sha,'protocol_sha256':protocol_sha256,'initial_cold_state_sha256':initial}
            torch.save(checkpoint,output/f'epoch-{epoch:02d}.pt')
            write_json(output/'curves.json',curves)
            print(f"controlled-semi {condition['name']} seed{seed} epoch{epoch} known+={positive_count} loss={row['supervised_mean_loss']:.6f} KL={row['unlabeled_mean_KL']:.6f}",flush=True)
    torch.save({'config':asdict(config),'dimensions':statistics.shape[1],'state_dict':model.state_dict(),
                'condition':condition,'seed':seed,'final_epoch':last,'masked_label_vector_sha256':label_sha,
                'protocol_sha256':protocol_sha256,'initial_cold_state_sha256':initial},output/'model.pt')
    record={'status':'training_worker_complete','condition':condition,'seed':seed,'first_epoch':first,'last_epoch':last,
        'curves':curves,'loss':criterion.metadata(),'masked_label_vector_sha256':label_sha,
        'initial_cold_state_sha256':initial,'protocol_sha256':protocol_sha256,
        'student_parameters':sum(p.numel() for p in model.parameters()),'teacher_trainable_parameters':sum(p.numel() for p in teacher.parameters() if p.requires_grad),
        'preparation_wall_seconds':preparation_seconds,'preparation_CPU_seconds':preparation_cpu,
        'training_loop_wall_seconds':time.monotonic()-loop_start,'training_loop_CPU_seconds':time.process_time()-loop_cpu,
        'total_worker_wall_seconds':time.monotonic()-begin,'total_worker_CPU_seconds':time.process_time()-begin_cpu,
        'whole_process_peak_bytes':peak_working_set_bytes(),'model_sha256':digest(output/'model.pt'),
        'diagnostic_resume':str(resume_checkpoint) if resume_checkpoint else None,
        'diagnostic_resume_sha256':digest(resume_checkpoint) if resume_checkpoint else None,
        'hidden_or_development_labels_accepted_by_worker':False}
    write_json(output/'training.json',record)
    return model,record
