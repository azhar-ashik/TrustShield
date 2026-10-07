# TrustShield FINAL

TrustShield is a demo mobile-finance safety application combining:
- labeled mobile-money ML risk scoring (PaySim-compatible development pipeline)
- behavioral transaction features and PR-AUC model selection
- SHAP training report + runtime feature occlusion explanations
- community scam reports and recipient reputation
- balance-drain safety policy
- real-time OTP prototype through a Telegram bot gateway
- PIN brute-force lockout
- append-only audit logging
- A/B intervention measurement and analyst dashboard
- scam-message checking

## Important ML note
The bundled `backend/data/trustshield_paysim_10k_demo.csv` is a generated PaySim-compatible development dataset, not a verbatim PaySim download and not real Upay data. For judge-facing training on the public PaySim benchmark, place the downloaded CSV at `backend/data/paysim.csv`, run `python extract_paysim_10k.py`, then train with:

```bash
PAYSIM_CSV=data/trustshield_paysim_10k.csv python train.py
```

Do not describe PaySim as real Upay/customer data.

## Run

### Backend
```bash
cd backend
source ../.venv/bin/activate
python -m pip install -r requirements.txt
python train.py
python -m uvicorn main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## OTP demo
For local development without a Telegram gateway, set:
```bash
export DEV_OTP_CONSOLE=1
```
Then the OTP is printed in the backend terminal. For Telegram delivery, configure `TELEGRAM_BOT_TOKEN` and optionally `TELEGRAM_CHAT_ID`.

## Demo accounts
- Rahim: `01700000001` / PIN `1111`
- Karim: `01811112222` / PIN `1111`
- Nusrat: `01512345678` / PIN `1111`
- Analyst: `01000000000` / PIN `9999`
