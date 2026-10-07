"""
TrustShield-v2 fraud model training.

Default:
    python train.py

This trains on backend/data/trustshield_paysim_10k_demo.csv so the repository
works immediately.

For the judge-facing experiment, create a true 10K subset from the public
PaySim CSV and run:
    PAYSIM_CSV=data/trustshield_paysim_10k.csv python train.py

The model uses only transaction-time/history features and intentionally
excludes PaySim balance-after-transaction fields and isFlaggedFraud.
"""

from pathlib import Path
import json
import os
import warnings

import joblib
import numpy as np
import pandas as pd

from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
DATA_PATH = Path(os.getenv(
    "PAYSIM_CSV",
    str(HERE / "data" / "trustshield_paysim_10k_demo.csv")
))

MODEL_DIR = HERE / "models"
MODEL_PATH = MODEL_DIR / "risk_model.joblib"
META_PATH = MODEL_DIR / "model_meta.json"
SHAP_PATH = MODEL_DIR / "shap_summary.png"

FEATURES = [
    "amount_log",
    "amount_ratio",
    "new_recipient",
    "night",
    "sender_tx_count_log",
    "sender_avg_gap_hours",
    "sender_amount_zscore",
]


def build_features(df):
    df = df.sort_values(["step", "nameOrig"]).reset_index(drop=True).copy()

    sender = df.groupby("nameOrig", sort=False)
    prev_count = sender.cumcount().astype(float)

    prev_sum = sender["amount"].cumsum() - df["amount"]
    amount_sq = df["amount"] ** 2
    prev_sq_sum = (
        amount_sq.groupby(df["nameOrig"], sort=False).cumsum()
        - amount_sq
    )

    safe_count = prev_count.replace(0, np.nan)
    prev_mean = prev_sum / safe_count
    prev_var = (prev_sq_sum / safe_count) - (prev_mean ** 2)
    prev_std = np.sqrt(prev_var.clip(lower=1.0))

    global_amount = float(df["amount"].median())
    prev_mean = prev_mean.fillna(max(global_amount, 1.0))
    prev_std = prev_std.fillna(max(global_amount * 0.50, 1.0))

    prev_step = sender["step"].shift(1)
    gap = (df["step"] - prev_step).astype(float)
    global_gap = float(gap.dropna().median()) if gap.notna().any() else 24.0
    gap = gap.fillna(global_gap).clip(0, 168)

    pair_count = df.groupby(
        ["nameOrig", "nameDest"], sort=False
    ).cumcount()

    hour = df["step"] % 24

    features = pd.DataFrame(index=df.index)
    features["amount_log"] = np.log1p(df["amount"]).astype(float)
    features["amount_ratio"] = (
        df["amount"] / prev_mean.clip(lower=1.0)
    ).clip(0, 50)
    features["new_recipient"] = pair_count.eq(0).astype(float)
    features["night"] = ((hour < 5) | (hour >= 23)).astype(float)
    features["sender_tx_count_log"] = np.log1p(prev_count)
    features["sender_avg_gap_hours"] = gap
    features["sender_amount_zscore"] = (
        (df["amount"] - prev_mean) / prev_std
    ).clip(-8, 8).fillna(0.0)

    return features[FEATURES].astype(np.float32)


def metric_dict(y_true, probability, threshold):
    prediction = (probability >= threshold).astype(int)
    return {
        "pr_auc": float(average_precision_score(y_true, probability)),
        "roc_auc": float(roc_auc_score(y_true, probability)),
        "precision": float(precision_score(y_true, prediction, zero_division=0)),
        "recall": float(recall_score(y_true, prediction, zero_division=0)),
        "f1": float(f1_score(y_true, prediction, zero_division=0)),
    }


def choose_threshold(y_true, probability):
    precision, recall, thresholds = precision_recall_curve(
        y_true, probability
    )
    if len(thresholds) == 0:
        return 0.5

    f1 = (
        2 * precision[:-1] * recall[:-1]
        / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    )
    return float(thresholds[int(np.nanargmax(f1))])


def make_shap_report(model, model_name, X_test):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import shap

        sample = X_test.sample(min(1000, len(X_test)), random_state=42)

        if model_name == "logistic_regression":
            scaler = model.named_steps["scaler"]
            classifier = model.named_steps["model"]
            scaled = scaler.transform(sample)
            explainer = shap.LinearExplainer(classifier, scaled)
            values = explainer.shap_values(scaled)
        else:
            explainer = shap.TreeExplainer(model)
            values = explainer.shap_values(sample)
            if isinstance(values, list):
                values = values[1]

        plt.figure(figsize=(9, 6))
        shap.summary_plot(
            values,
            sample,
            feature_names=FEATURES,
            show=False,
            plot_size=None,
        )
        plt.tight_layout()
        plt.savefig(SHAP_PATH, dpi=180, bbox_inches="tight")
        plt.close()
        print(f"SHAP report saved: {SHAP_PATH}")
    except Exception as exc:
        print(f"SHAP report skipped: {exc}")


