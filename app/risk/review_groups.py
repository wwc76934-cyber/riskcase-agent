"""Navigation groups preserve individual alert cutoffs, tasks and approvals."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import re


def group_id(source_sha, model_sha, user, day):
    if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{64}', x) for x in (source_sha, model_sha)):
        raise ValueError('Source and model fingerprints required')
    if not isinstance(user, str) or not user or len(user)>120:
        raise ValueError('Source user identity required')
    if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
        raise ValueError('Canonical source-local date required')
    return 'group-' + hashlib.sha256(json.dumps([source_sha,model_sha,user,day], separators=(',',':')).encode()).hexdigest()[:32]


def group_alerts(rows, source_sha, model_sha):
    """Input is an already audited selected queue; never replenish or rerank it."""
    groups=defaultdict(list)
    for row in rows:
        stamp=datetime.fromisoformat(row['event_time'])
        if stamp.tzinfo is not None:raise ValueError('Source-local time required')
        day=stamp.date().isoformat()
        key=group_id(source_sha,model_sha,row['user_id'],day)
        groups[(day,key)].append(deepcopy(row))
    result=[]
    for (day,key),members in groups.items():
        members.sort(key=lambda r:r['day_rank'])
        result.append(dict(group_id=key,day=day,user_id=members[0]['user_id'],
                           queue_available_at=(datetime.fromisoformat(day)+timedelta(days=1)).isoformat(),
                           first_day_rank=members[0]['day_rank'],members=members))
    return sorted(result,key=lambda g:(g['day'],g['first_day_rank'],g['group_id']))
