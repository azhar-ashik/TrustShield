# TrustShield v2 (hackathon demo)
Simulated MFS wallet + anti-scam layer. **No real money/SMS/MFS APIs. OTP is always 123456.** Data is synthetic.

## What's new
- Trained gradient-boosting risk model (`backend/train.py`, synthetic data; occlusion-based explanations). Falls back to rules if the model file is missing.
- Scam Message Check (Claude API if `ANTHROPIC_API_KEY` is set, else keyword heuristic).
- Analyst view (login 01000000000 / PIN 9999), Change PIN with OTP, daily send limit (৳500,000, env `DAILY_LIMIT`).
- Demo users (PIN 1111): 01700000001, 01811112222, 01512345678.

## Run locally
```
cd backend && pip install -r requirements.txt && python train.py && uvicorn main:app --reload
cd frontend && npm install && npm run dev
```
## Deploy
See the step-by-step guide in the chat/DEPLOY section: GitHub -> Render (backend, build runs train.py) -> Vercel (frontend, VITE_API_URL) -> set CORS_ORIGINS.
## Honest limits
Model is trained on synthetic, rule-labelled data (not real fraud). SQLite resets on Render redeploy; use PostgreSQL in production.
