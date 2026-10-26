from fastapi import FastAPI, Depends, HTTPException, Request, Response, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from sqlalchemy.sql import func
from datetime import datetime, timedelta, date
import os
import uuid
import hashlib
import secrets
import json
from typing import Optional
from pywebpush import webpush, WebPushException

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./renewals.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# VAPID keys – generate your own for production
# You can generate with: npx web-push generate-vapid-keys
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "UUxI4O8-FbRouAevSmBQ6o18hgE4nSG3qwvJTfKc-ls")
VAPID_CLAIMS = {"sub": "mailto:you@example.com"}

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    push_subscriptions = relationship("PushSubscription", back_populates="user", cascade="all, delete-orphan")

class SessionToken(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user = relationship("User")

class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    doc_type = Column(String, nullable=False)  # passport, insurance, mot, subscription, other
    expiry_date = Column(DateTime, nullable=False)
    notes = Column(Text, nullable=True)
    reminder_days = Column(Integer, default=30)
    created_at = Column(DateTime, default=func.now())
    owner = relationship("User", back_populates="documents")

class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    endpoint = Column(Text, nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    created_at = Column(DateTime, default=func.now())
    user = relationship("User", back_populates="push_subscriptions")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed

def create_session(db: Session, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(days=30)
    session = SessionToken(user_id=user_id, token=token, expires_at=expires)
    db.add(session)
    db.commit()
    return token

def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    session = db.query(SessionToken).filter(
        SessionToken.token == token,
        SessionToken.expires_at > datetime.utcnow()
    ).first()
    if not session:
        return None
    return db.query(User).filter(User.id == session.user_id).first()

def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def send_push_to_user(user: User, title: str, body: str, db: Session):
    for sub in user.push_subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth}
                },
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
        except WebPushException as e:
            print(f"Push failed: {e}")
            # Optionally delete invalid subscription
            db.delete(sub)
    db.commit()

app = FastAPI(title="Renewals Tracker")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

BASE_CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#1e293b; --muted:#64748b; --border:#e2e8f0; --accent:#0ea5e9; --danger:#dc2626; --success:#059669; }
* { box-sizing:border-box; }
body { margin:0; font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); padding-bottom:40px; }
.header { background:white; border-bottom:1px solid var(--border); padding:16px 20px; display:flex; justify-content:space-between; align-items:center; position:sticky; top:0; z-index:10; }
.logo { font-weight:800; font-size:1.4rem; color:var(--accent); text-decoration:none; }
.nav a, .btn { text-decoration:none; padding:8px 14px; border-radius:8px; font-weight:600; }
.btn-primary { background:var(--accent); color:white; }
.btn-outline { border:2px solid var(--accent); color:var(--accent); }
.container { max-width:800px; margin:30px auto; padding:0 16px; }
.card { background:var(--card); border-radius:12px; padding:20px; border:1px solid var(--border); margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
.doc-item { display:flex; justify-content:space-between; align-items:center; padding:14px 0; border-bottom:1px solid var(--border); gap:12px; }
.doc-item:last-child { border-bottom:none; }
.expiry-soon { color:var(--danger); font-weight:700; }
.expiry-ok { color:var(--success); }
input, select, textarea, button { padding:12px; border:2px solid var(--border); border-radius:8px; font-size:1rem; width:100%; margin-bottom:12px; }
button { background:var(--accent); color:white; border:none; font-weight:700; cursor:pointer; }
.form-row { display:flex; gap:10px; flex-wrap:wrap; }
.form-row > * { flex:1; min-width:140px; margin-bottom:0; }
.empty { text-align:center; color:var(--muted); padding:30px; }
"""

def page(title: str, body: str, user: Optional[User] = None) -> str:
    nav = ""
    if user:
        nav = '<a href="/dashboard" class="btn btn-outline">Dashboard</a> <a href="/add" class="btn btn-primary">Add document</a> <a href="/logout" class="btn btn-outline">Logout</a>'
    else:
        nav = '<a href="/login" class="btn btn-outline">Login</a> <a href="/register" class="btn btn-primary">Register</a>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} · Renewals</title>
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#0ea5e9">
  <style>{BASE_CSS}</style>
</head>
<body>
  <header class="header">
    <a href="/" class="logo">Renewals</a>
    <div>{nav}</div>
  </header>
  <div class="container">{body}</div>
  <script>
  if ('serviceWorker' in navigator) {{
    navigator.serviceWorker.register('/sw.js').catch(e => console.log('SW reg failed', e));
  }}
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/dashboard")
    body = """
    <div class="card" style="text-align:center; padding:40px 20px;">
      <h1>Never miss a renewal</h1>
      <p style="color:var(--muted);">Track passports, insurance, MOT, subscriptions and more. Get push reminders before they expire.</p>
      <a href="/register" class="btn btn-primary" style="display:inline-block; width:auto; padding:14px 28px;">Get started</a>
    </div>
    """
    return page("Home", body, user)

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user: return RedirectResponse("/dashboard")
    body = """
    <div class="card" style="max-width:420px; margin:20px auto;">
      <h2>Create account</h2>
      <form method="post" action="/register">
        <input name="email" type="email" placeholder="Email" required>
        <input name="password" type="password" placeholder="Password (min 6)" required minlength="6">
        <button type="submit">Register</button>
      </form>
      <p style="text-align:center;"><a href="/login">Already have an account?</a></p>
    </div>
    """
    return page("Register", body, user)

@app.post("/register")
def register(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == email).first():
        return RedirectResponse("/register")
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    token = create_session(db, user.id)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session_token", token, httponly=True, max_age=30*24*3600, samesite="lax")
    return resp

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user: return RedirectResponse("/dashboard")
    body = """
    <div class="card" style="max-width:420px; margin:20px auto;">
      <h2>Login</h2>
      <form method="post" action="/login">
        <input name="email" type="email" placeholder="Email" required>
        <input name="password" type="password" placeholder="Password" required>
        <button type="submit">Login</button>
      </form>
    </div>
    """
    return page("Login", body, user)

@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse("/login")
    token = create_session(db, user.id)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session_token", token, httponly=True, max_age=30*24*3600, samesite="lax")
    return resp

@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session_token")
    if token:
        db.query(SessionToken).filter(SessionToken.token == token).delete()
        db.commit()
    resp = RedirectResponse("/")
    resp.delete_cookie("session_token")
    return resp

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user: return RedirectResponse("/login")
    docs = db.query(Document).filter(Document.user_id == user.id).order_by(Document.expiry_date).all()
    today = date.today()
    items = ""
    for d in docs:
        days = (d.expiry_date.date() - today).days
        cls = "expiry-soon" if days <= d.reminder_days else "expiry-ok"
        items += f'''
        <div class="doc-item">
          <div>
            <strong>{d.name}</strong> <small style="color:var(--muted);">({d.doc_type})</small><br>
            <span class="{cls}">Expires {d.expiry_date.strftime("%d %b %Y")} ({days} days)</span>
            {f"<br><small>{d.notes}</small>" if d.notes else ""}
          </div>
          <form method="post" action="/documents/{d.id}/delete" style="margin:0; width:auto;">
            <button type="submit" style="background:#fee2e2; color:var(--danger); width:auto; padding:8px 12px;">Delete</button>
          </form>
        </div>'''
    body = f"""
    <h2>Your documents</h2>
    <div class="card">
      <button onclick="enablePush()" class="btn btn-primary" style="width:auto; margin-bottom:16px;">Enable push reminders</button>
      {items or '<div class="empty">No documents yet. <a href="/add">Add one</a></div>'}
    </div>
    <script>
    async function enablePush() {{
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) {{
        alert('Push not supported in this browser');
        return;
      }}
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({{
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array('{VAPID_PUBLIC_KEY}')
      }});
      await fetch('/push/subscribe', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(sub)
      }});
      alert('Push enabled. You will get reminders before documents expire.');
    }}
    function urlBase64ToUint8Array(base64String) {{
      const padding = '='.repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
      const rawData = window.atob(base64);
      return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
    }}
    </script>
    """
    return page("Dashboard", body, user)

@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user: return RedirectResponse("/login")
    body = """
    <div class="card">
      <h2>Add document or service</h2>
      <form method="post" action="/documents">
        <input name="name" placeholder="Name (e.g. Car Insurance, Passport)" required>
        <select name="doc_type" required>
          <option value="passport">Passport</option>
          <option value="driving_licence">Driving licence</option>
          <option value="insurance">Insurance</option>
          <option value="mot">MOT</option>
          <option value="subscription">Subscription</option>
          <option value="other">Other</option>
        </select>
        <label>Expiry date</label>
        <input name="expiry_date" type="date" required>
        <label>Remind me this many days before</label>
        <input name="reminder_days" type="number" value="30" min="1" max="365">
        <textarea name="notes" placeholder="Optional notes" rows="3"></textarea>
        <button type="submit">Save</button>
      </form>
    </div>
    """
    return page("Add document", body, user)

@app.post("/documents")
def create_document(
    name: str = Form(...),
    doc_type: str = Form(...),
    expiry_date: str = Form(...),
    reminder_days: int = Form(30),
    notes: Optional[str] = Form(None),
    request: Request = None,
    db: Session = Depends(get_db)
):
    user = require_user(request, db)
    try:
        exp = datetime.strptime(expiry_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date")
    doc = Document(
        user_id=user.id,
        name=name,
        doc_type=doc_type,
        expiry_date=exp,
        reminder_days=reminder_days,
        notes=notes
    )
    db.add(doc)
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)

@app.post("/documents/{doc_id}/delete")
def delete_document(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    doc = db.query(Document).filter(Document.id == doc_id, Document.user_id == user.id).first()
    if not doc:
        raise HTTPException(404)
    db.delete(doc)
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)

@app.post("/push/subscribe")
async def subscribe_push(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    data = await request.json()
    sub = PushSubscription(
        user_id=user.id,
        endpoint=data["endpoint"],
        p256dh=data["keys"]["p256dh"],
        auth=data["keys"]["auth"]
    )
    db.add(sub)
    db.commit()
    return {"ok": True}

@app.get("/push/test")
def test_push(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401)
    send_push_to_user(user, "Test reminder", "This is a test push from Renewals Tracker", db)
    return {"sent": True}

@app.get("/manifest.json")
def manifest():
    return {
        "name": "Renewals Tracker",
        "short_name": "Renewals",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": "#0ea5e9",
        "icons": []
    }

@app.get("/sw.js")
def service_worker():
    sw = """
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {title: 'Renewal', body: 'You have a document expiring soon'};
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icon.png'
    })
  );
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/dashboard'));
});
"""
    return Response(content=sw, media_type="application/javascript")

@app.get("/health")
def health():
    return {"status": "ok"}

# Simple daily check endpoint you can hit with a cron (or call manually)
@app.get("/admin/check-reminders")
def check_reminders(db: Session = Depends(get_db)):
    today = date.today()
    docs = db.query(Document).all()
    sent = 0
    for doc in docs:
        days_left = (doc.expiry_date.date() - today).days
        if 0 <= days_left <= doc.reminder_days:
            send_push_to_user(
                doc.owner,
                f"{doc.name} expires soon",
                f"{doc.name} ({doc.doc_type}) expires in {days_left} days",
                db
            )
            sent += 1
    return {"checked": len(docs), "reminders_sent": sent}
