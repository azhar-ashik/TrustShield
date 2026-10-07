"""TrustShield backend v3. DEMO: simulated wallet, no real money.
OTPs are random, expire in 3 minutes, allow 3 tries, and are sent through a Telegram bot gateway."""
import os, json, time, hmac, hashlib, secrets, math, datetime as dt, bcrypt, jwt, httpx
import numpy as np
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, event, Column, Integer, String, Float, DateTime, Boolean, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import ml

SECRET = os.getenv("JWT_SECRET", "dev-only-change-me")
DAILY_LIMIT = float(os.getenv("DAILY_LIMIT", "500000"))
MAX_FAILS, LOCK_MIN = 5, 15
OTP_TTL, OTP_TRIES, OTP_GAP = 180, 3, 30
engine = create_engine("sqlite:///trustshield.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
now = dt.datetime.utcnow

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True); name = Column(String); username = Column(String, unique=True)
    phone = Column(String, unique=True); pin_hash = Column(String); balance = Column(Float, default=1_000_000)
    created_at = Column(DateTime, default=now); is_analyst = Column(Boolean, default=False)
    failed_attempts = Column(Integer, default=0); locked_until = Column(DateTime, nullable=True)
class RecipientRisk(Base):
    __tablename__ = "recipient_risk"
    phone_number = Column(String, primary_key=True); display_name = Column(String); risk_score = Column(Float, default=0)
    risk_level = Column(String, default="SAFE"); report_count = Column(Integer, default=0); verified = Column(Boolean, default=False)
    categories = Column(String, default=""); last_reported = Column(DateTime, nullable=True); notes = Column(Text, default="")
class FraudReport(Base):
    __tablename__ = "fraud_reports"
    id = Column(Integer, primary_key=True); reporter_user_id = Column(Integer); phone_number = Column(String)
    category = Column(String); description = Column(Text); transaction_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now); status = Column(String, default="SUBMITTED")
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True); sender_id = Column(Integer); recipient_phone = Column(String)
    recipient_name = Column(String); amount = Column(Float); risk_score = Column(Float); risk_level = Column(String)
    decision = Column(String); status = Column(String, default="PENDING"); created_at = Column(DateTime, default=now)
    risk_reasons = Column(Text, default=""); review = Column(String, default="")
    experiment_group = Column(String, default="treatment"); user_action = Column(String, default="NONE")  # NONE|PROCEEDED|CANCELLED|ABANDONED
    survey_helpful = Column(Boolean, nullable=True)
class Contact(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True); owner_id = Column(Integer); name = Column(String); phone = Column(String)
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True); actor_user_id = Column(Integer, nullable=True); event_type = Column(String)
    ip_address = Column(String); user_agent = Column(String); metadata_json = Column(Text); created_at = Column(DateTime, default=now)

@event.listens_for(AuditLog, "before_update")
def _no_update(*_): raise ValueError("audit log is append-only")
@event.listens_for(AuditLog, "before_delete")
def _no_delete(*_): raise ValueError("audit log is append-only")

def audit(db, event_type, request=None, actor=None, **meta):
    ip = ua = ""
    if request is not None:
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
        ua = request.headers.get("user-agent", "")[:300]
    db.add(AuditLog(actor_user_id=actor, event_type=event_type, ip_address=ip, user_agent=ua, metadata_json=json.dumps(meta, default=str)))
    db.commit()

def level_for(score, reports):
    if reports >= 10 or score >= 85: return "REPORTED_SCAM"
    if score >= 65: return "HIGH_RISK"
    if score >= 35: return "SUSPICIOUS"
    if score >= 10: return "LOW_RISK"
    return "SAFE"

def seed():
    Base.metadata.create_all(engine); db = SessionLocal()
    if not db.query(RecipientRisk).first():
        for p, n, s, r, c, v in [("01700000001","Rahim Ahmed",0,0,"",1),("01811112222","Karim Hasan",12,1,"",1),("01999998888","Unknown",92,17,"Fake investment",0),
                                 ("01655554444","Unknown",48,4,"Social engineering",0),("01512345678","Nusrat",0,0,"",1)]:
            db.add(RecipientRisk(phone_number=p, display_name=n, risk_score=s, report_count=r, categories=c, verified=bool(v), risk_level=level_for(s, r)))
    for n, un, ph, pin, an in [("Rahim Ahmed","rahim","01700000001","1111",False),("Karim Hasan","karim","01811112222","1111",False),
                               ("Nusrat Jahan","nusrat","01512345678","1111",False),("Analyst","analyst","01000000000","9999",True)]:
        if not db.query(User).filter(User.phone == ph).first():
            db.add(User(name=n, username=un, phone=ph, balance=0 if an else 50_000, is_analyst=an, pin_hash=bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()))
    db.commit(); db.close()
