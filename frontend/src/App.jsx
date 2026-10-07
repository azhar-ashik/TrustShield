import { useState, useEffect } from "react";
const API = (import.meta.env.VITE_API_URL || "https://trustshield-1-ljaz.onrender.com").trim().replace(/\/+$/, "");
const taka = (n) => "৳" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const store = { get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { sessionStorage.setItem(k, v); } catch {} }, del: (k) => { try { sessionStorage.removeItem(k); } catch {} } };
let token = store.get("ts_token"); // session token only (cleared when the tab closes). The PIN is never stored.
async function api(path, method = "GET", body) {
  let res;
  try { res = await fetch(API + path, { method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) }, body: body && JSON.stringify(body) }); }
  catch { throw new Error("Backend unavailable. Is the server running?"); }
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Request failed");
  return d;
}
const Err = ({ e }) => (e ? <div className="err">{e}</div> : null);
const Live = () => <p className="live">● Real-time OTP dispatched via Telegram gateway (demo). Valid 3 minutes, 3 attempts.</p>;

function Auth({ onAuth }) {
  const [mode, setMode] = useState("login"); const [f, setF] = useState({}); const [e, setE] = useState(""); const [otpStep, setOtpStep] = useState(false); const [busy, setBusy] = useState(false);
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });
  const go = async (fn) => { setE(""); setBusy(true); try { await fn(); } catch (x) { setE(x.message); } setBusy(false); };
  const sendOtp = () => go(async () => { await api("/api/auth/register", "POST", f); setOtpStep(true); });
  if (otpStep) return <div className="card"><h2>Verify phone</h2><p>Enter the 6-digit code for {f.phone}</p><Live />
    <input placeholder="6-digit OTP" inputMode="numeric" maxLength={6} onChange={set("otp")} /><Err e={e} />
    <button disabled={busy} onClick={() => go(async () => onAuth(await api("/api/auth/verify-otp", "POST", { phone: f.phone, otp: f.otp })))}>Verify</button>
    <button className="alt" disabled={busy} onClick={sendOtp}>Resend code</button><button className="alt" onClick={() => setOtpStep(false)}>Back</button></div>;
  return <div className="card"><h2>🛡 TrustShield</h2><p>{mode === "login" ? "Login" : "Create your TrustShield account"}</p>
    {mode === "reg" && <><input placeholder="Full name" onChange={set("name")} /><input placeholder="Username" onChange={set("username")} /></>}
    <input placeholder="Phone (01XXXXXXXXX)" onChange={set("phone")} /><input type="password" placeholder="PIN" onChange={set("pin")} />
    {mode === "reg" && <input type="password" placeholder="Confirm PIN" onChange={set("confirm_pin")} />}<Err e={e} />
    {mode === "login" ? <><button disabled={busy} onClick={() => go(async () => onAuth(await api("/api/auth/login", "POST", { phone: f.phone, pin: f.pin })))}>Login</button>
      <button className="alt" onClick={() => { setE(""); setMode("reg"); }}>Create account</button><p className="small">5 wrong PINs lock the account for 15 minutes. Forgot PIN? A reset would require OTP verification.</p></>
      : <><button disabled={busy} onClick={sendOtp}>{busy ? "Sending code…" : "Continue"}</button><button className="alt" onClick={() => { setE(""); setMode("login"); }}>Back</button></>}</div>;
}

