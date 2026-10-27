from fastapi import FastAPI, Depends, HTTPException, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from sqlalchemy.sql import func
from datetime import datetime, timedelta, date
import os
import uuid
import hashlib
import secrets
import json
from typing import Optional
from collections import defaultdict

try:
    from pywebpush import webpush, WebPushException
    HAS_PUSH = True
except ImportError:
    HAS_PUSH = False

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./renewals.db")

# Normalise for SQLAlchemy + psycopg3 (Neon / Postgres)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

VAPID_PUBLIC_KEY = os.getenv(
    "VAPID_PUBLIC_KEY",
    "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U"
)
VAPID_PRIVATE_KEY = os.getenv(
    "VAPID_PRIVATE_KEY",
    "UUxI4O8-FbRouAevSmBQ6o18hgE4nSG3qwvJTfKc-ls"
)
VAPID_CLAIMS = {"sub": "mailto:you@example.com"}

DOC_TYPES = [
    ("passport", "Passport"),
    ("driving_licence", "Driving licence"),
    ("insurance", "Insurance"),
    ("mot", "MOT"),
    ("subscription", "Subscription"),
    ("tax", "Tax / Self Assessment"),
    ("membership", "Membership"),
    ("warranty", "Warranty"),
    ("lease", "Lease / Tenancy"),
    ("other", "Other"),
]

TYPE_FIELDS = {
    "insurance": [
        ("provider", "Provider name", "text"),
        ("policy_number", "Policy number", "text"),
        ("auto_renewal", "Auto-renewal on", "checkbox"),
        ("cover_amount", "Cover amount", "text"),
        ("premium", "Premium / cost", "text"),
    ],
    "mot": [
        ("registration", "Vehicle registration", "text"),
        ("garage", "Garage / tester", "text"),
        ("mileage", "Mileage at last MOT", "text"),
    ],
    "passport": [
        ("passport_number", "Passport number", "text"),
        ("nationality", "Nationality", "text"),
    ],
    "driving_licence": [
        ("licence_number", "Licence number", "text"),
        ("categories", "Categories", "text"),
    ],
    "subscription": [
        ("provider", "Provider", "text"),
        ("cost", "Cost", "text"),
        ("billing_cycle", "Billing cycle (monthly/yearly)", "text"),
        ("auto_renewal", "Auto-renewal on", "checkbox"),
    ],
    "tax": [
        ("reference", "UTR / reference", "text"),
        ("hmrc_login", "HMRC login note", "text"),
    ],
    "membership": [
        ("provider", "Organisation", "text"),
        ("membership_number", "Membership number", "text"),
        ("cost", "Cost", "text"),
    ],
    "warranty": [
        ("provider", "Provider", "text"),
        ("item_covered", "Item covered", "text"),
        ("serial_number", "Serial number", "text"),
    ],
    "lease": [
        ("landlord", "Landlord / agent", "text"),
        ("property_address", "Property address", "text"),
        ("rent", "Rent amount", "text"),
    ],
    "other": [
        ("reference", "Reference number", "text"),
        ("provider", "Provider / issuer", "text"),
    ],
}

GROUP_COLORS = [
    "#0ea5e9", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981",
    "#ef4444", "#6366f1", "#14b8a6", "#f97316", "#64748b",
]


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    groups = relationship("Group", back_populates="owner", cascade="all, delete-orphan", foreign_keys="Group.user_id")
    push_subscriptions = relationship("PushSubscription", back_populates="user", cascade="all, delete-orphan")


