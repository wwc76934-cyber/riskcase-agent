"""Label-free end-of-day capacity allocation, then paired outcome accounting."""

from collections import defaultdict
import hashlib
import math


POLICIES=("hgb","rule_only","reserve_20pct","rule_first")
TIE_SEED="riskcase-review-ties-v1"


def tie_key(identity):
    return hashlib.sha256(f"{TIE_SEED}\0{identity}".encode()).hexdigest()


def allocate_day(rows,budget,policy):
    if type(budget) is not int or not 1<=budget<=100 or policy not in POLICIES:
        raise ValueError("Bounded budget and supported policy required")
    ids=set();days=set()
    for row in rows:
        if "is_fraud" in row or "label" in row: raise ValueError("Outcomes must not enter allocation")
        identity=row["transaction_id"]
        if not isinstance(identity,str) or not identity or identity in ids: raise ValueError("Unique string transaction IDs required")
        if type(row["amount_cents"]) is not int or type(row["rule_match"]) is not bool: raise ValueError("Integer amount and explicit rule flag required")
        score=row["hgb_score"]
        if isinstance(score,bool) or not isinstance(score,(float,int)) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError("Finite bounded model score required")
        if row["tie"]!=tie_key(identity): raise ValueError("Tie key differs from frozen baseline")
        ids.add(identity);days.add(row["day"])
    if len(days)>1: raise ValueError("Daily capacity cannot transfer between days")
    model_key=lambda row:(row["hgb_score"],row["tie"],row["transaction_id"])
    ranked=sorted(rows,key=model_key,reverse=True)
    if policy=="hgb": return ranked[:budget]
    matched=[row for row in ranked if row["rule_match"]]
    if policy=="rule_only":
        return sorted(matched,key=lambda row:(row["amount_cents"],row["tie"],row["transaction_id"]),reverse=True)[:budget]
    if policy=="rule_first": return (matched+[row for row in ranked if not row["rule_match"]])[:budget]
    reserved=matched[:budget//5]
    selected={row["transaction_id"] for row in reserved}
    return reserved+[row for row in ranked if row["transaction_id"] not in selected][:budget-len(reserved)]


def outcome_metrics(population,selected,baseline,labels,*,budget):
    """Called only after the label-free review lists have been frozen."""
    if type(budget) is not int or not 1<=budget<=100: raise ValueError("Positive daily capacity required")
    universe=set(population);selected=set(selected);baseline=set(baseline)
    if set(labels)!=universe or not selected<=universe or not baseline<=universe or any(type(v) is not bool for v in labels.values()):
        raise ValueError("Complete binary outcomes and in-population review lists required")
    positives=sum(labels.values());tp=sum(labels[x] for x in selected)
    days=defaultdict(int)
    for identity in selected: days[population[identity]]+=1
    if any(count>budget for count in days.values()): raise ValueError("Daily capacity exceeded")
    gained,displaced=selected-baseline,baseline-selected
    gained_tp=sum(labels[x] for x in gained);lost_tp=sum(labels[x] for x in displaced)
    return {"rows":len(universe),"positives":positives,"reviews":len(selected),"true_positives":tp,"false_positives":len(selected)-tp,
        "false_negatives":positives-tp,"precision":tp/len(selected) if selected else None,"recall":tp/positives if positives else None,
        "days":len(set(population.values())),"capacity":len(set(population.values()))*budget,
        "unused_capacity":len(set(population.values()))*budget-len(selected),
        "added_reviews":len(gained),"displaced_reviews":len(displaced),"gained_tp":gained_tp,"lost_tp":lost_tp,"net_tp":gained_tp-lost_tp,
        "gained_fp":len(gained)-gained_tp,"displaced_fp":len(displaced)-lost_tp,"review_delta":len(selected)-len(baseline)}