function Send({ refresh }) {
  const [phone, setPhone] = useState(""); const [amount, setAmount] = useState(""); const [rec, setRec] = useState(null); const [a, setA] = useState(null); const [cs, setCs] = useState([]);
  const [pin, setPin] = useState(""); const [e, setE] = useState(""); const [done, setDone] = useState(""); const [cd, setCd] = useState(0); const [ack, setAck] = useState(false); const [bn, setBn] = useState(false);
  const [survey, setSurvey] = useState(null); const [thanks, setThanks] = useState(false);
  useEffect(() => { api("/api/contacts").then(setCs).catch(() => {}); }, []);
  useEffect(() => { setRec(null); if (/^01\d{9}$/.test(phone)) api("/api/recipients/" + phone).then(setRec).catch((x) => setE(x.message)); }, [phone]);
  useEffect(() => { if (cd > 0) { const t = setTimeout(() => setCd(cd - 1), 1000); return () => clearTimeout(t); } }, [cd]);
  const run = async (fn) => { setE(""); try { await fn(); } catch (x) { setE(x.message); } };
  const reset = () => { setA(null); setPin(""); setAck(false); setCd(0); };
  const control = a?.experiment_group === "control";
  if (done) return <div className="card ok"><h3>{done}</h3><p className="small">Demo environment — no real money is transferred.</p><button onClick={() => { setDone(""); setPhone(""); setAmount(""); reset(); }}>Done</button></div>;
  return <div>
    {survey && <div className="card"><h3>Quick question</h3><p>Did this warning help you prevent an unauthorized/scam payment?</p>
      <button onClick={() => run(async () => { await api("/api/transactions/survey", "POST", { transaction_id: survey, helpful: true }); setSurvey(null); setThanks(true); })}>Yes, it helped</button>
      <button className="alt" onClick={() => run(async () => { await api("/api/transactions/survey", "POST", { transaction_id: survey, helpful: false }); setSurvey(null); setThanks(true); })}>No</button></div>}
    {thanks && <div className="ok">Thanks for your feedback. Transfer cancelled.</div>}
    <div className="card"><h2>Send Money</h2>
      <input placeholder="Enter mobile number" value={phone} onChange={(x) => setPhone(x.target.value)} />
      <div className="chips">{cs.map((c) => <button key={c.phone} className="alt" onClick={() => setPhone(c.phone)}>{c.name}</button>)}</div>
      {rec && <div className="row"><div><b>{rec.name}</b><div className="small">{rec.phone} {rec.verified && "✓ verified"}</div></div><b className={rec.risk_level}>{rec.risk_level === "REPORTED_SCAM" ? "🚨 Reported scam" : rec.risk_level === "SUSPICIOUS" ? "⚠ Suspicious" : rec.risk_level.replace("_", " ")}</b></div>}
      {rec?.report_count > 0 && <p className="small">{rec.report_count} community report(s) {rec.categories && "· " + rec.categories}</p>}
      <p className="small">TrustShield Community Risk Database — Demo Data</p>
      <input type="number" placeholder="Amount (৳)" value={amount} onChange={(x) => setAmount(x.target.value)} /><Err e={e} />
      {!a && <button onClick={() => run(async () => { setThanks(false); const d = await api("/api/transactions/analyze", "POST", { recipient_phone: phone, amount: +amount }); setA(d); setCd(d.cooldown_seconds); })}>Scan with TrustShield</button>}</div>
    {a && control && <div className="card"><h3>Confirm transfer</h3><p>Send {taka(a.amount)} to {a.recipient.name} ({a.recipient.phone})</p>
      <input type="password" placeholder="Enter PIN to confirm" value={pin} onChange={(x) => setPin(x.target.value)} />
      <button disabled={!pin} onClick={() => run(async () => { const d = await api("/api/transactions/confirm", "POST", { transaction_id: a.transaction_id, pin }); setDone(d.message + " — new balance " + taka(d.balance)); refresh(); })}>Confirm & Send</button>
      <button className="alt" onClick={() => run(async () => { await api("/api/transactions/cancel", "POST", { transaction_id: a.transaction_id }); reset(); refresh(); })}>Cancel</button></div>}
    {a && !control && <div className="card"><h3>TrustShield Security Check</h3><h1 className={a.risk_level}>{a.risk_score} / 100 · {a.risk_level}</h1>
      <p><b>{a.risk_level === "LOW" ? "✓" : "🚨"} {bn ? a.message_bn : a.message_en}</b> <button className="alt" onClick={() => setBn(!bn)}>{bn ? "English" : "বাংলা"}</button></p>
      {a.reasons.map((r, i) => <div key={i}>{r.severity === "high" ? "🔴" : r.severity === "medium" ? "🟠" : "🟡"} {r.text}</div>)}
      {cd > 0 && <p><b>Safety pause: {cd}s</b></p>}
      {a.requires_explicit_confirm && <label><input type="checkbox" style={{ width: "auto" }} checked={ack} onChange={(x) => setAck(x.target.checked)} /> I understand the risk and want to continue</label>}
      <input type="password" placeholder="Enter PIN to confirm" value={pin} onChange={(x) => setPin(x.target.value)} />
      <button disabled={cd > 0 || (a.requires_explicit_confirm && !ack) || !pin} onClick={() => run(async () => { const d = await api("/api/transactions/confirm", "POST", { transaction_id: a.transaction_id, pin, acknowledged: ack }); setDone(d.message + " — new balance " + taka(d.balance)); refresh(); })}>{a.requires_explicit_confirm ? "Continue Anyway" : "Confirm & Send"}</button>
      <button className="alt" onClick={() => run(async () => { const d = await api("/api/transactions/cancel", "POST", { transaction_id: a.transaction_id }); if (d.show_survey) setSurvey(a.transaction_id); else setThanks(false); reset(); refresh(); })}>Cancel Transfer</button>
      <p className="small">{a.demo_notice}</p></div>}</div>;
}

