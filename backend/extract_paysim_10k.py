"""
Create a 10,000-row TRANSFER-only subset from the public PaySim CSV.

Example:
    python extract_paysim_10k.py \
      --input data/paysim.csv \
      --output data/trustshield_paysim_10k.csv
"""
from pathlib import Path
import argparse
import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/paysim.csv")
    parser.add_argument(
        "--output",
        default="data/trustshield_paysim_10k.csv",
    )
    parser.add_argument("--fraud", type=int, default=120)
    parser.add_argument("--legit", type=int, default=9880)
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)

    if not src.exists():
        raise FileNotFoundError(f"PaySim CSV not found: {src}")

    columns = [
        "step", "type", "amount", "nameOrig", "oldbalanceOrg",
        "newbalanceOrig", "nameDest", "oldbalanceDest",
        "newbalanceDest", "isFraud", "isFlaggedFraud",
    ]

    fraud_parts = []
    legit_parts = []

    print("Scanning PaySim in chunks...")
    for chunk_id, chunk in enumerate(pd.read_csv(
        src,
        usecols=columns,
        chunksize=250_000,
    )):
        transfer = chunk[chunk["type"].eq("TRANSFER")]

        fraud = transfer[transfer["isFraud"].eq(1)]
        legit = transfer[transfer["isFraud"].eq(0)]

        if not fraud.empty:
            fraud_parts.append(fraud)

        if not legit.empty:
            # Build a manageable legitimate pool from all time regions.
            take = min(1200, len(legit))
            legit_parts.append(
                legit.sample(
                    n=take,
                    random_state=42 + chunk_id,
                )
            )

    fraud_all = pd.concat(fraud_parts, ignore_index=True)
    legit_pool = pd.concat(legit_parts, ignore_index=True)

    if len(fraud_all) < args.fraud:
        raise ValueError("Not enough TRANSFER fraud rows.")
    if len(legit_pool) < args.legit:
        raise ValueError("Not enough legitimate sample rows.")

    fraud_sample = fraud_all.sample(
        n=args.fraud,
        random_state=42,
    )
    legit_sample = legit_pool.sample(
        n=args.legit,
        random_state=43,
    )

    result = pd.concat(
        [fraud_sample, legit_sample],
        ignore_index=True,
    ).sort_values(
        ["step", "nameOrig"]
    ).reset_index(drop=True)

    dst.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(dst, index=False)

    print(f"Created: {dst}")
    print(f"Rows: {len(result):,}")
    print(f"Fraud: {int(result.isFraud.sum())}")
    print(f"Fraud rate: {100 * result.isFraud.mean():.2f}%")


if __name__ == "__main__":
    main()
