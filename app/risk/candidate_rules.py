"""Restricted candidate rules and incremental retrospective review evaluation."""

from collections import Counter
import operator
import re


FIELDS = {"amount_cents","count_1h","count_24h","count_7d","count_30d",
    "history_30d_complete","has_history_30d","merchant_seen_30d"}
FLAGS = {"history_30d_complete","has_history_30d","merchant_seen_30d"}
OPS = {"gt":operator.gt,"ge":operator.ge,"eq":operator.eq}


def compile_candidate(spec):
    if not isinstance(spec,dict) or set(spec)!={"version","name","all"} or type(spec["version"]) is not int or spec["version"]!=1:
        raise ValueError("Restricted rule version/schema required")
    if not isinstance(spec["name"],str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",spec["name"]):
        raise ValueError("Simple rule identifier required")
    if not isinstance(spec["all"],list) or not 1<=len(spec["all"])<=4: raise ValueError("One to four AND conditions required")
    conditions = []
    for term in spec["all"]:
        if not isinstance(term,dict) or set(term)!={"field","op","value"}: raise ValueError("Invalid condition schema")
        field,op,value = term["field"],term["op"],term["value"]
        if not isinstance(field,str) or field not in FIELDS or not isinstance(op,str) or op not in OPS:
            raise ValueError("Field/operator not allowed")
        if type(value) is not int or not -10**12<=value<=10**12: raise ValueError("Bounded integer threshold required")
        if field in FLAGS and (op!="eq" or value not in (0,1)): raise ValueError("Flags require eq 0 or 1")
        if field.startswith("count_") and value<0: raise ValueError("Nonnegative count threshold required")
        conditions.append((field,OPS[op],value))
    def matches(row):
        # Validate all inputs before evaluating; a false first condition cannot hide bad data.
        for field,_,_ in conditions:
            if type(row.get(field)) is not int: raise ValueError("Missing/invalid feature")
            if field in FLAGS and row[field] not in (0,1): raise ValueError("Invalid flag")
            if field.startswith("count_") and row[field]<0: raise ValueError("Invalid count")
        return all(compare(row[field],value) for field,compare,value in conditions)
    return matches


def replay_candidate(records,baseline_ids,spec):
    matches = compile_candidate(spec)
    baseline_ids = set(baseline_ids)
    seen,c = set(),Counter()
    for row in records:
        if row.get("split")!="validation" or type(row.get("is_fraud")) is not bool:
            raise ValueError("Development validation and explicit binary evaluation labels only")
        identity = row["transaction_id"]
        if identity in seen: raise ValueError("Duplicate transaction in replay")
        seen.add(identity)
        candidate,base,outcome = matches(row),identity in baseline_ids,row["is_fraud"]
        c["rows"]+=1; c["positives"]+=outcome
        for group,hit in (("baseline",base),("candidate",candidate),("union",base or candidate),("added",candidate and not base)):
            c[group+"_alerts"]+=hit
            c[group+"_tp"]+=hit and outcome
        c["overlap_alerts"]+=base and candidate
    if not baseline_ids<=seen: raise ValueError("Baseline contains IDs outside replay")
    result = {"rows":c["rows"],"positives":c["positives"],"overlap_alerts":c["overlap_alerts"]}
    for group in ("baseline","candidate","union","added"):
        alerts,tp = c[group+"_alerts"],c[group+"_tp"]
        result[group] = {"alerts":alerts,"true_positives":tp,"false_positives":alerts-tp,
            "precision":tp/alerts if alerts else None,"recall":tp/c["positives"] if c["positives"] else None}
    return result