function History() {
  const [t, setT] = useState([]); const [flt, setFlt] = useState("ALL");
  useEffect(() => { api("/api/transactions").then(setT).catch(() => {}); }, []);
  const shown = t.filter((x) => flt === "ALL" || (flt === "SENT" && x.direction === "OUT" && x.status === "COMPLETED") || (flt === "RECEIVED" && x.direction === "IN") || (flt === "CANCELLED" && x.status === "CANCELLED") || (flt === "FLAGGED" && x.direction === "OUT" && ["HIGH", "CRITICAL", "MEDIUM"].includes(x.risk_level)));
  return <div className="card"><h2>History</h2><div className="chips">{["ALL", "SENT", "RECEIVED", "CANCELLED", "FLAGGED"].map((k) => <button key={k} className={flt === k ? "" : "alt"} onClick={() => setFlt(k)}>{k}</button>)}</div>
    {shown.map((x) => <div className="row" key={x.direction + x.id}><div><b>{x.direction === "IN" ? "Received from " : "Sent to "}{x.counterparty_name}</b><div className="small">{new Date(x.created_at).toLocaleString()} · {x.status}</div></div>
      <div style={{ textAlign: "right" }}><b className={x.direction === "IN" ? "in" : ""}>{x.direction === "IN" ? "+" : "-"}{taka(x.amount)}</b>{x.direction === "OUT" && <div className={"small " + x.risk_level}>{x.risk_level}</div>}</div></div>)}
    {!shown.length && <p className="small">No transactions yet.</p>}</div>;
}

function Report() {
  const [f, setF] = useState({ category: "Fake investment" }); const [m, setM] = useState(""); const [e, setE] = useState(""); const [list, setList] = useState([]);
  const load = () => api("/api/fraud/reports").then(setList).catch(() => {}); useEffect(() => { load(); }, []);
  return <div className="card"><h2>Report a suspicious number</h2><input placeholder="Phone number" onChange={(x) => setF({ ...f, phone: x.target.value })} />
    <select onChange={(x) => setF({ ...f, category: x.target.value })}>{["Fake investment", "Fake customer support", "Prize/lottery scam", "Loan scam", "Account takeover", "Social engineering", "Marketplace scam", "Other"].map((c) => <option key={c}>{c}</option>)}</select>
    <textarea placeholder="Tell us what happened" onChange={(x) => setF({ ...f, description: x.target.value })} /><Err e={e} />{m && <div className="ok">{m}</div>}
    <button onClick={async () => { setE(""); setM(""); try { const d = await api("/api/fraud/report", "POST", f); setM(d.message + " Status: user-reported."); load(); } catch (x) { setE(x.message); } }}>Submit Report</button>
    <h3>My reports</h3>{list.map((r, i) => <div className="row" key={i}><span>{r.phone} · {r.category}</span><span className="small">{r.status}</span></div>)}</div>;
}