seed()

app = FastAPI(title="TrustShield (demo)")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])
@app.middleware("http")
async def fix_double_slash(request, call_next):
    p = request.scope["path"]
    if p.startswith("//"): request.scope["path"] = "/" + p.lstrip("/")
    return await call_next(request)
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def valid_phone(p): return p.isdigit() and len(p) == 11 and p.startswith("01")
def token_for(u): return jwt.encode({"sub": str(u.id), "exp": now() + dt.timedelta(hours=8)}, SECRET, "HS256")
def current_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    try: uid = int(jwt.decode(authorization.split()[1], SECRET, ["HS256"])["sub"])
    except Exception: raise HTTPException(401, "Please log in again")
    u = db.get(User, uid)
    if not u: raise HTTPException(401, "User not found")
    return u
def user_out(u): return {"id": u.id, "name": u.name, "username": u.username, "phone": u.phone, "balance": u.balance, "is_analyst": bool(u.is_analyst), "created_at": u.created_at.isoformat()}

# ---------- PIN check with brute-force lockout ----------
def verify_pin(db, u, pin, request, context):
    if u.locked_until and u.locked_until > now():
        raise HTTPException(423, f"Account locked. Try again in {int((u.locked_until - now()).total_seconds() // 60) + 1} minute(s).")
    if u.locked_until: u.locked_until = None; u.failed_attempts = 0
    if not bcrypt.checkpw(pin.encode(), u.pin_hash.encode()):
        u.failed_attempts = (u.failed_attempts or 0) + 1
        if u.failed_attempts >= MAX_FAILS:
            u.locked_until = now() + dt.timedelta(minutes=LOCK_MIN); db.commit()
            audit(db, "LOCKOUT", request, u.id, context=context)
            raise HTTPException(423, f"Too many wrong PINs. Account locked for {LOCK_MIN} minutes.")
        db.commit(); audit(db, "AUTH_FAILURE", request, u.id, context=context, attempts=u.failed_attempts)
        raise HTTPException(401, f"Wrong PIN ({MAX_FAILS - u.failed_attempts} attempt(s) left)")
    u.failed_attempts = 0; u.locked_until = None; db.commit()

# ---------- OTP: random, hashed, 3-minute expiry, 3 tries, delivered via Telegram bot ----------
OTPS = {}
def _h(key, code): return hmac.new(SECRET.encode(), f"{key}:{code}".encode(), hashlib.sha256).hexdigest()
async def send_telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID", "5429313710")
    if not token:
        if os.getenv("DEV_OTP_CONSOLE") == "1": print("[DEV OTP]", text); return True  # local development only
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text})
        return r.status_code == 200
    except Exception: return False
async def issue_otp(key, label):
    t = time.time()
    for k in [k for k, v in OTPS.items() if v["exp"] < t]: del OTPS[k]
    if key in OTPS and t - OTPS[key]["issued"] < OTP_GAP: raise HTTPException(429, f"Please wait {OTP_GAP} seconds before requesting another OTP")
    code = str(secrets.randbelow(900000) + 100000)
    OTPS[key] = {"h": _h(key, code), "exp": t + OTP_TTL, "tries": 0, "issued": t}
    if not await send_telegram(f"TrustShield {label} code: {code}\nValid for 3 minutes. Never share it."):
        OTPS.pop(key, None); raise HTTPException(502, "Could not deliver the OTP. Check the Telegram gateway settings.")
def check_otp(key, code):
    rec = OTPS.get(key)
    if not rec: raise HTTPException(400, "No active OTP. Request a new one.")
    if time.time() > rec["exp"]: OTPS.pop(key, None); raise HTTPException(400, "OTP expired. Request a new one.")
    rec["tries"] += 1
    if not hmac.compare_digest(rec["h"], _h(key, code.strip())):
        if rec["tries"] >= OTP_TRIES: OTPS.pop(key, None); raise HTTPException(400, "Too many wrong OTPs. Request a new one.")
        raise HTTPException(400, f"Invalid OTP ({OTP_TRIES - rec['tries']} attempt(s) left)")
    OTPS.pop(key, None)

