"""Train the TrustShield risk model on SYNTHETIC data (labels come from scam-pattern rules + noise).
Run: python train.py   -> writes models/risk_model.joblib"""
import numpy as np, joblib, os
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from ml import FEATURES
rng = np.random.default_rng(42); N = 12000
rc = np.where(rng.random(N) < 0.7, 0, rng.integers(1, 26, N))
rs = np.clip(rc * 6 + rng.integers(0, 15, N) * (rc > 0), 0, 100)
ratio = np.clip(np.exp(rng.normal(0.3, 1.1, N)), 0.1, 50)
drain = np.clip(rng.beta(1.2, 10, N), 0, 1)
new = (rng.random(N) < 0.4).astype(int); night = (rng.random(N) < 0.1).astype(int); recent = rng.poisson(0.5, N)
logit = -5 + 0.25 * np.minimum(rc, 15) + 0.03 * rs + 0.9 * np.log(np.clip(ratio, 1, 50)) + 0.8 * new + 0.6 * night + 0.4 * np.minimum(recent, 5) + 3 * drain
y = (rng.random(N) < 1 / (1 + np.exp(-(logit + rng.normal(0, 0.5, N))))).astype(int)
X = np.column_stack([rc, rs, ratio, drain, new, night, recent])  # order must match ml.FEATURES
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=1, stratify=y)
m = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=1).fit(Xtr, ytr)
print("features:", FEATURES, "| positives: %.1f%%" % (100 * y.mean()), "| holdout AUC: %.3f" % roc_auc_score(yte, m.predict_proba(Xte)[:, 1]))
os.makedirs("models", exist_ok=True); joblib.dump(m, "models/risk_model.joblib"); print("saved models/risk_model.joblib")