function MsgCheck() {
  const [t, setT] = useState(""); const [r, setR] = useState(null); const [e, setE] = useState(""); const [bn, setBn] = useState(false); const [busy, setBusy] = useState(false);
  return <div className="card"><h2>Scam Message Check</h2><p className="small">Paste an SMS, WhatsApp message or call script.</p>
    <textarea rows={5} placeholder="Paste message here" value={t} onChange={(x) => setT(x.target.value)} /><Err e={e} />
    <button disabled={busy || t.length < 5} onClick={async () => { setE(""); setR(null); setBusy(true); try { setR(await api("/api/scam/check", "POST", { text: t })); } catch (x) { setE(x.message); } setBusy(false); }}>{busy ? "Checking…" : "Check message"}</button>
    {r && <div><h3 className={r.verdict === "SCAM" ? "CRITICAL" : r.verdict === "SUSPICIOUS" ? "MEDIUM" : "LOW"}>{r.verdict}{r.scam_type ? " · " + r.scam_type : ""}</h3>
      {(r.red_flags || []).map((f, i) => <div key={i}>🚩 {f}</div>)}<p>{bn ? r.advice_bn : r.advice_en}</p><button className="alt" onClick={() => setBn(!bn)}>{bn ? "English" : "বাংলা"}</button><p className="small">Analysis by: {r.source}</p></div>}</div>;
}

function ChangePin() {
  const [f, setF] = useState({}); const [sent, setSent] = useState(false); const [m, setM] = useState(""); const [e, setE] = useState(""); const set = (k) => (x) => setF({ ...f, [k]: x.target.value });
  const run = async (fn) => { setE(""); setM(""); try { await fn(); } catch (x) { setE(x.message); } };
  return <div className="card"><h3>Change PIN</h3><Err e={e} />{m && <div className="ok">{m}</div>}
    {!sent ? <button onClick={() => run(async () => { await api("/api/auth/change-pin/request-otp", "POST"); setSent(true); })}>Send OTP</button> : <>
      <Live /><input type="password" placeholder="Current PIN" onChange={set("current_pin")} /><input type="password" placeholder="New PIN" onChange={set("new_pin")} /><input placeholder="6-digit OTP" inputMode="numeric" maxLength={6} onChange={set("otp")} />
      <button onClick={() => run(async () => { const d = await api("/api/auth/change-pin", "POST", f); setM(d.message); setSent(false); })}>Update PIN</button></>}</div>;
}

function Security({ user, logout }) {
  const [s, setS] = useState(null); useEffect(() => { api("/api/security/status").then(setS).catch(() => {}); }, []);
  return <div><div className="card"><h2>🛡 TrustShield Protection: {s?.status}</h2>{s?.features.map((f) => <div key={f}>✓ {f}</div>)}<p className="small">{s?.database_label}</p></div>
    <div className="card"><h3>Profile</h3><div>{user.name} (@{user.username})</div><div>{user.phone}</div><div className="small">Joined {new Date(user.created_at).toLocaleDateString()}</div><button className="red" onClick={logout}>Logout</button></div><ChangePin /></div>;
}