class SessionToken(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user = relationship("User")


class Group(Base):
    __tablename__ = "groups"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    parent_id = Column(String, ForeignKey("groups.id"), nullable=True)
    name = Column(String, nullable=False)
    color = Column(String, default="#0ea5e9")
    created_at = Column(DateTime, default=func.now())
    owner = relationship("User", back_populates="groups", foreign_keys=[user_id])
    parent = relationship("Group", remote_side=[id], backref="subgroups")
    documents = relationship("Document", back_populates="group")
    shares = relationship("GroupShare", back_populates="group", cascade="all, delete-orphan")


class GroupShare(Base):
    __tablename__ = "group_shares"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    group_id = Column(String, ForeignKey("groups.id"), nullable=False)
    shared_with_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    can_edit = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    group = relationship("Group", back_populates="shares")
    shared_with = relationship("User", foreign_keys=[shared_with_user_id])


class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    group_id = Column(String, ForeignKey("groups.id"), nullable=True)
    name = Column(String, nullable=False)
    doc_type = Column(String, nullable=False)
    expiry_date = Column(DateTime, nullable=False)
    notes = Column(Text, nullable=True)
    reminder_days = Column(Integer, default=30)
    color = Column(String, nullable=True)
    extra_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    owner = relationship("User", back_populates="documents")
    group = relationship("Group", back_populates="documents")


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


def ensure_schema():
    inspector = inspect(engine)
    if not inspector.has_table("groups"):
        return
    if inspector.has_table("documents"):
        cols = [c["name"] for c in inspector.get_columns("documents")]
        for col, typ in [("group_id", "VARCHAR"), ("color", "VARCHAR"), ("extra_data", "TEXT")]:
            if col not in cols:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {typ}"))
                except Exception:
                    pass


ensure_schema()


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
    db.add(SessionToken(user_id=user_id, token=token, expires_at=expires))
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


def accessible_group_ids(user: User, db: Session):
    owned = [g.id for g in db.query(Group).filter(Group.user_id == user.id).all()]
    shared = [s.group_id for s in db.query(GroupShare).filter(GroupShare.shared_with_user_id == user.id).all()]
    return set(owned + shared)


def get_accessible_groups(user: User, db: Session):
    ids = accessible_group_ids(user, db)
    if not ids:
        return []
    return db.query(Group).filter(Group.id.in_(ids)).order_by(Group.name).all()


def get_user_documents(user: User, db: Session):
    own = db.query(Document).filter(Document.user_id == user.id).all()
    group_ids = accessible_group_ids(user, db)
    shared_docs = []
    if group_ids:
        shared_docs = (
            db.query(Document)
            .filter(Document.group_id.in_(group_ids), Document.user_id != user.id)
            .all()
        )
    return own + shared_docs


def send_push_to_user(user: User, title: str, body: str, db: Session):
    if not HAS_PUSH:
        return
    for sub in list(user.push_subscriptions):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
            )
        except Exception as e:
            print(f"Push failed: {e}")
            try:
                db.delete(sub)
            except Exception:
                pass
    db.commit()


def doc_color(doc: Document) -> str:
    if doc.group and doc.group.color:
        return doc.group.color
    return doc.color or "#64748b"


def parse_extra(doc: Document) -> dict:
    if not doc.extra_data:
        return {}
    try:
        return json.loads(doc.extra_data)
    except Exception:
        return {}


app = FastAPI(title="Renewals Tracker")

BASE_CSS = """
:root {
  --bg:#f1f5f9; --card:#fff; --text:#0f172a; --muted:#64748b;
  --border:#e2e8f0; --accent:#0ea5e9; --danger:#dc2626; --success:#059669;
}
* { box-sizing:border-box; }
body { margin:0; font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); padding-bottom:48px; }
.header { background:#fff; border-bottom:1px solid var(--border); padding:14px 18px; display:flex; justify-content:space-between; align-items:center; position:sticky; top:0; z-index:20; gap:10px; flex-wrap:wrap; }
.logo { font-weight:800; font-size:1.35rem; color:var(--accent); text-decoration:none; }
.nav { display:flex; gap:8px; flex-wrap:wrap; }
.nav a, .btn { text-decoration:none; padding:8px 14px; border-radius:8px; font-weight:600; font-size:0.9rem; border:none; cursor:pointer; display:inline-block; }
.btn-primary { background:var(--accent); color:#fff; }
.btn-outline { border:2px solid var(--accent); color:var(--accent); background:transparent; }
.btn-danger { background:#fee2e2; color:var(--danger); }
.btn-sm { padding:6px 10px; font-size:0.8rem; }
.container { max-width:900px; margin:24px auto; padding:0 16px; }
.card { background:var(--card); border-radius:12px; padding:18px; border:1px solid var(--border); margin-bottom:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
h1,h2,h3 { margin-top:0; }
input, select, textarea, button { padding:11px 12px; border:2px solid var(--border); border-radius:8px; font-size:1rem; width:100%; margin-bottom:10px; background:#fff; }
button { background:var(--accent); color:#fff; border:none; font-weight:700; cursor:pointer; }
label { display:block; font-size:0.85rem; font-weight:600; margin-bottom:4px; color:var(--muted); }
.req { color:var(--danger); }
.hint { font-size:0.85rem; color:var(--muted); margin-bottom:12px; }
.view-tabs { display:flex; gap:8px; margin-bottom:16px; }
.view-tabs a { flex:1; text-align:center; padding:10px; border-radius:8px; background:#e2e8f0; color:var(--text); text-decoration:none; font-weight:600; }
.view-tabs a.active { background:var(--accent); color:#fff; }
.group-block { border:1px solid var(--border); border-radius:10px; margin-bottom:12px; overflow:hidden; background:#fff; }
.group-header { display:flex; align-items:center; gap:10px; padding:12px 14px; cursor:pointer; background:#f8fafc; user-select:none; }
.group-header:hover { background:#f1f5f9; }
.color-dot { width:14px; height:14px; border-radius:50%; flex-shrink:0; }
.group-body { display:none; padding:0 12px 12px; }
.group-body.open { display:block; }
.subgroup { margin:8px 0 8px 12px; border-left:3px solid var(--border); padding-left:10px; }
.subgroup-header { font-weight:600; font-size:0.95rem; padding:8px 0; cursor:pointer; color:var(--muted); }
.doc-row { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; padding:10px 0; border-bottom:1px solid var(--border); }
.doc-row:last-child { border-bottom:none; }
.expiry-soon { color:var(--danger); font-weight:700; }
.expiry-ok { color:var(--success); }
.meta { font-size:0.85rem; color:var(--muted); }
.empty { text-align:center; color:var(--muted); padding:28px; }
.form-row { display:flex; gap:10px; flex-wrap:wrap; }
.form-row > * { flex:1; min-width:140px; }
.optional-box { background:#f8fafc; border:1px dashed var(--border); border-radius:8px; padding:12px; margin:12px 0; }
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
.cal-head { text-align:center; font-size:0.75rem; font-weight:700; color:var(--muted); padding:6px 0; }
.cal-day { min-height:72px; background:#fff; border:1px solid var(--border); border-radius:8px; padding:4px; }
.cal-day.other { opacity:0.4; }
.cal-day.today { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
.cal-num { font-size:0.75rem; font-weight:600; margin-bottom:2px; }
.cal-dots { display:flex; flex-wrap:wrap; gap:3px; }
.cal-dot { width:10px; height:10px; border-radius:50%; cursor:pointer; }
.modal-bg { display:none; position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:50; align-items:center; justify-content:center; padding:16px; }
.modal-bg.open { display:flex; }
.modal { background:#fff; border-radius:12px; padding:20px; max-width:420px; width:100%; max-height:80vh; overflow:auto; }
.chip { display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.75rem; background:#e0f2fe; color:#0369a1; margin-left:6px; }
.share-list { font-size:0.9rem; }
.share-list li { margin-bottom:6px; }
"""


