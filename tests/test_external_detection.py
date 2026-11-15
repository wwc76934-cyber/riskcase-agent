import numpy as np
import pandas as pd
import pytest
from app.cli.external_detection import FEATURES,masks,metrics


def test_fixed_time_splits_keep_ties_and_exclude_time_label_predictors():
    frame=pd.DataFrame({'Time':[0,86399,86400,86400,129599,129600,172799],'Class':[0,1,0,1,0,1,0]})
    split=masks(frame)
    assert [list(frame.index[split[k]]) for k in ('train','development','holdout')]==[[0,1],[2,3,4],[5,6]]
    assert sum(v.astype(int) for v in split.values()).eq(1).all()
    assert 'Class' not in FEATURES and 'Time' not in FEATURES and len(FEATURES)==29


def test_capacity_ties_use_source_identity_not_label_or_input_order():
    a=metrics([0,1,1],[.9,.9,.8],[1,2,3]);b=metrics([1,0,1],[.9,.9,.8],[2,1,3])
    assert a==b and a['review_fraction']['0.001']['captured']==0
    with pytest.raises(ValueError):metrics([0,1],[.2,np.nan],[0,1])