PENDING = {}  # phone -> registration data awaiting OTP
class RegisterIn(BaseModel): name: str; username: str; phone: str; pin: str; confirm_pin: str
class OtpIn(BaseModel): phone: str; otp: str
class LoginIn(BaseModel): phone: str; pin: str

@app.post("/api/auth/register")
async def register(d: RegisterIn, db: Session = Depends(get_db)):
    if not valid_phone(d.phone): raise HTTPException(400, "Invalid phone number (use 01XXXXXXXXX)")
    if not (d.pin.isdigit() and len(d.pin) in (4, 5)): raise HTTPException(400, "PIN must be 4 or 5 digits")
    if d.pin != d.confirm_pin: raise HTTPException(400, "PINs do not match")
    if db.query(User).filter((User.phone == d.phone) | (User.username == d.username)).first(): raise HTTPException(409, "Phone or username already registered")
    await issue_otp("reg:" + d.phone, "registration")
    PENDING[d.phone] = {"name": d.name.strip(), "username": d.username.strip(), "pin_hash": bcrypt.hashpw(d.pin.encode(), bcrypt.gensalt()).decode()}
    return {"message": "OTP dispatched via Telegram gateway", "expires_in": OTP_TTL}

@app.post("/api/auth/verify-otp")
def verify_otp(d: OtpIn, request: Request, db: Session = Depends(get_db)):
    p = PENDING.get(d.phone)
    if not p: raise HTTPException(400, "No pending registration")
    check_otp("reg:" + d.phone, d.otp)
    u = User(phone=d.phone, balance=1_000_000, **p); db.add(u); db.commit(); PENDING.pop(d.phone, None)
    audit(db, "AUTH_SUCCESS", request, u.id, context="register")
    return {"token": token_for(u), "user": user_out(u)}

@app.post("/api/auth/login")
def login(d: LoginIn, request: Request, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.phone == d.phone).first()
    if not u:
        audit(db, "AUTH_FAILURE", request, None, context="login", phone=d.phone); raise HTTPException(401, "Wrong phone number or PIN")
    verify_pin(db, u, d.pin, request, "login"); audit(db, "AUTH_SUCCESS", request, u.id, context="login")
    return {"token": token_for(u), "user": user_out(u)}

class PinIn(BaseModel): current_pin: str; new_pin: str; otp: str
@app.post("/api/auth/change-pin/request-otp")
async def pin_otp(u: User = Depends(current_user)):
    await issue_otp(f"pin:{u.id}", "PIN change"); return {"message": "OTP dispatched via Telegram gateway", "expires_in": OTP_TTL}