def page(title: str, body: str, user: Optional[User] = None) -> str:
    if user:
        nav = (
            '<a href="/dashboard" class="btn btn-outline btn-sm">Dashboard</a>'
            '<a href="/add" class="btn btn-primary btn-sm">Add item</a>'
            '<a href="/groups" class="btn btn-outline btn-sm">Groups</a>'
            '<a href="/logout" class="btn btn-outline btn-sm">Logout</a>'
        )
    else:
        nav = (
            '<a href="/login" class="btn btn-outline btn-sm">Login</a>'
            '<a href="/register" class="btn btn-primary btn-sm">Register</a>'
        )
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
    <div class="nav">{nav}</div>
  </header>
  <div class="container">{body}</div>
  <script>
  if ('serviceWorker' in navigator) {{
    navigator.serviceWorker.register('/sw.js').catch(() => {{}});
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
    <div class="card" style="text-align:center;padding:40px 20px;">
      <h1>Never miss a renewal</h1>
      <p class="hint">Track passports, insurance, MOT, subscriptions and more. Group them, share with family, get push reminders.</p>
      <a href="/register" class="btn btn-primary" style="width:auto;padding:14px 28px;">Get started free</a>
    </div>
    """
    return page("Home", body, user)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/dashboard")
    body = """
    <div class="card" style="max-width:420px;margin:20px auto;">
      <h2>Create account</h2>
      <form method="post" action="/register">
        <label>Email <span class="req">*</span></label>
        <input name="email" type="email" required>
        <label>Password <span class="req">*</span></label>
        <input name="password" type="password" required minlength="6">
        <button type="submit">Register</button>
      </form>
      <p style="text-align:center;"><a href="/login">Already have an account?</a></p>
    </div>
    """
    return page("Register", body)


@app.post("/register")
def register(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == email.strip().lower()).first():
        return RedirectResponse("/register", status_code=302)
    user = User(email=email.strip().lower(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    token = create_session(db, user.id)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session_token", token, httponly=True, max_age=30 * 24 * 3600, samesite="lax")
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/dashboard")
    body = """
    <div class="card" style="max-width:420px;margin:20px auto;">
      <h2>Login</h2>
      <form method="post" action="/login">
        <label>Email</label>
        <input name="email" type="email" required>
        <label>Password</label>
        <input name="password" type="password" required>
        <button type="submit">Login</button>
      </form>
      <p style="text-align:center;"><a href="/register">Need an account?</a></p>
    </div>
    """
    return page("Login", body)


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse("/login", status_code=302)
    token = create_session(db, user.id)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session_token", token, httponly=True, max_age=30 * 24 * 3600, samesite="lax")
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


def _doc_row_html(d: Document, today: date, current_user_id: str) -> str:
    days = (d.expiry_date.date() - today).days
    cls = "expiry-soon" if days <= (d.reminder_days or 30) else "expiry-ok"
    shared = "" if d.user_id == current_user_id else '<span class="chip">shared</span>'
    color = doc_color(d)
    return f"""
    <div class="doc-row">
      <div>
        <span class="color-dot" style="background:{color};display:inline-block;vertical-align:middle;margin-right:6px;"></span>
        <strong>{d.name}</strong> {shared}
        <div class="meta">{d.doc_type}</div>
        <span class="{cls}">Expires {d.expiry_date.strftime("%d %b %Y")} ({days}d)</span>
      </div>
      <div style="display:flex;gap:6px;">
        <a href="/documents/{d.id}/edit" class="btn btn-outline btn-sm">Edit</a>
        <form method="post" action="/documents/{d.id}/delete" style="margin:0;width:auto;" onsubmit="return confirm('Delete?')">
          <button type="submit" class="btn btn-danger btn-sm" style="width:auto;">Delete</button>
        </form>
      </div>
    </div>
    """


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), view: str = "list"):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    docs = get_user_documents(user, db)
    groups = get_accessible_groups(user, db)
    top_groups = [g for g in groups if g.parent_id is None]
    subgroups_by_parent = defaultdict(list)
    for g in groups:
        if g.parent_id:
            subgroups_by_parent[g.parent_id].append(g)

    docs_by_group = defaultdict(list)
    ungrouped = []
    for d in docs:
        if d.group_id:
            docs_by_group[d.group_id].append(d)
        else:
            ungrouped.append(d)

    has_push = len(user.push_subscriptions) > 0
    today = date.today()
    view = "calendar" if view == "calendar" else "list"

    list_html = ""
    if view == "list":
        for g in top_groups:
            subs = subgroups_by_parent.get(g.id, [])
            items_direct = docs_by_group.get(g.id, [])
            sub_items_count = sum(len(docs_by_group.get(s.id, [])) for s in subs)
            total = len(items_direct) + sub_items_count
            body_parts = []
            for s in subs:
                s_docs = docs_by_group.get(s.id, [])
                s_rows = "".join(_doc_row_html(d, today, user.id) for d in sorted(s_docs, key=lambda x: x.expiry_date))
                body_parts.append(f"""
                <div class="subgroup">
                  <div class="subgroup-header" onclick="this.nextElementSibling.classList.toggle('open')">
                    ▸ {s.name} ({len(s_docs)})
                  </div>
                  <div class="group-body">{s_rows or '<div class="meta">No items</div>'}</div>
                </div>
                """)
            direct_rows = "".join(_doc_row_html(d, today, user.id) for d in sorted(items_direct, key=lambda x: x.expiry_date))
            if direct_rows:
                body_parts.append(direct_rows)
            list_html += f"""
            <div class="group-block">
              <div class="group-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <span class="color-dot" style="background:{g.color}"></span>
                <strong style="flex:1;">{g.name}</strong>
                <span class="meta">{total} item{'s' if total != 1 else ''}</span>
                <span class="meta">▾</span>
              </div>
              <div class="group-body open">{''.join(body_parts) or '<div class="meta">Empty group</div>'}</div>
            </div>
            """
        if ungrouped:
            rows = "".join(_doc_row_html(d, today, user.id) for d in sorted(ungrouped, key=lambda x: x.expiry_date))
            list_html += f"""
            <div class="group-block">
              <div class="group-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <span class="color-dot" style="background:#94a3b8"></span>
                <strong style="flex:1;">Ungrouped</strong>
                <span class="meta">{len(ungrouped)}</span>
                <span class="meta">▾</span>
              </div>
              <div class="group-body open">{rows}</div>
            </div>
            """
        if not list_html:
            list_html = '<div class="empty">No items yet. <a href="/add">Add one</a> or create a <a href="/groups">group</a>.</div>'

    cal_html = ""
    if view == "calendar":
        year, month = today.year, today.month
        first = date(year, month, 1)
        start_pad = first.weekday()
        if month == 12:
            next_m = date(year + 1, 1, 1)
        else:
            next_m = date(year, month + 1, 1)
        days_in_month = (next_m - first).days
        by_day = defaultdict(list)
        for d in docs:
            if d.expiry_date.year == year and d.expiry_date.month == month:
                by_day[d.expiry_date.day].append(d)
        cells = []
        for _ in range(start_pad):
            cells.append('<div class="cal-day other"></div>')
        for day in range(1, days_in_month + 1):
            is_today = day == today.day
            dots = ""
            for d in by_day.get(day, []):
                c = doc_color(d)
                extra = parse_extra(d)
                tip = f"{d.name} · {d.doc_type}"
                payload = json.dumps({
                    "id": d.id,
                    "name": d.name,
                    "type": d.doc_type,
                    "expiry": d.expiry_date.strftime("%d %b %Y"),
                    "notes": d.notes or "",
                    "extra": extra,
                    "color": c,
                })
                dots += f'<span class="cal-dot" style="background:{c}" title="{tip}" onclick=\'showDoc({payload})\'></span>'
            cells.append(
                f'<div class="cal-day{" today" if is_today else ""}"><div class="cal-num">{day}</div><div class="cal-dots">{dots}</div></div>'
            )
        while len(cells) % 7:
            cells.append('<div class="cal-day other"></div>')
        heads = "".join(f'<div class="cal-head">{h}</div>' for h in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        cal_html = f"""
        <div class="card">
          <h3 style="margin:0 0 12px;">{first.strftime("%B %Y")}</h3>
          <div class="cal-grid">{heads}{''.join(cells)}</div>
          <p class="hint" style="margin-top:12px;">Tap a coloured dot for details.</p>
        </div>
        <div class="modal-bg" id="docModal" onclick="if(event.target===this)this.classList.remove('open')">
          <div class="modal" id="docModalBody"></div>
        </div>
        <script>
        function showDoc(d) {{
          let extra = '';
          if (d.extra) {{
            for (const [k,v] of Object.entries(d.extra)) {{
              if (v === true) extra += `<div><strong>${{k}}</strong>: Yes</div>`;
              else if (v) extra += `<div><strong>${{k}}</strong>: ${{v}}</div>`;
            }}
          }}
          document.getElementById('docModalBody').innerHTML = `
            <h3 style="margin-top:0;">${{d.name}}</h3>
            <div class="meta">${{d.type}} · expires ${{d.expiry}}</div>
            ${{d.notes ? `<p>${{d.notes}}</p>` : ''}}
            ${{extra}}
            <button onclick="document.getElementById('docModal').classList.remove('open')" style="margin-top:12px;">Close</button>
          `;
          document.getElementById('docModal').classList.add('open');
        }}
        </script>
        """

    if not has_push:
        push_btn = f"""
        <button type="button" id="pushBtn" onclick="enablePush()" class="btn btn-primary" style="width:auto;margin-bottom:12px;">Enable push reminders</button>
        <script>
        async function enablePush() {{
          if (!('serviceWorker' in navigator) || !('PushManager' in window)) {{
            alert('Push not supported in this browser');
            return;
          }}
          try {{
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
            const btn = document.getElementById('pushBtn');
            if (btn) btn.remove();
            alert('Push enabled.');
          }} catch (e) {{
            alert('Could not enable push: ' + e.message);
          }}
        }}
        function urlBase64ToUint8Array(base64String) {{
          const padding = '='.repeat((4 - base64String.length % 4) % 4);
          const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
          const rawData = window.atob(base64);
          return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
        }}
        </script>
        """
    else:
        push_btn = '<p class="hint">✓ Push reminders enabled</p>'

    list_active = "active" if view == "list" else ""
    cal_active = "active" if view == "calendar" else ""
    body = f"""
    <h2>Dashboard</h2>
    {push_btn}
    <div class="view-tabs">
      <a href="/dashboard?view=list" class="{list_active}">List</a>
      <a href="/dashboard?view=calendar" class="{cal_active}">Calendar</a>
    </div>
    {list_html if view == "list" else cal_html}
    """
    return page("Dashboard", body, user)


@app.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    groups = db.query(Group).filter(Group.user_id == user.id, Group.parent_id == None).order_by(Group.name).all()
    shared_in = (
        db.query(Group)
        .join(GroupShare, GroupShare.group_id == Group.id)
        .filter(GroupShare.shared_with_user_id == user.id, Group.parent_id == None)
        .all()
    )

    def group_card(g: Group, is_owner: bool) -> str:
        subs = db.query(Group).filter(Group.parent_id == g.id).order_by(Group.name).all()
        sub_html = "".join(f'<div class="meta" style="margin:4px 0 4px 12px;">↳ {s.name}</div>' for s in subs)
        shares = db.query(GroupShare).filter(GroupShare.group_id == g.id).all()
        share_html = ""
        if shares and is_owner:
            share_html = "<ul class='share-list'>" + "".join(
                f"<li>{s.shared_with.email if s.shared_with else s.shared_with_user_id} "
                f"<form method='post' action='/groups/{g.id}/unshare' style='display:inline;'>"
                f"<input type='hidden' name='user_id' value='{s.shared_with_user_id}'>"
                f"<button type='submit' class='btn btn-danger btn-sm' style='width:auto;'>Remove</button></form></li>"
                for s in shares
            ) + "</ul>"
        if is_owner:
            color_opts = "".join(
                f'<option value="{c}" {"selected" if c == g.color else ""}>{c}</option>'
                for c in GROUP_COLORS
            )
            owner_actions = f"""
            <form method="post" action="/groups/{g.id}/update" class="form-row" style="margin-top:10px;">
              <input name="name" value="{g.name}" required>
              <select name="color">{color_opts}</select>
              <button type="submit" class="btn-sm" style="width:auto;">Save</button>
            </form>
            <form method="post" action="/groups/{g.id}/subgroup" style="margin-top:8px;" class="form-row">
              <input name="name" placeholder="New subgroup name" required>
              <button type="submit" class="btn-sm" style="width:auto;">Add subgroup</button>
            </form>
            <form method="post" action="/groups/{g.id}/share" style="margin-top:8px;" class="form-row">
              <input name="email" type="email" placeholder="Share with email" required>
              <button type="submit" class="btn-sm" style="width:auto;">Share group</button>
            </form>
            {share_html}
            <form method="post" action="/groups/{g.id}/delete" onsubmit="return confirm('Delete group and unlink items?')" style="margin-top:8px;">
              <button type="submit" class="btn btn-danger btn-sm" style="width:auto;">Delete group</button>
            </form>
            """
        else:
            owner_actions = '<span class="chip">Shared with you</span>'

        return f"""
        <div class="card">
          <div style="display:flex;align-items:center;gap:10px;">
            <span class="color-dot" style="background:{g.color}"></span>
            <strong>{g.name}</strong>
          </div>
          {sub_html}
          {owner_actions}
        </div>
        """

    owned_html = "".join(group_card(g, True) for g in groups) or '<div class="empty">No groups yet.</div>'
    shared_html = "".join(group_card(g, False) for g in shared_in)
    color_opts = "".join(f'<option value="{c}">{c}</option>' for c in GROUP_COLORS)

    body = f"""
    <h2>Groups</h2>
    <div class="card">
      <h3>Create group</h3>
      <form method="post" action="/groups" class="form-row">
        <input name="name" placeholder="e.g. Car, Household" required>
        <select name="color">{color_opts}</select>
        <button type="submit" style="width:auto;">Create</button>
      </form>
    </div>
    <h3>Your groups</h3>
    {owned_html}
    {"<h3>Shared with you</h3>" + shared_html if shared_html else ""}
    """
    return page("Groups", body, user)


@app.post("/groups")
def create_group(name: str = Form(...), color: str = Form("#0ea5e9"), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    db.add(Group(user_id=user.id, name=name.strip(), color=color or "#0ea5e9"))
    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/update")
def update_group(group_id: str, name: str = Form(...), color: str = Form("#0ea5e9"), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)
    g.name = name.strip()
    g.color = color
    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/subgroup")
def create_subgroup(group_id: str, name: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    parent = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id, Group.parent_id == None).first()
    if not parent:
        raise HTTPException(404)
    db.add(Group(user_id=user.id, parent_id=parent.id, name=name.strip(), color=parent.color))
    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/share")
def share_group(group_id: str, email: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)
    other = db.query(User).filter(User.email == email.strip().lower()).first()
    if not other:
        raise HTTPException(400, detail="User not found. They need to register first.")
    if other.id == user.id:
        raise HTTPException(400, detail="Cannot share with yourself")
    existing = db.query(GroupShare).filter(GroupShare.group_id == g.id, GroupShare.shared_with_user_id == other.id).first()
    if not existing:
        db.add(GroupShare(group_id=g.id, shared_with_user_id=other.id))
        for sub in db.query(Group).filter(Group.parent_id == g.id).all():
            if not db.query(GroupShare).filter(GroupShare.group_id == sub.id, GroupShare.shared_with_user_id == other.id).first():
                db.add(GroupShare(group_id=sub.id, shared_with_user_id=other.id))
        db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/unshare")
def unshare_group(group_id: str, user_id: str = Form(...), request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)
    db.query(GroupShare).filter(GroupShare.group_id == g.id, GroupShare.shared_with_user_id == user_id).delete()
    for sub in db.query(Group).filter(Group.parent_id == g.id).all():
        db.query(GroupShare).filter(GroupShare.group_id == sub.id, GroupShare.shared_with_user_id == user_id).delete()
    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/delete")
def delete_group(group_id: str, request: Request = None, db: Session = Depends(get_db)):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)
    for d in db.query(Document).filter(Document.group_id == g.id).all():
        d.group_id = None
    for sub in db.query(Group).filter(Group.parent_id == g.id).all():
        for d in db.query(Document).filter(Document.group_id == sub.id).all():
            d.group_id = None
        db.delete(sub)
    db.delete(g)
    db.commit()
    return RedirectResponse("/groups", status_code=302)


def _type_options(selected: str = "") -> str:
    return "".join(
        f'<option value="{k}" {"selected" if k == selected else ""}>{label}</option>'
        for k, label in DOC_TYPES
    )


def _group_options(user: User, db: Session, selected: Optional[str] = None) -> str:
    groups = get_accessible_groups(user, db)
    top = [g for g in groups if g.parent_id is None]
    opts = '<option value="">— No group —</option>'
    for g in top:
        opts += f'<option value="{g.id}" {"selected" if selected == g.id else ""}>{g.name}</option>'
        for s in [x for x in groups if x.parent_id == g.id]:
            opts += f'<option value="{s.id}" {"selected" if selected == s.id else ""}>↳ {g.name} / {s.name}</option>'
    return opts


@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    type_fields_js = json.dumps({k: [{"key": a, "label": b, "type": c} for a, b, c in v] for k, v in TYPE_FIELDS.items()})
    body = f"""
    <div class="card">
      <h2>Add item</h2>
      <p class="hint"><span class="req">*</span> = mandatory. Everything else is optional.</p>
      <form method="post" action="/documents" id="docForm">
        <label>Name <span class="req">*</span></label>
        <input name="name" placeholder="e.g. Car insurance" required>
        <label>Type <span class="req">*</span></label>
        <select name="doc_type" id="docType" required onchange="updateExtraFields()">
          {_type_options("insurance")}
        </select>
        <label>Expiry date <span class="req">*</span></label>
        <input name="expiry_date" type="date" required>
        <label>Remind me this many days before <span class="req">*</span></label>
        <input name="reminder_days" type="number" value="30" min="1" max="365" required>
        <label>Group / subgroup</label>
        <select name="group_id">{_group_options(user, db)}</select>
        <label>Colour (only if not in a group)</label>
        <select name="color">
          <option value="">Use default / group colour</option>
          {"".join(f'<option value="{c}">{c}</option>' for c in GROUP_COLORS)}
        </select>
        <div id="extraFields"></div>
        <label>Notes</label>
        <textarea name="notes" rows="3" placeholder="Optional notes"></textarea>
        <button type="submit">Save</button>
      </form>
    </div>
    <script>
    const TYPE_FIELDS = {type_fields_js};
    function updateExtraFields() {{
      const t = document.getElementById('docType').value;
      const fields = TYPE_FIELDS[t] || [];
      let html = '';
      if (fields.length) {{
        html = '<div class="optional-box"><p class="hint" style="margin-top:0;">Optional fields for this type</p>';
        fields.forEach(f => {{
          if (f.type === 'checkbox') {{
            html += `<label style="display:flex;align-items:center;gap:8px;font-weight:500;">
              <input type="checkbox" name="extra_${{f.key}}" value="true" style="width:auto;margin:0;"> ${{f.label}}</label>`;
          }} else {{
            html += `<label>${{f.label}}</label><input name="extra_${{f.key}}">`;
          }}
        }});
        html += '</div>';
      }}
      document.getElementById('extraFields').innerHTML = html;
    }}
    updateExtraFields();
    </script>
    """
    return page("Add item", body, user)


@app.post("/documents")
async def create_document(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    form = await request.form()
    name = (form.get("name") or "").strip()
    doc_type = form.get("doc_type") or "other"
    expiry_date = form.get("expiry_date")
    reminder_days = int(form.get("reminder_days") or 30)
    notes = form.get("notes") or None
    group_id = form.get("group_id") or None
    color = form.get("color") or None
    if not name or not expiry_date:
        raise HTTPException(400, "Name and expiry date are required")
    exp = datetime.strptime(expiry_date, "%Y-%m-%d")
    if group_id and group_id not in accessible_group_ids(user, db):
        group_id = None
    extra = {}
    for key in form.keys():
        if key.startswith("extra_"):
            field = key[6:]
            val = form.get(key)
            if val is not None and val != "":
                extra[field] = True if val == "true" else val
    doc = Document(
        user_id=user.id,
        group_id=group_id or None,
        name=name,
        doc_type=doc_type,
        expiry_date=exp,
        reminder_days=reminder_days,
        notes=notes,
        color=color if not group_id else None,
        extra_data=json.dumps(extra) if extra else None,
    )
    db.add(doc)
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/documents/{doc_id}/edit", response_class=HTMLResponse)
def edit_document_page(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404)
    if doc.user_id != user.id:
        if not doc.group_id or doc.group_id not in accessible_group_ids(user, db):
            raise HTTPException(403)
    extra = parse_extra(doc)
    type_fields_js = json.dumps({k: [{"key": a, "label": b, "type": c} for a, b, c in v] for k, v in TYPE_FIELDS.items()})
    body = f"""
    <div class="card">
      <h2>Edit item</h2>
      <p class="hint"><span class="req">*</span> = mandatory.</p>
      <form method="post" action="/documents/{doc.id}/edit">
        <label>Name <span class="req">*</span></label>
        <input name="name" value="{doc.name}" required>
        <label>Type <span class="req">*</span></label>
        <select name="doc_type" id="docType" required onchange="updateExtraFields()">
          {_type_options(doc.doc_type)}
        </select>
        <label>Expiry date <span class="req">*</span></label>
        <input name="expiry_date" type="date" value="{doc.expiry_date.strftime('%Y-%m-%d')}" required>
        <label>Remind me this many days before <span class="req">*</span></label>
        <input name="reminder_days" type="number" value="{doc.reminder_days}" min="1" max="365" required>
        <label>Group / subgroup</label>
        <select name="group_id">{_group_options(user, db, doc.group_id)}</select>
        <label>Colour (only if not in a group)</label>
        <select name="color">
          <option value="">Use default / group colour</option>
          {"".join(f'<option value="{c}" {"selected" if doc.color == c else ""}>{c}</option>' for c in GROUP_COLORS)}
        </select>
        <div id="extraFields"></div>
        <label>Notes</label>
        <textarea name="notes" rows="3">{doc.notes or ""}</textarea>
        <button type="submit">Save changes</button>
      </form>
    </div>
    <script>
    const TYPE_FIELDS = {type_fields_js};
    const EXISTING = {json.dumps(extra)};
    function updateExtraFields() {{
      const t = document.getElementById('docType').value;
      const fields = TYPE_FIELDS[t] || [];
      let html = '';
      if (fields.length) {{
        html = '<div class="optional-box"><p class="hint" style="margin-top:0;">Optional fields for this type</p>';
        fields.forEach(f => {{
          const v = EXISTING[f.key];
          if (f.type === 'checkbox') {{
            const ck = (v === true || v === 'true') ? 'checked' : '';
            html += `<label style="display:flex;align-items:center;gap:8px;font-weight:500;">
              <input type="checkbox" name="extra_${{f.key}}" value="true" ${{ck}} style="width:auto;margin:0;"> ${{f.label}}</label>`;
          }} else {{
            html += `<label>${{f.label}}</label><input name="extra_${{f.key}}" value="${{v || ''}}">`;
          }}
        }});
        html += '</div>';
      }}
      document.getElementById('extraFields').innerHTML = html;
    }}
    updateExtraFields();
    </script>
    """
    return page("Edit item", body, user)


@app.post("/documents/{doc_id}/edit")
async def edit_document(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404)
    if doc.user_id != user.id:
        if not doc.group_id or doc.group_id not in accessible_group_ids(user, db):
            raise HTTPException(403)
    form = await request.form()
    name = (form.get("name") or "").strip()
    doc_type = form.get("doc_type") or "other"
    expiry_date = form.get("expiry_date")
    reminder_days = int(form.get("reminder_days") or 30)
    notes = form.get("notes") or None
    group_id = form.get("group_id") or None
    color = form.get("color") or None
    if not name or not expiry_date:
        raise HTTPException(400, "Name and expiry date are required")
    exp = datetime.strptime(expiry_date, "%Y-%m-%d")
    if group_id and group_id not in accessible_group_ids(user, db):
        group_id = doc.group_id
    extra = {}
    for key in form.keys():
        if key.startswith("extra_"):
            field = key[6:]
            val = form.get(key)
            if val is not None and val != "":
                extra[field] = True if val == "true" else val
    doc.name = name
    doc.doc_type = doc_type
    doc.expiry_date = exp
    doc.reminder_days = reminder_days
    doc.notes = notes
    doc.group_id = group_id or None
    doc.color = color if not group_id else None
    doc.extra_data = json.dumps(extra) if extra else None
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/documents/{doc_id}/delete")
def delete_document(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(404)
    if doc.user_id != user.id:
        raise HTTPException(403, "Only the owner can delete")
    db.delete(doc)
    db.commit()
    return RedirectResponse("/dashboard", status_code=302)


@app.post("/push/subscribe")
async def subscribe_push(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    data = await request.json()
    existing = db.query(PushSubscription).filter(
        PushSubscription.user_id == user.id,
        PushSubscription.endpoint == data["endpoint"]
    ).first()
    if not existing:
        db.add(PushSubscription(
            user_id=user.id,
            endpoint=data["endpoint"],
            p256dh=data["keys"]["p256dh"],
            auth=data["keys"]["auth"],
        ))
        db.commit()
    return {"ok": True}


@app.get("/push/test")
def test_push(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401, detail="Not authenticated — log in first")
    if not user.push_subscriptions:
        return {"sent": False, "error": "No push subscription. Enable push on the dashboard first."}
    send_push_to_user(user, "Test reminder", "This is a test push from Renewals Tracker", db)
    return {"sent": True}


@app.get("/admin/check-reminders")
def check_reminders(db: Session = Depends(get_db)):
    today = date.today()
    docs = db.query(Document).all()
    sent = 0
    for doc in docs:
        days_left = (doc.expiry_date.date() - today).days
        if 0 <= days_left <= (doc.reminder_days or 30):
            owner = db.query(User).filter(User.id == doc.user_id).first()
            if owner:
                send_push_to_user(
                    owner,
                    f"{doc.name} expires soon",
                    f"{doc.name} ({doc.doc_type}) expires in {days_left} day(s)",
                    db,
                )
                sent += 1
    return {"checked": len(docs), "reminders_sent": sent}


@app.get("/manifest.json")
def manifest():
    return {
        "name": "Renewals Tracker",
        "short_name": "Renewals",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f1f5f9",
        "theme_color": "#0ea5e9",
        "icons": [],
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
