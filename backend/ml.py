import os
import numpy as np
FEATURES = ["report_count", "rec_score", "amount_ratio", "drain", "new_recipient", "night", "recent"]
BASE = {"report_count": 0, "rec_score": 0, "amount_ratio": 1, "drain": 0.01, "new_recipient": 0, "night": 0, "recent": 0}  # "safe" reference values
_model = None
try:
    import joblib
    _model = joblib.load(os.path.join(os.path.dirname(__file__), "models", "risk_model.joblib"))
except Exception:
    _model = None

def _p(f): return float(_model.predict_proba(np.array([[f[k] for k in FEATURES]]))[0][1]) * 100

def _rules(f):
    c = {k: 0 for k in FEATURES}
    c["report_count"] = 40 if f["report_count"] >= 10 else 28 if f["report_count"] >= 3 else 8 if f["report_count"] >= 1 else 0
    c["rec_score"] = f["rec_score"] * 0.2 if f["rec_score"] >= 30 else 0
    c["amount_ratio"] = 22 if f["amount_ratio"] >= 10 else 12 if f["amount_ratio"] >= 4 else 0
    c["drain"] = 15 if f["drain"] >= 0.5 else 7 if f["drain"] >= 0.1 else 0
    c["new_recipient"] = 10 * f["new_recipient"]; c["night"] = 8 * f["night"]; c["recent"] = 10 if f["recent"] >= 3 else 0
    return round(min(100, sum(c.values()))), c

def score_and_explain(f):
    """Returns (score 0-100, per-feature contribution in points). Contribution = how much the score drops if that feature
    were set to its safe reference value (occlusion explanation, SHAP-like, no extra dependency)."""
    if _model is None: return _rules(f)
    full = _p(f); c = {}
    for k in FEATURES:
        g = dict(f); g[k] = BASE[k]; c[k] = full - _p(g)
    return round(min(100, max(0, full))), c

def info(): return "trained gradient-boosting model (synthetic data)" if _model else "rule-based fallback (model file not found)"
