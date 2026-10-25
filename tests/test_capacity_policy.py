from copy import deepcopy
import json
import sqlite3

import pytest

from app.risk.capacity_policy import POLICIES,allocate_day,outcome_metrics,tie_key
from scripts.verify_capacity_experiment import sql_metrics,sql_selection


def rows():
    return [{"transaction_id":str(i),"day":"2018-01-01","event_time":"2018-01-01T12:00:00","hgb_score":1-i/20,
        "amount_cents":i*100,"rule_match":i>=5,"tie":tie_key(str(i))} for i in range(10)]


@pytest.mark.parametrize("budget",[5,10,20])
@pytest.mark.parametrize("policy",POLICIES)
def test_python_ranking_matches_independent_sql_and_is_order_invariant(budget,policy):
    data=rows()
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE population(transaction_id TEXT,day TEXT,hgb_score REAL,amount_cents INTEGER,rule_match INTEGER,tie TEXT)")
        db.executemany("INSERT INTO population VALUES (?,?,?,?,?,?)",[(r["transaction_id"],r["day"],r["hgb_score"],r["amount_cents"],int(r["rule_match"]),r["tie"]) for r in data])
        selected=allocate_day(data,budget,policy)
        expected={(r["transaction_id"],r["day"],i) for i,r in enumerate(selected,1)}
        assert sql_selection(db,budget,policy)==expected
    assert selected==allocate_day(list(reversed(data)),budget,policy)
    assert len(selected)<=budget and len({r["transaction_id"] for r in selected})==len(selected)


def test_reserve_can_displace_a_baseline_review_without_exceeding_capacity():
    data=rows()
    assert [r["transaction_id"] for r in allocate_day(data,5,"hgb")]==["0","1","2","3","4"]
    assert [r["transaction_id"] for r in allocate_day(data,5,"reserve_20pct")]==["5","0","1","2","3"]
    assert [r["transaction_id"] for r in allocate_day(data,5,"rule_first")]==["5","6","7","8","9"]
    assert [r["transaction_id"] for r in allocate_day(data,5,"rule_only")]==["9","8","7","6","5"]


def test_rule_shortage_fills_from_hgb_only_for_hybrid_policies():
    data=rows()
    for r in data: r["rule_match"]=False
    assert allocate_day(data,5,"rule_only")==[]
    for policy in ("reserve_20pct","rule_first"): assert allocate_day(data,5,policy)==allocate_day(data,5,"hgb")


@pytest.mark.parametrize("mutation",["labels","duplicate","nan","bool_score","wrong_tie","other_day"])
def test_no_label_leak_or_bad_ranking_input(mutation):
    data=rows()
    if mutation=="labels": data[0]["is_fraud"]=True
    if mutation=="duplicate": data.append(deepcopy(data[0]))
    if mutation=="nan": data[0]["hgb_score"]=float("nan")
    if mutation=="bool_score": data[0]["hgb_score"]=True
    if mutation=="wrong_tie": data[0]["tie"]="wrong"
    if mutation=="other_day": data[0]["day"]="2018-01-02"
    with pytest.raises(ValueError): allocate_day(data,5,"hgb")


def test_equal_scores_use_original_seeded_baseline_ties():
    data=rows()
    for r in data:r["hgb_score"]=0.5
    selected=allocate_day(data,5,"hgb")
    assert [r["tie"] for r in selected]==sorted((r["tie"] for r in data),reverse=True)[:5]


@pytest.mark.parametrize("budget,policy",[(True,"hgb"),(0,"hgb"),(101,"hgb"),(10,"arbitrary")])
def test_capacity_contract(budget,policy):
    with pytest.raises(ValueError): allocate_day(rows(),budget,policy)


def test_gained_and_lost_frauds_are_both_counted():
    population={"a":"2018-01-01","b":"2018-01-01","c":"2018-01-01"}
    labels={"a":True,"b":False,"c":True}
    result=outcome_metrics(population,{"b","c"},{"a","b"},labels,budget=2)
    assert result["gained_tp"]==1 and result["lost_tp"]==1 and result["net_tp"]==0
    assert result["reviews"]==2 and result["review_delta"]==0 and result["unused_capacity"]==0
    assert result["precision"]==0.5 and result["recall"]==0.5


def test_capacity_cannot_be_pooled_across_days():
    pop={"a":"2018-01-01","b":"2018-01-01","c":"2018-01-02"}
    with pytest.raises(ValueError): outcome_metrics(pop,{"a","b"},set(),{x:False for x in pop},budget=1)


def test_no_positive_or_empty_review_metrics_remain_explicit():
    result=outcome_metrics({"a":"2018-01-01"},set(),set(),{"a":False},budget=5)
    assert result["precision"] is None and result["recall"] is None and result["unused_capacity"]==5


@pytest.mark.parametrize("labels",[{"a":1},{}, {"a":False,"b":True}])
def test_outcome_join_must_be_complete_and_boolean(labels):
    with pytest.raises(ValueError): outcome_metrics({"a":"2018-01-01"},set(),set(),labels,budget=1)


def test_independent_sql_paired_metrics_matches_python():
    data=rows();pop={r["transaction_id"]:r["day"] for r in data};labels={str(i):i in (0,5,9) for i in range(10)}
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE population(transaction_id TEXT,day TEXT)")
        db.executemany("INSERT INTO population VALUES (?,?)",list(pop.items()))
        db.execute("ATTACH DATABASE ':memory:' AS outcomes");db.execute("CREATE TABLE outcomes.labels(transaction_id TEXT,is_fraud INTEGER,split TEXT)")
        db.executemany("INSERT INTO outcomes.labels VALUES (?,?,'validation')",[(i,int(y)) for i,y in labels.items()])
        db.execute("ATTACH DATABASE ':memory:' AS plans");db.execute("CREATE TABLE plans.reviews(transaction_id TEXT,budget INTEGER,policy TEXT)")
        selected={r["transaction_id"] for r in allocate_day(data,5,"reserve_20pct")};base={r["transaction_id"] for r in allocate_day(data,5,"hgb")}
        db.executemany("INSERT INTO plans.reviews VALUES (?,5,?)",[(i,p) for p,ids in (("hgb",base),("reserve_20pct",selected)) for i in ids])
        assert sql_metrics(db,5,"reserve_20pct")==outcome_metrics(pop,selected,base,labels,budget=5)
        assert sql_metrics(db,5,"reserve_20pct","2018-01")==outcome_metrics(pop,selected,base,labels,budget=5)
