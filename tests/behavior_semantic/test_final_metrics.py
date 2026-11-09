import importlib.util
from pathlib import Path
import numpy as np
from sklearn.metrics import average_precision_score


def test_independent_AP_matches_tie_aware_reference():
    source=Path(__file__).resolve().parents[2]/'scripts/behavior_v3/final_evaluate.py'
    spec=importlib.util.spec_from_file_location('track_a_final_metrics_fixture',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    y=np.array([0,1,0,1,1,0,0,1]);scores=np.array([.1,.1,.5,.5,.5,.8,.9,.9])
    assert abs(module.manual_ap(y,scores)-average_precision_score(y,scores))<1e-12


def test_cluster_weighted_AP_matches_explicit_resampled_rows():
    source=Path(__file__).resolve().parents[2]/'scripts/behavior_v3/analyze_development.py'
    spec=importlib.util.spec_from_file_location('track_a_bootstrap_fixture',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    y=np.array([0,1,0,1,1,0]);scores=np.array([.1,.1,.5,.5,.8,.8]);weights=np.array([0,2,1,3,1,0])
    idx=np.repeat(np.arange(len(y)),weights)
    assert abs(module.weighted_ap(y,scores,weights)-average_precision_score(y[idx],scores[idx]))<1e-12