def main():
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATA_PATH}"
        )

    print(f"Loading dataset: {DATA_PATH}")

    usecols = [
        "step",
        "type",
        "amount",
        "nameOrig",
        "nameDest",
        "isFraud",
    ]
    df = pd.read_csv(DATA_PATH, usecols=usecols)

    # TrustShield is a send-money / transfer flow.
    df = df[df["type"].astype(str).eq("TRANSFER")].copy()

    if df.empty:
        raise ValueError("Dataset contains no TRANSFER rows.")

    df["isFraud"] = df["isFraud"].astype(int)
    df = df.sort_values(["step", "nameOrig"]).reset_index(drop=True)

    print(f"Transfer rows: {len(df):,}")
    print(
        f"Fraud rows: {int(df.isFraud.sum()):,} "
        f"({100 * df.isFraud.mean():.2f}%)"
    )

    X = build_features(df)
    y = df["isFraud"].to_numpy(dtype=np.int8)
    steps = df["step"].to_numpy()

    # Chronological split.
    train_cut = float(np.quantile(steps, 0.70))
    val_cut = float(np.quantile(steps, 0.85))

    train_mask = steps <= train_cut
    val_mask = (steps > train_cut) & (steps <= val_cut)
    test_mask = steps > val_cut

    X_train, y_train = X.loc[train_mask], y[train_mask]
    X_val, y_val = X.loc[val_mask], y[val_mask]
    X_test, y_test = X.loc[test_mask], y[test_mask]

    print(
        f"Split: train <= {train_cut:.0f}, "
        f"validation <= {val_cut:.0f}, test > {val_cut:.0f}"
    )
    print(
        f"Rows: train={len(X_train):,}, "
        f"validation={len(X_val):,}, test={len(X_test):,}"
    )
    print(
        f"Fraud: train={y_train.sum()}, "
        f"validation={y_val.sum()}, test={y_test.sum()}"
    )

    if min(y_train.sum(), y_val.sum(), y_test.sum()) == 0:
        raise ValueError(
            "At least one chronological split has no fraud examples."
        )

    models = {
        "logistic_regression": Pipeline([
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(
                class_weight="balanced",
                max_iter=1200,
                random_state=42,
            )),
        ]),
        "random_forest": RandomForestClassifier(
            n_estimators=180,
            max_depth=10,
            min_samples_leaf=2,
            class_weight="balanced_subsample",
            n_jobs=-1,
            random_state=42,
        ),
        "gradient_boosting": GradientBoostingClassifier(
            n_estimators=140,
            learning_rate=0.05,
            max_depth=3,
            random_state=42,
        ),
    }

    comparison = {}
    fitted = {}

    neg = max(int((y_train == 0).sum()), 1)
    pos = max(int((y_train == 1).sum()), 1)
    positive_weight = min(50.0, neg / pos)

    for name, model in models.items():
        print(f"\nTraining {name}...")

        if name == "gradient_boosting":
            weights = np.where(
                y_train == 1,
                positive_weight,
                1.0,
            )
            model.fit(X_train, y_train, sample_weight=weights)
        else:
            model.fit(X_train, y_train)

        val_probability = model.predict_proba(X_val)[:, 1]
        threshold = choose_threshold(y_val, val_probability)
        metrics = metric_dict(y_val, val_probability, threshold)

        comparison[name] = {
            **metrics,
            "threshold": threshold,
        }
        fitted[name] = model

        print(
            f"{name}: "
            f"PR-AUC={metrics['pr_auc']:.4f}, "
            f"ROC-AUC={metrics['roc_auc']:.4f}, "
            f"Precision={metrics['precision']:.4f}, "
            f"Recall={metrics['recall']:.4f}, "
            f"F1={metrics['f1']:.4f}, "
            f"threshold={threshold:.4f}"
        )

    # Fraud-specific model selection metric.
    best_name = max(
        comparison,
        key=lambda name: comparison[name]["pr_auc"],
    )
    best_model = fitted[best_name]
    threshold = comparison[best_name]["threshold"]

    test_probability = best_model.predict_proba(X_test)[:, 1]
    test_metrics = metric_dict(y_test, test_probability, threshold)

    print("\n========== FINAL MODEL ==========")
    print("Selected:", best_name)
    for key, value in test_metrics.items():
        print(f"{key}: {value:.4f}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(best_model, MODEL_PATH)

    metadata = {
        "dataset": DATA_PATH.name,
        "dataset_type": (
            "generated PaySim-compatible demo"
            if "trustshield_paysim_10k_demo" in DATA_PATH.name
            else "public PaySim-derived subset"
        ),
        "transaction_scope": "TRANSFER only",
        "features": FEATURES,
        "target": "isFraud",
        "leakage_policy": (
            "Model excludes oldbalanceOrg, newbalanceOrig, "
            "oldbalanceDest, newbalanceDest and isFlaggedFraud."
        ),
        "split_method": "chronological 70/15/15",
        "model_selection_metric": "validation PR-AUC",
        "selected_model": best_name,
        "threshold": threshold,
        "model_comparison": comparison,
        "test_metrics": test_metrics,
        "rows": int(len(df)),
        "fraud_rows": int(y.sum()),
        "fraud_rate": float(y.mean()),
    }

    META_PATH.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    make_shap_report(best_model, best_name, X_test)

    print(f"\nSaved model: {MODEL_PATH}")
    print(f"Saved metadata: {META_PATH}")


if __name__ == "__main__":
    main()