@app.post("/api/auth/change-pin")
def change_pin(d: PinIn, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not (d.new_pin.isdigit() and len(d.new_pin) in (4, 5)): raise HTTPException(400, "New PIN must be 4 or 5 digits")
    verify_pin(db, u, d.current_pin, request, "change_pin"); check_otp(f"pin:{u.id}", d.otp)
    u.pin_hash = bcrypt.hashpw(d.new_pin.encode(), bcrypt.gensalt()).decode(); db.commit()
    audit(db, "AUTH_SUCCESS", request, u.id, context="pin_changed"); return {"message": "PIN updated"}

@app.get("/api/me")
def me(u: User = Depends(current_user)): return user_out(u)
@app.get("/api/me/balance")
def bal(u: User = Depends(current_user)): return {"balance": u.balance}

def rec_info(db, phone):
    r = db.get(RecipientRisk, phone)
    if not r:
        ru = db.query(User).filter(User.phone == phone).first()
        if ru: return {"phone": phone, "name": ru.name, "risk_level": "SAFE", "risk_score": 0, "report_count": 0, "verified": True, "categories": ""}
        return {"phone": phone, "name": "Unknown (not in database)", "risk_level": "UNKNOWN", "risk_score": 20, "report_count": 0, "verified": False, "categories": ""}
    return {"phone": phone, "name": r.display_name, "risk_level": r.risk_level, "risk_score": r.risk_score, "report_count": r.report_count, "verified": r.verified, "categories": r.categories}
@app.get("/api/recipients/{phone}")
def recipient(phone: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not valid_phone(phone): raise HTTPException(400, "Invalid phone number")
    return rec_info(db, phone)
@app.get("/api/fraud/check/{phone}")
def check(phone: str, u: User = Depends(current_user), db: Session = Depends(get_db)): return rec_info(db, phone)
@app.get("/api/contacts")
def contacts(u: User = Depends(current_user), db: Session = Depends(get_db)):
    cs = db.query(Contact).filter(Contact.owner_id == u.id).all()
    if not cs:
        for n, p in [("Rahim Ahmed", "01700000001"), ("Karim Hasan", "01811112222"), ("Nusrat", "01512345678")]: db.add(Contact(owner_id=u.id, name=n, phone=p))
        db.commit(); cs = db.query(Contact).filter(Contact.owner_id == u.id).all()
    return [{"name": c.name, "phone": c.phone, "risk_level": rec_info(db, c.phone)["risk_level"]} for c in cs]

class ReportIn(BaseModel): phone: str; category: str; description: str = ""; transaction_id: int | None = None
@app.post("/api/fraud/report")
def report(d: ReportIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not valid_phone(d.phone): raise HTTPException(400, "Invalid phone number")
    if d.phone == u.phone: raise HTTPException(400, "You cannot report your own number")
    if db.query(FraudReport).filter(FraudReport.reporter_user_id == u.id, FraudReport.phone_number == d.phone).first(): raise HTTPException(409, "You have already reported this number")
    db.add(FraudReport(reporter_user_id=u.id, phone_number=d.phone, category=d.category, description=d.description, transaction_id=d.transaction_id))
    r = db.get(RecipientRisk, d.phone)
    if not r: r = RecipientRisk(phone_number=d.phone, display_name="Unknown", report_count=0, risk_score=0, categories=""); db.add(r)
    r.report_count += 1; r.risk_score = min(100, (r.risk_score or 0) + 8 + r.report_count); r.last_reported = now()
    if d.category not in (r.categories or ""): r.categories = (r.categories + ", " if r.categories else "") + d.category
    r.risk_level = level_for(r.risk_score, r.report_count); db.commit()
    return {"message": "Report submitted successfully.", "recipient": rec_info(db, d.phone)}
@app.get("/api/fraud/reports")
def reports(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"phone": r.phone_number, "category": r.category, "status": r.status, "created_at": r.created_at.isoformat()} for r in db.query(FraudReport).filter(FraudReport.reporter_user_id == u.id).order_by(FraudReport.id.desc())]

# ---------- Fraud engine ----------
def reason_text(k, f):
    if k == "amount_log":
        return f"Transaction amount is ৳{f['amount']:,.2f}."
    if k == "amount_ratio":
        return f"Amount is about {f['amount_ratio']:.1f}× your usual activity."
    if k == "new_recipient":
        return "New recipient."
    if k == "night":
        return "Unusual transaction time."
    if k == "sender_tx_count_log":
        return f"You have only {f['sender_tx_count']} previous completed transfer(s)."
    if k == "sender_avg_gap_hours":
        return (
            f"This transfer is only about {f['sender_avg_gap_hours']:.1f} "
            "hour(s) after your previous transfer."
        )
    if k == "sender_amount_zscore":
        return (
            "The amount is unusually large compared with your previous "
            f"transfer pattern (z-score {f['sender_amount_zscore']:.1f})."
        )
    return None


def analyze_features(u, amount, phone, db):
    r = rec_info(db, phone)

    # Use Bangladesh local hour without changing the stored UTC timestamps.
    h = (now() + dt.timedelta(hours=6)).hour

    past = (
        db.query(Transaction)
        .filter(
            Transaction.sender_id == u.id,
            Transaction.status == "COMPLETED",
        )
        .order_by(Transaction.created_at.asc())
        .all()
    )

    amounts = [float(t.amount) for t in past if float(t.amount) > 0]

    if amounts:
        avg = sum(amounts) / len(amounts)
        if len(amounts) >= 2:
            variance = sum((x - avg) ** 2 for x in amounts) / len(amounts)
            std = max(variance ** 0.5, 1.0)
        else:
            std = max(avg * 0.50, 1.0)
    else:
        avg = 2000.0
        std = 1000.0

    if past:
        gap_hours = max(
            0.0,
            (now() - past[-1].created_at).total_seconds() / 3600.0,
        )
        gap_hours = min(gap_hours, 168.0)
    else:
        gap_hours = 24.0

    new_recipient = int(
        not any(t.recipient_phone == phone for t in past)
    )

    f = {
        # ML features
        "amount_log": float(math.log1p(float(amount))),
        "amount_ratio": min(
            float(amount) / max(avg, 1.0),
            50.0,
        ),
        "new_recipient": new_recipient,
        "night": int(h < 5 or h >= 23),
        "sender_tx_count_log": float(math.log1p(len(past))),
        "sender_avg_gap_hours": gap_hours,
        "sender_amount_zscore": float(
            np.clip(
                (float(amount) - avg) / max(std, 1.0),
                -8.0,
                8.0,
            )
        ),

        # Extra TrustShield-only policy fields; ml.py ignores these.
        "amount": float(amount),
        "sender_tx_count": len(past),
        "drain": float(amount) / max(float(u.balance), 1.0),
        "report_count": int(r["report_count"]),
        "rec_score": float(r["risk_score"]),
    }

    score, contrib = ml.score_and_explain(f)

    reasons = []

    # ML explanations
    for k, pts in sorted(
        contrib.items(),
        key=lambda kv: kv[1],
        reverse=True,
    ):
        if pts < 1.0:
            continue

        msg = reason_text(k, f)
        if not msg:
            continue

        severity = (
            "high" if pts >= 12
            else "medium" if pts >= 5
            else "low"
        )

        reasons.append({
            "severity": severity,
            "text": msg,
            "points": round(pts, 2),
            "source": "ml",
        })

    # Community reputation is a separate safety layer.
    if f["report_count"] >= 10:
        reasons.append({
            "severity": "high",
            "text": f"Recipient has {f['report_count']} community reports.",
            "points": 0,
            "source": "community",
        })
        score = max(score, 85)

    elif f["report_count"] >= 3:
        reasons.append({
            "severity": "medium",
            "text": f"Recipient has {f['report_count']} community reports.",
            "points": 0,
            "source": "community",
        })
        score = max(score, 70)

    elif f["report_count"] >= 1:
        reasons.append({
            "severity": "low",
            "text": f"Recipient has {f['report_count']} community report(s).",
            "points": 0,
            "source": "community",
        })
        score = max(score, 40)

    if f["rec_score"] >= 65:
        reasons.append({
            "severity": "high",
            "text": "Recipient has a high community risk score.",
            "points": 0,
            "source": "community",
        })
        score = max(score, 70)

    elif f["rec_score"] >= 35:
        reasons.append({
            "severity": "medium",
            "text": "Recipient has an elevated community risk score.",
            "points": 0,
            "source": "community",
        })
        score = max(score, 50)

    # Balance drain is kept as an app-specific policy rule, not a training label.
    if f["drain"] >= 0.80:
        reasons.append({
            "severity": "high",
            "text": (
                f"Transfer uses about {f['drain'] * 100:.0f}% "
                "of your current balance."
            ),
            "points": 0,
            "source": "policy",
        })
        score = max(score, 75)

    elif f["drain"] >= 0.50:
        reasons.append({
            "severity": "medium",
            "text": (
                f"Transfer uses about {f['drain'] * 100:.0f}% "
                "of your current balance."
            ),
            "points": 0,
            "source": "policy",
        })
        score = max(score, 55)

    if r["risk_level"] == "REPORTED_SCAM":
        score = max(score, 85)

    score = round(min(100.0, max(0.0, score)), 2)

    level = (
        "LOW" if score < 40
        else "MEDIUM" if score < 70
        else "HIGH" if score < 85
        else "CRITICAL"
    )

    return score, level, reasons[:8], r

BN = {"LOW": "এই লেনদেনটি নিরাপদ মনে হচ্ছে।", "MEDIUM": "অতিরিক্ত যাচাই প্রয়োজন। টাকা পাঠানোর আগে প্রাপকের তথ্য যাচাই করুন।",
      "HIGH": "TrustShield এই লেনদেনে সম্ভাব্য ঝুঁকি শনাক্ত করেছে। টাকা পাঠানোর আগে প্রাপক এবং লেনদেনের তথ্য যাচাই করুন।", "CRITICAL": "সতর্কতা: এই নম্বরটি একাধিক ব্যবহারকারী রিপোর্ট করেছেন।"}
EN = {"LOW": "Transaction appears safe.", "MEDIUM": "Additional verification recommended.", "HIGH": "TrustShield detected unusual activity.", "CRITICAL": "Potential scam detected."}

def assign_group(user_id, txn_id):
    """Deterministic A/B assignment per transaction. Set AB_TEST_ENABLED=false to give everyone the full protection (use for live demos)."""
    if os.getenv("AB_TEST_ENABLED", "true").lower() not in ("1", "true", "yes"): return "treatment"
    pct = int(os.getenv("AB_TREATMENT_PCT", "50")); n = int(hashlib.sha256(f"{os.getenv('AB_SALT', 'ts')}:{user_id}:{txn_id}".encode()).hexdigest(), 16)
    return "treatment" if n % 100 < pct else "control"

def expire_pending(db):
    for t in db.query(Transaction).filter(Transaction.status == "PENDING", Transaction.created_at < now() - dt.timedelta(minutes=10)):
        t.status = "CANCELLED"; t.user_action = "ABANDONED"
    db.commit()

class AnalyzeIn(BaseModel): recipient_phone: str; amount: float
@app.post("/api/transactions/analyze")
def analyze(d: AnalyzeIn, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    expire_pending(db)
    if not valid_phone(d.recipient_phone): raise HTTPException(400, "Invalid phone number")
    if d.recipient_phone == u.phone: raise HTTPException(400, "Cannot send money to yourself")
    if d.amount <= 0: raise HTTPException(400, "Enter a valid amount")
    if d.amount > u.balance: raise HTTPException(400, "Insufficient balance")
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    spent = sum(t.amount for t in db.query(Transaction).filter(Transaction.sender_id == u.id, Transaction.status == "COMPLETED", Transaction.created_at >= start))
    if spent + d.amount > DAILY_LIMIT: raise HTTPException(400, f"Daily limit of ৳{DAILY_LIMIT:,.0f} exceeded (already sent ৳{spent:,.0f} today)")
    score, level, reasons, r = analyze_features(u, d.amount, d.recipient_phone, db)
    t = Transaction(sender_id=u.id, recipient_phone=d.recipient_phone, recipient_name=r["name"], amount=d.amount, risk_score=score, risk_level=level, decision=level,
                    status="PENDING", risk_reasons=" | ".join(x["text"] for x in reasons)); db.add(t); db.flush()
    t.experiment_group = assign_group(u.id, t.id); db.commit()
    audit(db, "TRANSFER_INIT", request, u.id, transaction_id=t.id, amount=d.amount, risk_score=score, risk_level=level, group=t.experiment_group)
    base = {"transaction_id": t.id, "experiment_group": t.experiment_group, "recipient": r, "amount": d.amount, "demo_notice": "Demo environment — no real money is transferred."}
    if t.experiment_group == "control":  # silent scoring: no warning, explanation or cooldown shown
        return {**base, "risk_score": None, "risk_level": None, "reasons": [], "message_en": "Review your transfer and confirm with your PIN.",
                "message_bn": "আপনার লেনদেন যাচাই করে পিন দিয়ে নিশ্চিত করুন।", "cooldown_seconds": 0, "requires_explicit_confirm": False}
    return {**base, "risk_score": score, "risk_level": level, "reasons": reasons, "message_en": EN[level], "message_bn": BN[level],
            "cooldown_seconds": 60 if level in ("HIGH", "CRITICAL") else 0, "requires_explicit_confirm": level in ("HIGH", "CRITICAL")}

class ConfirmIn(BaseModel): transaction_id: int; pin: str; acknowledged: bool = False
@app.post("/api/transactions/confirm")
def confirm(d: ConfirmIn, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    verify_pin(db, u, d.pin, request, "transfer_confirm")
    t = db.get(Transaction, d.transaction_id)
    if not t or t.sender_id != u.id: raise HTTPException(404, "Transaction not found")
    if t.status != "PENDING": raise HTTPException(409, f"Transaction already {t.status}")
    if t.experiment_group == "treatment" and t.risk_level in ("HIGH", "CRITICAL"):  # enforced server-side, not just in the UI
        if not d.acknowledged: raise HTTPException(400, "Please acknowledge the risk warning first")
        wait = 60 - (now() - t.created_at).total_seconds()
        if wait > 0: raise HTTPException(400, f"Safety pause: wait {int(wait) + 1} more second(s)")
    if t.amount > u.balance: raise HTTPException(400, "Insufficient balance")
    u.balance -= t.amount
    rcv = db.query(User).filter(User.phone == t.recipient_phone).first()
    if rcv: rcv.balance += t.amount
    t.status = "COMPLETED"; t.user_action = "PROCEEDED"; db.commit()
    audit(db, "TRANSFER_SUCCESS", request, u.id, transaction_id=t.id, amount=t.amount, risk_level=t.risk_level, group=t.experiment_group)
    return {"message": "Transaction successful", "balance": u.balance}

class CancelIn(BaseModel): transaction_id: int
@app.post("/api/transactions/cancel")
def cancel(d: CancelIn, request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(Transaction, d.transaction_id)
    if not t or t.sender_id != u.id: raise HTTPException(404, "Transaction not found")
    if t.status == "PENDING":
        t.status = "CANCELLED"; t.user_action = "CANCELLED"; db.commit()
        audit(db, "TRANSFER_CANCEL", request, u.id, transaction_id=t.id, risk_level=t.risk_level, group=t.experiment_group)
    return {"status": t.status, "show_survey": t.experiment_group == "treatment" and t.risk_level in ("HIGH", "CRITICAL") and t.user_action == "CANCELLED"}

class SurveyIn(BaseModel): transaction_id: int; helpful: bool
@app.post("/api/transactions/survey")
def survey(d: SurveyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(Transaction, d.transaction_id)
    if not t or t.sender_id != u.id or t.user_action != "CANCELLED": raise HTTPException(404, "Transaction not found")
    t.survey_helpful = d.helpful; db.commit(); return {"message": "Thanks for your feedback"}

@app.get("/api/transactions")
def txns(u: User = Depends(current_user), db: Session = Depends(get_db)):
    expire_pending(db); out = []
    for t in db.query(Transaction).filter(Transaction.sender_id == u.id).all():
        out.append({"id": t.id, "direction": "OUT", "counterparty_name": t.recipient_name, "counterparty_phone": t.recipient_phone, "amount": t.amount, "risk_score": t.risk_score,
                    "risk_level": t.risk_level if t.experiment_group == "treatment" else "LOW", "status": t.status, "created_at": t.created_at.isoformat() + "Z", "reasons": t.risk_reasons})
    for t in db.query(Transaction).filter(Transaction.recipient_phone == u.phone, Transaction.status == "COMPLETED").all():
        s = db.get(User, t.sender_id)
        out.append({"id": t.id, "direction": "IN", "counterparty_name": s.name if s else "Unknown", "counterparty_phone": s.phone if s else "", "amount": t.amount,
                    "risk_score": 0, "risk_level": "LOW", "status": "COMPLETED", "created_at": t.created_at.isoformat() + "Z", "reasons": ""})
    return sorted(out, key=lambda x: x["created_at"], reverse=True)

@app.get("/api/security/status")
def sec(u: User = Depends(current_user)):
    return {"status": "ACTIVE", "features": ["Recipient reputation checking", "Transaction risk analysis", "Fraud reporting", "Suspicious transaction warnings", "Safety cooldown (server-enforced)",
            "Brute-force lockout", "Audit logging", "Transaction monitoring"], "database_label": "TrustShield Community Risk Database — Demo Data"}

# ---------- Scam message checker ----------
class MsgIn(BaseModel): text: str
KEYWORDS = ["prize", "lottery", "otp", "pin", "urgent", "verify", "blocked", "suspended", "click", "link", "reward", "double", "investment", "gift", "লটারি", "পিন", "ওটিপি", "পুরস্কার", "জরুরি", "ব্লক"]
def heuristic(t):
    hits = [k for k in KEYWORDS if k in t.lower()]
    return {"verdict": "SCAM" if len(hits) >= 3 else "SUSPICIOUS" if hits else "LIKELY_SAFE", "scam_type": "Possible social engineering" if hits else "",
            "red_flags": [f"Contains risky word: {h}" for h in hits],
            "advice_en": "Never share your PIN or OTP, and don't send money to claim prizes or unlock accounts." if hits else "No obvious red flags, but stay careful.",
            "advice_bn": "কখনো পিন বা ওটিপি কাউকে দেবেন না। পুরস্কার বা অ্যাকাউন্ট খোলার জন্য টাকা পাঠাবেন না।" if hits else "স্পষ্ট ঝুঁকি দেখা যায়নি, তবুও সতর্ক থাকুন।", "source": "keyword heuristic (no API key set)"}
@app.post("/api/scam/check")
def scam_check(d: MsgIn, u: User = Depends(current_user)):
    t = d.text.strip()[:2000]
    if len(t) < 5: raise HTTPException(400, "Paste a longer message")
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key: return heuristic(t)
    prompt = ("You are a scam-detection assistant for mobile financial services in Bangladesh. Analyze the message below. Reply with ONLY JSON: "
              '{"verdict":"SCAM|SUSPICIOUS|LIKELY_SAFE","scam_type":"","red_flags":[],"advice_en":"","advice_bn":""}. advice_bn must be in Bangla.\n\nMESSAGE:\n' + t)
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        out = json.loads(r.json()["content"][0]["text"].replace("```json", "").replace("```", "").strip()); out["source"] = "Claude API"; return out
    except Exception:
        h = heuristic(t); h["source"] = "keyword heuristic (Claude API call failed)"; return h

# ---------- Analyst + empirical impact metrics ----------
def analyst_only(u: User = Depends(current_user)):
    if not u.is_analyst: raise HTTPException(403, "Analyst access only")
    return u
@app.get("/api/analyst/summary")
def a_summary(u: User = Depends(analyst_only), db: Session = Depends(get_db)):
    expire_pending(db); ts = db.query(Transaction).all(); fl = [t for t in ts if t.risk_level in ("HIGH", "CRITICAL")]
    names = {x.id: x.name for x in db.query(User).all()}
    return {"total": len(ts), "flagged": len(fl), "reports": db.query(FraudReport).count(), "model": ml.info(),
            "avg_risk": round(sum(t.risk_score for t in ts) / len(ts), 1) if ts else 0,
            "alerts": [{"id": t.id, "recipient": t.recipient_name, "sender": names.get(t.sender_id, "?"), "amount": t.amount, "risk_score": t.risk_score, "risk_level": t.risk_level,
                        "status": t.status, "review": t.review, "reasons": t.risk_reasons, "group": t.experiment_group, "user_action": t.user_action} for t in sorted(fl, key=lambda x: -x.id)[:20]]}
class ReviewIn(BaseModel): transaction_id: int; action: str
@app.post("/api/analyst/review")
def a_review(d: ReviewIn, request: Request, u: User = Depends(analyst_only), db: Session = Depends(get_db)):
    t = db.get(Transaction, d.transaction_id)
    if not t or d.action not in ("ALLOWED", "ESCALATED"): raise HTTPException(400, "Bad request")
    t.review = d.action; db.commit(); audit(db, "ANALYST_REVIEW", request, u.id, transaction_id=t.id, action=d.action); return {"review": t.review}
@app.get("/api/analyst/audit")
def a_audit(u: User = Depends(analyst_only), db: Session = Depends(get_db)):
    return [{"id": a.id, "actor": a.actor_user_id, "event": a.event_type, "ip": a.ip_address, "meta": a.metadata_json, "at": a.created_at.isoformat()} for a in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(50)]

@app.get("/api/metrics/impact")
def impact(u: User = Depends(analyst_only), db: Session = Depends(get_db)):
    expire_pending(db); ts = db.query(Transaction).all(); hi = [t for t in ts if t.risk_level in ("HIGH", "CRITICAL")]
    aborted = lambda t: t.user_action in ("CANCELLED", "ABANDONED")
    def grp(g):
        h = [t for t in hi if t.experiment_group == g and t.user_action != "NONE"]; ab = [t for t in h if aborted(t)]
        return {"high_risk_resolved": len(h), "aborted": len(ab), "proceeded": len(h) - len(ab), "dropoff_rate": round(100 * len(ab) / len(h), 1) if h else 0.0,
                "amount_aborted_bdt": sum(t.amount for t in ab), "amount_sent_bdt": sum(t.amount for t in h if not aborted(t))}
    tr, ct = grp("treatment"), grp("control")
    reviewed = [t for t in ts if t.review in ("ALLOWED", "ESCALATED")]; allowed = [t for t in reviewed if t.review == "ALLOWED"]
    sv = [t for t in ts if t.survey_helpful is not None]
    return {"fraud_loss_prevented_bdt": tr["amount_aborted_bdt"], "scam_interventions_count": tr["aborted"], "high_risk_dropoff_rate": tr["dropoff_rate"],
            "false_positive_rate": round(100 * len(allowed) / len(reviewed), 1) if reviewed else None, "reviewed_alerts": len(reviewed),
            "intervention_success_rate": round(100 * sum(1 for t in sv if t.survey_helpful) / len(sv), 1) if sv else None, "survey_responses": len(sv),
            "groups": {"treatment": tr, "control": ct}, "dropoff_lift_pts": round(tr["dropoff_rate"] - ct["dropoff_rate"], 1),
            "high_risk_cancelled_all_groups_bdt": sum(t.amount for t in hi if aborted(t)), "model": ml.metrics(),
            "note": "Loss prevented counts only treatment-group (warned) cancellations. Model metrics are from a synthetic holdout. Small samples: treat as indicative."}
