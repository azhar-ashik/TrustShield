"""TrustShield-v2 runtime ML scoring layer.

The model is trained on labeled mobile-money benchmark data.
TrustShield-specific community reputation and balance-drain checks remain
separate policy signals in main.py.
"""
from pathlib import Path
import json
import joblib
import numpy as np

FEATURES = [
    "amount_log",
    "amount_ratio",
    "new_recipient",
    "night",
    "sender_tx_count_log",
    "sender_avg_gap_hours",
    "sender_amount_zscore",
]

BASE = {
    "amount_log": float(np.log1p(2000.0)),
    "amount_ratio": 1.0,
    "new_recipient": 0.0,
    "night": 0.0,
    "sender_tx_count_log": 0.0,
    "sender_avg_gap_hours": 24.0,
    "sender_amount_zscore": 0.0,
}

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "models" / "risk_model.joblib"
META_PATH = HERE / "models" / "model_meta.json"

_model = None
_meta = {}

try:
    _model = joblib.load(MODEL_PATH)
except Exception:
    _model = None

try:
    _meta = json.loads(META_PATH.read_text(encoding="utf-8"))
except Exception:
    _meta = {}


def _predict_probability(features):
    x = np.array([[float(features[k]) for k in FEATURES]], dtype=np.float32)
    return float(_model.predict_proba(x)[0][1])


def _rules(features):
    """Fallback only if the trained model is unavailable."""
    c = {k: 0.0 for k in FEATURES}

    c["amount_ratio"] = (
        22 if features["amount_ratio"] >= 8
        else 12 if features["amount_ratio"] >= 4
        else 0
    )
    c["sender_amount_zscore"] = (
        20 if features["sender_amount_zscore"] >= 4
        else 12 if features["sender_amount_zscore"] >= 2.5
        else 0
    )
    c["new_recipient"] = 10 * features["new_recipient"]
    c["night"] = 8 * features["night"]
    c["sender_tx_count_log"] = 6 if features["sender_tx_count_log"] <= 0.7 else 0
    c["sender_avg_gap_hours"] = (
        8 if features["sender_avg_gap_hours"] < 1
        else 4 if features["sender_avg_gap_hours"] < 3
        else 0
    )
    c["amount_log"] = 5 if features["amount_log"] >= np.log1p(10000) else 0

    return round(min(100.0, sum(c.values())), 2), c


def score_and_explain(features):
    """
    Returns (risk_score_0_to_100, per_feature_contribution).

    Runtime explanations use feature occlusion. Training also produces a
    real SHAP summary report at backend/models/shap_summary.png.
    """
    if _model is None:
        return _rules(features)

    full = _predict_probability(features)
    contributions = {}

    for key in FEATURES:
        altered = dict(features)
        altered[key] = BASE[key]
        without_feature = _predict_probability(altered)
        contributions[key] = round((full - without_feature) * 100.0, 3)

    return round(min(100.0, max(0.0, full * 100.0)), 2), contributions


def info():
    if _model is None:
        return "PaySim model unavailable — rule-based fallback active"

    name = _meta.get("selected_model", "trained model")
    test = _meta.get("test_metrics", {})
    pr_auc = test.get("pr_auc")
    roc_auc = test.get("roc_auc")

    if pr_auc is None:
        return f"{name} | labeled mobile-money model"

    return (
        f"{name} | PaySim transfer model | "
        f"test PR-AUC={pr_auc:.3f} | "
        f"test ROC-AUC={roc_auc:.3f}"
    )


def model_threshold():
    return float(_meta.get("threshold", 0.5))


def metadata():
    return dict(_meta)


def metrics():
    """Compact metrics payload for the analyst dashboard."""
    test = _meta.get("test_metrics", {})
    return {
        "selected_model": _meta.get("selected_model", "trained model"),
        "pr_auc": test.get("pr_auc"),
        "roc_auc": test.get("roc_auc"),
        "precision": test.get("precision"),
        "recall": test.get("recall"),
        "f1": test.get("f1"),
        "threshold": _meta.get("threshold", 0.5),
        "dataset": _meta.get("dataset", "PaySim-based labeled mobile-money benchmark"),
    }