function Analyst() {
  const [s, setS] = useState(null); const [m, setM] = useState(null); const [e, setE] = useState("");
  const load = () => Promise.all([api("/api/analyst/summary").then(setS), api("/api/metrics/impact").then(setM)]).catch((x) => setE(x.message)); useEffect(() => { load(); }, []);
  const act = async (id, action) => { try { await api("/api/analyst/review", "POST", { transaction_id: id, action }); load(); } catch (x) { setE(x.message); } };
  if (!s || !m) return <div className="card"><Err e={e} />Loading…</div>;
  const pct = (v) => (v == null ? "n/a" : v + "%"); const mm = m.model; const G = ({ title, g }) => <div className="stat"><div className="small">{title}</div><b>{g.dropoff_rate}%</b><div className="small">{g.aborted} of {g.high_risk_resolved} high-risk aborted</div></div>;
  return <div><div className="card"><h2>Impact metrics</h2><Err e={e} /><div className="stats">
      <div className="stat"><div className="small">Fraud loss prevented</div><b>{taka(m.fraud_loss_prevented_bdt)}</b><div className="small">{m.scam_interventions_count} interventions</div></div>
      <div className="stat"><div className="small">Intervention success rate</div><b>{pct(m.intervention_success_rate)}</b><div className="small">{m.survey_responses} survey responses</div></div>
      <div className="stat"><div className="small">Model precision (holdout)</div><b>{mm ? (mm.precision * 100).toFixed(1) + "%" : "n/a"}</b><div className="small">threshold {mm?.threshold}</div></div>
      <div className="stat"><div className="small">False alarm rate (FPR)</div><b>{mm ? (mm.fpr * 100).toFixed(1) + "%" : "n/a"}</b><div className="small">analyst-allowed alerts: {pct(m.false_positive_rate)}</div></div></div>
      <p className="small">Model metrics come from a synthetic holdout (pipeline validation only). {m.note}</p></div>
    <div className="card"><h3>A/B experiment: warning drop-off</h3><div className="two"><G title="Treatment (warned)" g={m.groups.treatment} /><G title="Control (silent)" g={m.groups.control} /></div>
      <p className="small">Lift in drop-off: {m.dropoff_lift_pts} points. Overall: {s.total} transactions, {s.flagged} flagged, {s.reports} fraud reports. Engine: {s.model}</p></div>
    <div className="card"><h3>Recent alerts</h3>{s.alerts.map((a) => <div key={a.id} className="row"><div><b>#{a.id} {a.sender} → {a.recipient} · {taka(a.amount)}</b>
      <div className={"small " + a.risk_level}>{a.risk_score} {a.risk_level} · {a.status} · {a.group} · {a.user_action} {a.review && "· " + a.review}</div><div className="small">{a.reasons}</div></div>
      <div><button className="alt" onClick={() => act(a.id, "ALLOWED")}>Allow</button><button className="red" onClick={() => act(a.id, "ESCALATED")}>Escalate</button></div></div>)}</div></div>;
}

export default function App() {
  const [user, setUser] = useState(null); const [ready, setReady] = useState(!token); const [tab, setTab] = useState(store.get("ts_tab") || "home"); const [recent, setRecent] = useState([]);
  const refresh = () => Promise.all([api("/api/me").then(setUser), api("/api/transactions").then(setRecent)]).catch(() => {});
  const onAuth = (d) => { token = d.token; store.set("ts_token", d.token); setUser(d.user); refresh(); };
  const logout = () => { token = null; store.del("ts_token"); store.del("ts_tab"); setUser(null); setTab("home"); };
  useEffect(() => { if (!token) return; api("/api/me").then((u) => { setUser(u); refresh(); }).catch((x) => { if (x.message.includes("log in")) { token = null; store.del("ts_token"); } }).finally(() => setReady(true)); }, []);
  useEffect(() => { store.set("ts_tab", tab); }, [tab]);
  if (!ready) return <div className="wrap"><p className="small">Loading…</p></div>;
  if (!user) return <div className="wrap"><Auth onAuth={onAuth} /></div>;
  return <div className="wrap">
    {tab === "home" && <><div className="top"><span className="logo">🛡 TrustShield</span><span className="small">Demo</span></div><div className="card bal"><div>Hello, {user.name}</div><div>Available Balance</div><h1>{taka(user.balance)}</h1><button className="alt" onClick={() => setTab("send")}>Send Money</button></div>
      <div className="card">🛡 TrustShield Protection Active<div className="small">Your transactions are automatically checked for scams.</div></div>
      <div className="card"><h3>Recent Transactions</h3>{recent.filter((x) => x.status === "COMPLETED").slice(0, 5).map((x) => <div className="row" key={x.direction + x.id}><span>{x.direction === "IN" ? "Received from " : "Sent to "}{x.counterparty_name}</span><b className={x.direction === "IN" ? "in" : ""}>{x.direction === "IN" ? "+" : "-"}{taka(x.amount)}</b></div>)}</div></>}
    {tab === "send" && <Send refresh={refresh} />}{tab === "history" && <History />}{tab === "report" && <Report />}{tab === "check" && <MsgCheck />}
    {tab === "security" && <Security user={user} logout={logout} />}{tab === "analyst" && user.is_analyst && <Analyst />}
    <nav>{[["home", "Home"], ["send", "Send"], ["history", "History"], ["report", "Report"], ["check", "Scam Check"], ["security", "Security"], ...(user.is_analyst ? [["analyst", "Analyst"]] : [])].map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</nav></div>;
}
