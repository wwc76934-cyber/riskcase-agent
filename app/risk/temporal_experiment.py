"""Time boundaries and controlled variants for development-only diagnostics."""

from copy import deepcopy

from app.risk.ml_models import make_model


VARIANTS = ("logistic_base", "tree_base", "tree_no_channel", "tree_stronger_regularization", "tree_unweighted")


def fold_masks(metadata, fold):
    start, end, begin, stop = (fold[key] for key in ("fit_start", "fit_end", "evaluate_start", "evaluate_end"))
    if not "2016-01-01" <= start < end <= begin < stop <= "2018-01-01":
        raise ValueError("Nonoverlapping chronological windows within original training period required")
    if not metadata["split"].eq("train").all():
        raise ValueError("Temporal diagnostics may only use original train records")
    stamps = metadata["event_time"]
    fit = ((stamps >= start) & (stamps < end)).to_numpy()
    evaluate = ((stamps >= begin) & (stamps < stop)).to_numpy()
    if not fit.any() or not evaluate.any():
        raise ValueError("Both chronological windows must contain records")
    if (fit & evaluate).any():
        raise ValueError("Time windows overlap")
    return fit, evaluate


def make_variant(name, base_policy, temporal_policy):
    if name not in VARIANTS:
        raise ValueError("Unknown predeclared variant")
    policy = deepcopy(base_policy)
    if name == "tree_stronger_regularization":
        policy["hist_gradient_boosting"].update(temporal_policy["stronger_regularization"])
    model = make_model("logistic_regression" if name == "logistic_base" else "hist_gradient_boosting", policy)
    if name == "tree_no_channel":
        preprocess = model.named_steps["preprocess"]
        preprocess.transformers = [(key, transformer, [column for column in columns if column != "channel"])
                                   for key, transformer, columns in preprocess.transformers]
        model.set_params(model__categorical_features=[False] * 12 + [True])
    if name == "tree_unweighted":
        model.set_params(model__class_weight=None)
    return model
