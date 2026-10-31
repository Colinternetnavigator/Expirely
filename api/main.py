from fastapi import FastAPI, Depends, HTTPException, Request, Response, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from sqlalchemy.sql import func
from datetime import datetime, timedelta, date
import os
import uuid
import hashlib
import secrets
import json
import html
from pathlib import Path
from typing import Optional
from collections import defaultdict

try:
    from pywebpush import webpush, WebPushException
    HAS_PUSH = True
except ImportError:
    HAS_PUSH = False


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./renewals.db")

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


# Nutify autumnal colour palette.
# The existing colours are retained so previously saved group colours still work.
GROUP_COLORS = [
    "#f97316",  # Orange
    "#7c2d12",  # Acorn brown
    "#f59e0b",  # Amber
    "#a16207",  # Ochre
    "#10b981",  # Emerald
    "#ef4444",  # Red
    "#6366f1",  # Indigo
    "#14b8a6",  # Teal
    "#ec4899",  # Pink
    "#64748b",  # Slate
    "#0ea5e9",  # Sky blue
    "#8b5cf6",  # Violet
]

COLOR_NAMES = {
    "#f97316": "Nutify orange",
    "#7c2d12": "Acorn brown",
    "#f59e0b": "Amber",
    "#a16207": "Ochre",
    "#10b981": "Emerald",
    "#ef4444": "Red",
    "#6366f1": "Indigo",
    "#14b8a6": "Teal",
    "#ec4899": "Pink",
    "#64748b": "Slate",
    "#0ea5e9": "Sky blue",
    "#8b5cf6": "Violet",
}

# Calendar icon choices. "dot" preserves the original default appearance.
ITEM_ICONS = [
    ("dot", "●", "Default dot"),
    ("house", "⌂", "House"),
    ("car", "🚗", "Car"),
    ("passport", "🛂", "Passport"),
    ("document", "📄", "Document"),
    ("insurance", "🛡️", "Insurance shield"),
    ("key", "🔑", "Key"),
    ("health", "✚", "Health"),
    ("money", "£", "Money"),
    ("subscription", "↻", "Subscription"),
    ("membership", "★", "Membership"),
    ("warranty", "🔧", "Warranty"),
    ("travel", "✈", "Travel"),
    ("pet", "🐾", "Pet"),
    ("heart", "♥", "Heart"),
    ("diamond", "◆", "Diamond"),
    ("triangle", "▲", "Triangle"),
    ("square", "■", "Square"),
    ("star", "★", "Star"),
    ("bell", "♟", "Other symbol"),
]

ICON_MAP = {
    key: symbol for key, symbol, label in ITEM_ICONS
}


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    plan = Column(String, default="free", nullable=False)  # free | unlimited
    email_verified = Column(Boolean, default=False, nullable=False)
    verification_token = Column(String, nullable=True)
    verification_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())
    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    groups = relationship(
        "Group", back_populates="owner", cascade="all, delete-orphan",
        foreign_keys="Group.user_id"
    )
    push_subscriptions = relationship(
        "PushSubscription", back_populates="user", cascade="all, delete-orphan"
    )


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
    color = Column(String, default="#f97316")
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
    icon = Column(String, nullable=False, default="dot")
    last_notified_at = Column(DateTime, nullable=True)
    snooze_until = Column(DateTime, nullable=True)
    extra_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    owner = relationship("User", back_populates="documents")
    group = relationship("Group", back_populates="documents")



class DocumentFile(Base):
    __tablename__ = "document_files"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    document_id = Column(String, ForeignKey("documents.id"), nullable=True)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=True)
    file_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())
    user = relationship("User")
    document = relationship("Document")

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
    """Safely add fields to existing SQLite/Postgres databases."""
    inspector = inspect(engine)

    if inspector.has_table("documents"):
        cols = [c["name"] for c in inspector.get_columns("documents")]
        additions = [
            ("group_id", "VARCHAR"),
            ("color", "VARCHAR"),
            ("extra_data", "TEXT"),
            ("icon", "VARCHAR NOT NULL DEFAULT 'dot'"),
            ("last_notified_at", "TIMESTAMP"),
            ("snooze_until", "TIMESTAMP"),
        ]

        for col, typ in additions:
            if col not in cols:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {typ}"))
                except Exception as exc:
                    print(f"Could not add documents.{col}: {exc}")

    if inspector.has_table("users"):
        cols = [c["name"] for c in inspector.get_columns("users")]
        user_additions = [
            ("plan", "VARCHAR NOT NULL DEFAULT 'free'"),
            ("email_verified", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("verification_token", "VARCHAR"),
            ("verification_expires", "TIMESTAMP"),
        ]
        for col, typ in user_additions:
            if col not in cols:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typ}"))
                except Exception as exc:
                    print(f"Could not add users.{col}: {exc}")

    if inspector.has_table("groups"):
        cols = [c["name"] for c in inspector.get_columns("groups")]
        if "color" not in cols:
            try:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE groups ADD COLUMN color VARCHAR"))
            except Exception as exc:
                print(f"Could not add groups.color: {exc}")


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
    shared = [
        s.group_id for s in db.query(GroupShare)
        .filter(GroupShare.shared_with_user_id == user.id).all()
    ]
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


def send_push_to_user(user: User, title: str, body: str, db: Session, extra: dict = None):
    if not HAS_PUSH:
        return
    payload = {"title": title, "body": body}
    if extra:
        payload.update(extra)
    for sub in list(user.push_subscriptions):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(payload),
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


def safe_json_for_html(value) -> str:
    """Keep JSON safe inside a single-quoted HTML onclick attribute."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("'", "\\u0027")
    )


def color_options(selected: Optional[str] = None, include_default: bool = False) -> str:
    options = ""
    if include_default:
        options += (
            '<option value="" '
            f'{"selected" if not selected else ""}>'
            'Use default / group colour</option>'
        )

    for colour in GROUP_COLORS:
        name = COLOR_NAMES.get(colour, colour)
        options += (
            f'<option value="{colour}" '
            f'style="color:{colour};font-weight:700;" '
            f'{"selected" if selected == colour else ""}>'
            f'● {name}</option>'
        )
    return options


def icon_options(selected: str = "dot") -> str:
    options = ""
    for key, symbol, label in ITEM_ICONS:
        options += (
            f'<option value="{key}" '
            f'{"selected" if key == selected else ""}>'
            f'{symbol} {label}</option>'
        )
    return options


app = FastAPI(title="Nutify")


# Support either "static" or "Static" for the logo directory.
@app.get("/brand-logo")
def brand_logo():
    candidates = [
        Path("static/Nutifylogo.png"),
        Path("Static/Nutifylogo.png"),
        Path(__file__).resolve().parent / "static" / "Nutifylogo.png",
        Path(__file__).resolve().parent / "Static" / "Nutifylogo.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return FileResponse(candidate, media_type="image/png")
    raise HTTPException(status_code=404, detail="Nutify logo not found")


BASE_CSS = """
:root {
  --bg:#fffaf5;
  --card:#ffffff;
  --text:#352319;
  --muted:#8b7769;
  --border:#eadfd4;
  --accent:#f97316;
  --accent-dark:#7c2d12;
  --danger:#dc2626;
  --success:#15803d;
}
* { box-sizing:border-box; }
body {
  margin:0;
  font-family:system-ui,-apple-system,sans-serif;
  background:var(--bg);
  color:var(--text);
  padding-bottom:48px;
}
.header {
  background:#fff;
  border-bottom:1px solid var(--border);
  padding:10px 18px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  position:sticky;
  top:0;
  z-index:20;
  gap:10px;
  flex-wrap:wrap;
}
.logo {
  display:flex;
  align-items:center;
  gap:9px;
  font-weight:800;
  font-size:1.35rem;
  color:var(--accent-dark);
  text-decoration:none;
}
.logo img {
  width:42px;
  height:42px;
  object-fit:contain;
}
.nav { display:none; }
.hamburger { background:none; border:none; font-size:1.6rem; cursor:pointer; padding:8px; color:var(--accent-dark); margin-left:auto; }
.menu-panel {
  display:none; position:fixed; top:0; right:0; height:100vh; width:50%; max-width:320px;
  background:#fff; border-left:1px solid var(--border); box-shadow:-4px 0 16px rgba(0,0,0,.12);
  z-index:40; padding:20px 0; overflow-y:auto;
}
.menu-panel.open { display:block; }
.menu-panel a {
  display:block; padding:14px 24px; text-decoration:none; color:var(--text); font-weight:600; border-bottom:1px solid #f1f5f9;
}
.menu-panel a:hover { background:#fff7ed; }
.menu-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.3); z-index:35; }
.menu-overlay.open { display:block; }
.fab {
  position:fixed; bottom:24px; right:24px; width:56px; height:56px; border-radius:50%;
  background:var(--accent); color:#fff; font-size:2rem; font-weight:700;
  display:flex; align-items:center; justify-content:center; text-decoration:none;
  box-shadow:0 4px 12px rgba(249,115,22,.4); z-index:25; border:none; cursor:pointer;
}
.fab:hover { background:var(--accent-dark); }
.add-choice { display:none; position:fixed; bottom:90px; right:24px; background:#fff; border-radius:12px; box-shadow:0 4px 16px rgba(0,0,0,.15); z-index:26; padding:8px; }
.add-choice.open { display:block; }
.add-choice a { display:block; padding:12px 16px; text-decoration:none; color:var(--text); font-weight:600; border-radius:8px; }
.add-choice a:hover { background:#fff7ed; }
.nav a, .btn {
  text-decoration:none;
  padding:8px 14px;
  border-radius:8px;
  font-weight:600;
  font-size:0.9rem;
  border:none;
  cursor:pointer;
  display:inline-block;
}
.btn-primary { background:var(--accent); color:#fff; }
.btn-outline {
  border:2px solid var(--accent);
  color:var(--accent-dark);
  background:transparent;
}
.btn-danger { background:#fee2e2; color:var(--danger); }
.btn-sm { padding:6px 10px; font-size:0.8rem; }
.container { max-width:900px; margin:24px auto; padding:0 16px; }
.card {
  background:var(--card);
  border-radius:12px;
  padding:18px;
  border:1px solid var(--border);
  margin-bottom:14px;
  box-shadow:0 1px 2px rgba(91,45,18,.05);
}
h1,h2,h3 { margin-top:0; }
input, select, textarea, button {
  padding:11px 12px;
  border:2px solid var(--border);
  border-radius:8px;
  font-size:1rem;
  width:100%;
  margin-bottom:10px;
  background:#fff;
  color:var(--text);
}
button {
  background:var(--accent);
  color:#fff;
  border:none;
  font-weight:700;
  cursor:pointer;
}
label {
  display:block;
  font-size:0.85rem;
  font-weight:600;
  margin-bottom:4px;
  color:var(--muted);
}
.req { color:var(--danger); }
.hint { font-size:0.85rem; color:var(--muted); margin-bottom:12px; }
.view-tabs { display:flex; gap:8px; margin-bottom:16px; }
.view-tabs a {
  flex:1;
  text-align:center;
  padding:10px;
  border-radius:8px;
  background:#f2e7dc;
  color:var(--text);
  text-decoration:none;
  font-weight:600;
}
.view-tabs a.active { background:var(--accent); color:#fff; }
.group-block {
  border:1px solid var(--border);
  border-radius:10px;
  margin-bottom:12px;
  overflow:hidden;
  background:#fff;
}
.group-header {
  display:flex;
  align-items:center;
  gap:10px;
  padding:12px 14px;
  cursor:pointer;
  background:#fff7ed;
  user-select:none;
}
.group-header:hover { background:#ffedd5; }
.color-dot {
  width:14px;
  height:14px;
  border-radius:50%;
  flex-shrink:0;
}
.group-body { display:none; padding:0 12px 12px; }
.group-body.open { display:block; }
.subgroup {
  margin:8px 0 8px 12px;
  border-left:3px solid var(--border);
  padding-left:10px;
}
.subgroup-header {
  font-weight:600;
  font-size:0.95rem;
  padding:8px 0;
  cursor:pointer;
  color:var(--muted);
}
.doc-row {
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:10px;
  padding:10px 0;
  border-bottom:1px solid var(--border);
}
.doc-row:last-child { border-bottom:none; }
.expiry-soon { color:var(--danger); font-weight:700; }
.expiry-ok { color:var(--success); }
.meta { font-size:0.85rem; color:var(--muted); }
.empty { text-align:center; color:var(--muted); padding:28px; }
.form-row { display:flex; gap:10px; flex-wrap:wrap; }
.form-row > * { flex:1; min-width:140px; }
.optional-box {
  background:#fffaf5;
  border:1px dashed var(--border);
  border-radius:8px;
  padding:12px;
  margin:12px 0;
}
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
.cal-head {
  text-align:center;
  font-size:0.75rem;
  font-weight:700;
  color:var(--muted);
  padding:6px 0;
}
.cal-day {
  min-height:72px;
  background:#fff;
  border:1px solid var(--border);
  border-radius:8px;
  padding:4px;
  overflow:hidden;
}
.cal-day.other { opacity:0.4; }
.cal-day.today {
  border-color:var(--accent);
  box-shadow:0 0 0 1px var(--accent);
}
.cal-num { font-size:0.75rem; font-weight:600; margin-bottom:2px; }
.cal-dots { display:flex; flex-wrap:wrap; gap:4px; align-items:center; }
.cal-dot {
  width:12px;
  height:12px;
  border-radius:50%;
  cursor:pointer;
  flex-shrink:0;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  font-size:12px;
  line-height:1;
}
.cal-icon {
  width:auto;
  min-width:16px;
  height:20px;
  border-radius:4px;
  background:transparent !important;
  font-size:16px;
  cursor:pointer;
  padding:0;
}
.modal-bg {
  display:none;
  position:fixed;
  inset:0;
  background:rgba(0,0,0,.4);
  z-index:50;
  align-items:center;
  justify-content:center;
  padding:16px;
}
.modal-bg.open { display:flex; }
.modal {
  background:#fff;
  border-radius:12px;
  padding:20px;
  max-width:420px;
  width:100%;
  max-height:80vh;
  overflow:auto;
}
.chip {
  display:inline-block;
  padding:2px 8px;
  border-radius:999px;
  font-size:0.75rem;
  background:#ffedd5;
  color:#9a3412;
  margin-left:6px;
}
.share-list { font-size:0.9rem; }
.share-list li { margin-bottom:6px; }

@media (max-width:520px) {
  .header { padding:8px 12px; }
  .logo { font-size:1.15rem; }
  .logo img { width:34px; height:34px; }
  .nav { gap:5px; }
  .nav a { padding:7px 9px; font-size:0.78rem; }
  .container { margin:16px auto; padding:0 10px; }
  .card { padding:14px; }
  .cal-day { min-height:58px; padding:3px; }
  .cal-dots { gap:2px; }
}
"""


def page(title: str, body: str, user: Optional[User] = None, show_fab: bool = False) -> str:
    if user:
        menu = f'''
        <button class="hamburger" onclick="document.getElementById('menuPanel').classList.toggle('open');document.getElementById('menuOverlay').classList.toggle('open')">☰</button>
        <div class="menu-overlay" id="menuOverlay" onclick="document.getElementById('menuPanel').classList.remove('open');this.classList.remove('open')"></div>
        <div class="menu-panel" id="menuPanel">
          <a href="/dashboard">Dashboard</a>
          <a href="/groups">Groups</a>
          <a href="/documents">My documents</a>
          <a href="/upgrade">Upgrade</a>
          <a href="/account">My account</a>
          <a href="/logout">Logout</a>
        </div>
        '''
        fab = '''
        <button class="fab" onclick="document.getElementById('addChoice').classList.toggle('open')">+</button>
        <div class="add-choice" id="addChoice">
          <a href="/add">Add manually</a>
          <a href="/add-from-document">Add from document</a>
        </div>
        ''' if show_fab else ''
    else:
        menu = '''
        <div class="nav">
          <a href="/login" class="btn btn-outline btn-sm">Login</a>
          <a href="/register" class="btn btn-primary btn-sm">Register</a>
        </div>
        '''
        fab = ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)} · Nutify</title>
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#f97316">
  <style>{BASE_CSS}</style>
</head>
<body>
  <header class="header">
    <a href="/" class="logo">
      <img src="/brand-logo" alt="Nutify logo" onerror="this.style.display='none'">
      <span>Nutify</span>
    </a>
    {menu}
  </header>
  <div class="container">{body}</div>
  {fab}
  <script>
  if ('serviceWorker' in navigator) {{
    navigator.serviceWorker.register('/sw.js').catch(() => {{}});
  }}

  function paintColourSelect(select) {{
    if (!select) return;
    const colour = select.value;
    if (colour && /^#[0-9a-fA-F]{{6}}$/.test(colour)) {{
      select.style.borderColor = colour;
      select.style.backgroundColor = colour + '18';
    }} else {{
      select.style.borderColor = '';
      select.style.backgroundColor = '';
    }}
  }}

  document.querySelectorAll('select[name="color"]').forEach(select => {{
    paintColourSelect(select);
    select.addEventListener('change', () => paintColourSelect(select));
  }});

  document.addEventListener('click', e => {{
    const panel = document.getElementById('menuPanel');
    if (panel && !panel.contains(e.target) && !e.target.closest('.hamburger')) {{
      panel.classList.remove('open');
    }}
  }});
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
      <h1>Make renewals less of a worry.</h1>
      <p class="hint">Keep track of passports, insurance, MOTs, subscriptions and more. Organise everything, share with family, and get reminders before things expire.</p>
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
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        return RedirectResponse("/register?error=exists", status_code=302)

    token = secrets.token_urlsafe(32)
    user = User(
        email=email,
        password_hash=hash_password(password),
        plan="free",
        email_verified=False,
        verification_token=token,
        verification_expires=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(user)
    db.commit()

    # Send verification email via Resend
    api_key = os.getenv("RESEND_API_KEY")
    from_email = os.getenv("FROM_EMAIL", "onboarding@resend.dev")
    base_url = os.getenv("BASE_URL", "https://expirely-5pkx.onrender.com")
    verify_url = f"{base_url}/verify?token={token}"

    if api_key:
        try:
            import requests
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "from": from_email,
                    "to": [email],
                    "subject": "Verify your Nutify account",
                    "html": f"<p>Click the link below to verify your account:</p><p><a href='{verify_url}'>Verify email</a></p><p>Or copy: {verify_url}</p>"
                },
                timeout=10,
            )
            if resp.status_code >= 400:
                print(f"Resend error: {resp.text}")
        except Exception as e:
            print(f"Failed to send verification email: {e}")
    else:
        print(f"VERIFICATION LINK: {verify_url}")

    return RedirectResponse("/verify-sent", status_code=302)


@app.get("/verify-sent", response_class=HTMLResponse)
def verify_sent():
    body = """
    <div class="card" style="text-align:center;">
      <h2>Check your email</h2>
      <p>We've sent a verification link. Click it to activate your account.</p>
      <p class="hint">If you don't see it, check spam. The link expires in 24 hours.</p>
    </div>
    """
    return page("Verify email", body)


@app.get("/verify")
def verify_email(token: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.verification_token == token).first()
    if not user or not user.verification_expires or user.verification_expires < datetime.utcnow():
        raise HTTPException(400, "Invalid or expired verification link")
    user.email_verified = True
    user.verification_token = None
    user.verification_expires = None
    db.commit()
    # Auto-login after verify
    session_token = create_session(db, user.id)
    resp = RedirectResponse("/dashboard", status_code=302)
    resp.set_cookie("session_token", session_token, httponly=True, max_age=30*24*3600, samesite="lax")
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
    resp.set_cookie(
        "session_token", token, httponly=True,
        max_age=30 * 24 * 3600, samesite="lax"
    )
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
    if days < 0:
        cls = "expiry-soon"
        expiry_text = f"Expired {d.expiry_date.strftime('%d %b %Y')} ({abs(days)}d ago)"
    elif days <= (d.reminder_days or 30):
        cls = "expiry-soon"
        expiry_text = f"Expires {d.expiry_date.strftime('%d %b %Y')} ({days}d)"
    else:
        cls = "expiry-ok"
        expiry_text = f"Expires {d.expiry_date.strftime('%d %b %Y')} ({days}d)"
    shared = "" if d.user_id == current_user_id else '<span class="chip">shared</span>'
    color = doc_color(d)
    symbol = ICON_MAP.get(d.icon or "dot", "●")
    icon_class = "cal-dot" if (d.icon or "dot") == "dot" else "cal-icon"

    return f"""
    <div class="doc-row">
      <div>
        <span class="{icon_class}" style="color:{color};background:{color};display:inline-flex;vertical-align:middle;margin-right:6px;{'color:white;' if icon_class == 'cal-dot' else ''}">{html.escape(symbol)}</span>
        <strong>{html.escape(d.name)}</strong> {shared}
        <div class="meta">{html.escape(d.doc_type)}</div>
        <span class="{cls}">{expiry_text}</span>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0;">
        <a href="/documents/{d.id}/edit" class="btn btn-outline btn-sm">Edit</a>
        <form method="post" action="/documents/{d.id}/delete" style="margin:0;width:auto;" onsubmit="return confirm('Delete?')">
          <button type="submit" class="btn btn-danger btn-sm" style="width:auto;">Delete</button>
        </form>
      </div>
    </div>
    """



def is_unlimited(user: User) -> bool:
    return user and user.plan == "unlimited"


@app.get("/documents", response_class=HTMLResponse)
def my_documents(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    files = db.query(DocumentFile).filter(DocumentFile.user_id == user.id).order_by(DocumentFile.created_at.desc()).all()
    rows = ""
    for f in files:
        rows += f'<div class="doc-row"><div><strong>{html.escape(f.filename)}</strong><div class="meta">{f.created_at.strftime("%d %b %Y")}</div></div><a href="/documents/{f.id}/download" class="btn btn-outline btn-sm">Download</a></div>'
    body = f"""
    <div class="card">
      <h2>My documents</h2>
      {rows or '<p class="hint">No documents uploaded yet.</p>'}
    </div>
    """
    return page("My documents", body, user, show_fab=True)


@app.get("/documents/{file_id}/download")
def download_document(file_id: str, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    df = db.query(DocumentFile).filter(DocumentFile.id == file_id, DocumentFile.user_id == user.id).first()
    if not df or not Path(df.file_path).exists():
        raise HTTPException(404)
    return FileResponse(df.file_path, filename=df.filename)

@app.get("/add-from-document", response_class=HTMLResponse)
def add_from_doc_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    body = """
    <div class="card" style="max-width:480px;margin:20px auto;">
      <h2>Add from document</h2>
      <p class="hint">Upload a policy or document. AI will try to extract the key details.</p>
      <form method="post" action="/add-from-document" enctype="multipart/form-data">
        <label>Document file</label>
        <input type="file" name="file" accept=".pdf,.jpg,.jpeg,.png,.webp" required>
        <button type="submit">Upload and scan</button>
      </form>
    </div>
    """
    return page("Add from document", body, user)

@app.post("/add-from-document")
async def add_from_doc(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Save file
    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = Path(file.filename).suffix or ".bin"
    path = upload_dir / f"{file_id}{ext}"
    content = await file.read()
    path.write_bytes(content)

    df = DocumentFile(id=file_id, user_id=user.id, filename=file.filename, content_type=file.content_type, file_path=str(path))
    db.add(df)
    db.commit()

    # Placeholder AI extraction — replace with real OpenAI/Anthropic call later
    # For now, redirect to manual add with a note
    return RedirectResponse(f"/add?prefill_from={file_id}", status_code=302)

@app.get("/upgrade", response_class=HTMLResponse)
def upgrade_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    body = """
    <div class="card">
      <h2>Upgrade to Unlimited</h2>
      <p>Free plan: 3 push reminders, max 2 groups, colours only (default calendar dot).</p>
      <h3>Unlimited includes</h3>
      <ul>
        <li>Unlimited push + email reminders</li>
        <li>Unlimited groups</li>
        <li>Custom calendar icons</li>
        <li>Google Calendar sync</li>
      </ul>
      <p><strong>Monthly</strong> £0.89 / month</p>
      <button onclick="alert('Google Play billing ready — connect later')">Subscribe monthly</button>
      <p style="margin-top:16px;"><strong>Yearly</strong> £4.99 / year</p>
      <button onclick="alert('Google Play billing ready — connect later')">Subscribe yearly</button>
      <p class="hint" style="margin-top:20px;">Buttons are placeholders for Google Play Billing integration.</p>
    </div>
    """
    return page("Upgrade", body, user)

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    verified = "Yes" if user.email_verified else "No"
    body = f"""
    <div class="card">
      <h2>My account</h2>
      <p><strong>Email:</strong> {html.escape(user.email)}</p>
      <p><strong>Plan:</strong> {user.plan.title()}</p>
      <p><strong>Email verified:</strong> {verified}</p>
      <p class="hint">To send verification emails you need an email provider (Resend, SendGrid, etc.). Set SMTP or API keys as environment variables and call it from the register endpoint.</p>
    </div>
    """
    return page("Account", body, user)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), view: str = "list", month: str = None):
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
                s_rows = "".join(
                    _doc_row_html(d, today, user.id)
                    for d in sorted(s_docs, key=lambda x: x.expiry_date)
                )
                body_parts.append(f"""
                <div class="subgroup">
                  <div class="subgroup-header" onclick="this.nextElementSibling.classList.toggle('open')">
                    ▸ {html.escape(s.name)} ({len(s_docs)})
                  </div>
                  <div class="group-body">{s_rows or '<div class="meta">No items</div>'}</div>
                </div>
                """)

            direct_rows = "".join(
                _doc_row_html(d, today, user.id)
                for d in sorted(items_direct, key=lambda x: x.expiry_date)
            )
            if direct_rows:
                body_parts.append(direct_rows)

            list_html += f"""
            <div class="group-block">
              <div class="group-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <span class="color-dot" style="background:{g.color}"></span>
                <strong style="flex:1;">{html.escape(g.name)}</strong>
                <span class="meta">{total} item{'s' if total != 1 else ''}</span>
                <span class="meta">▾</span>
              </div>
              <div class="group-body open">{''.join(body_parts) or '<div class="meta">Empty group</div>'}</div>
            </div>
            """

        if ungrouped:
            rows = "".join(
                _doc_row_html(d, today, user.id)
                for d in sorted(ungrouped, key=lambda x: x.expiry_date)
            )
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
        # Support ?month=YYYY-MM
        if month:
            try:
                y, m = map(int, month.split("-"))
                first = date(y, m, 1)
            except Exception:
                first = date(today.year, today.month, 1)
        else:
            first = date(today.year, today.month, 1)
        year, mon = first.year, first.month
        start_pad = first.weekday()

        if mon == 12:
            next_m = date(year + 1, 1, 1)
        else:
            next_m = date(year, mon + 1, 1)

        days_in_month = (next_m - first).days
        by_day = defaultdict(list)

        for d in docs:
            if d.expiry_date.year == year and d.expiry_date.month == mon:
                by_day[d.expiry_date.day].append(d)

        prev_first = first - timedelta(days=1)
        prev_month_str = prev_first.replace(day=1).strftime("%Y-%m")
        next_month_str = next_m.strftime("%Y-%m")
        nav = f"""
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <a href="/dashboard?view=calendar&month={prev_month_str}" class="btn btn-outline btn-sm">← Prev</a>
          <h3 style="margin:0;">{first.strftime("%B %Y")}</h3>
          <a href="/dashboard?view=calendar&month={next_month_str}" class="btn btn-outline btn-sm">Next →</a>
        </div>
        """

        cells = []
        for _ in range(start_pad):
            cells.append('<div class="cal-day other"></div>')

        for day in range(1, days_in_month + 1):
            is_today = (year == today.year and mon == today.month and day == today.day)
            dots = ""

            for d in by_day.get(day, []):
                colour = doc_color(d)
                symbol = ICON_MAP.get(d.icon or "dot", "●")
                icon_key = d.icon or "dot"
                tip = html.escape(f"{d.name} · {d.doc_type}", quote=True)
                extra = parse_extra(d)
                expired = d.expiry_date.date() < today
                opacity = "opacity:0.4;filter:grayscale(0.6);" if expired else ""

                payload = safe_json_for_html({
                    "id": d.id,
                    "name": d.name,
                    "type": d.doc_type,
                    "expiry": d.expiry_date.strftime("%d %b %Y"),
                    "notes": d.notes or "",
                    "extra": extra,
                    "color": colour,
                    "icon": icon_key,
                    "expired": expired,
                })

                if icon_key == "dot":
                    dots += (
                        f'<span class="cal-dot" '
                        f'style="background:{colour};{opacity}" '
                        f'title="{tip}" role="button" tabindex="0" '
                        f'onclick=\'showDoc({payload})\' '
                        f'onkeydown="if(event.key===\'Enter\')showDoc({payload})"></span>'
                    )
                else:
                    dots += (
                        f'<span class="cal-icon" '
                        f'style="color:{colour};{opacity}" '
                        f'title="{tip}" role="button" tabindex="0" '
                        f'onclick=\'showDoc({payload})\' '
                        f'onkeydown="if(event.key===\'Enter\')showDoc({payload})">'
                        f'{html.escape(symbol)}</span>'
                    )

            cells.append(
                f'<div class="cal-day{" today" if is_today else ""}">'
                f'<div class="cal-num">{day}</div>'
                f'<div class="cal-dots">{dots}</div></div>'
            )

        while len(cells) % 7:
            cells.append('<div class="cal-day other"></div>')

        heads = "".join(
            f'<div class="cal-head">{h}</div>'
            for h in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        )

        cal_html = f"""
        <div class="card">
          {nav}
          <div class="cal-grid">{heads}{''.join(cells)}</div>
          <p class="hint" style="margin-top:12px;">Tap a dot or symbol for item details. Faded = expired.</p>
        </div>
        <div class="modal-bg" id="docModal" onclick="if(event.target===this)this.classList.remove('open')">
          <div class="modal" id="docModalBody"></div>
        </div>
        <script>
        function showDoc(d) {{
          const modal = document.getElementById('docModalBody');
          modal.replaceChildren();

          const heading = document.createElement('h3');
          heading.style.marginTop = '0';
          heading.textContent = d.name;
          modal.appendChild(heading);

          const meta = document.createElement('div');
          meta.className = 'meta';
          meta.textContent = d.type + ' · ' + (d.expired ? 'expired ' : 'expires ') + d.expiry;
          modal.appendChild(meta);

          if (d.notes) {{
            const notes = document.createElement('p');
            notes.textContent = d.notes;
            modal.appendChild(notes);
          }}

          if (d.extra) {{
            for (const [k, v] of Object.entries(d.extra)) {{
              if (v === true || (v !== false && v !== null && v !== '')) {{
                const row = document.createElement('div');
                const strong = document.createElement('strong');
                strong.textContent = k.replaceAll('_', ' ') + ': ';
                row.appendChild(strong);
                row.appendChild(document.createTextNode(v === true ? 'Yes' : String(v)));
                modal.appendChild(row);
              }}
            }}
          }}

          const edit = document.createElement('a');
          edit.href = '/documents/' + encodeURIComponent(d.id) + '/edit';
          edit.className = 'btn btn-outline';
          edit.style.marginTop = '12px';
          edit.style.marginRight = '8px';
          edit.textContent = 'Edit item';
          modal.appendChild(edit);

          const close = document.createElement('button');
          close.textContent = 'Close';
          close.style.marginTop = '12px';
          close.onclick = () => document.getElementById('docModal').classList.remove('open');
          modal.appendChild(close);

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
    return page("Dashboard", body, user, show_fab=True)


@app.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    groups = (
        db.query(Group)
        .filter(Group.user_id == user.id, Group.parent_id == None)
        .order_by(Group.name)
        .all()
    )
    shared_in = (
        db.query(Group)
        .join(GroupShare, GroupShare.group_id == Group.id)
        .filter(GroupShare.shared_with_user_id == user.id, Group.parent_id == None)
        .all()
    )

    def group_card(g: Group, is_owner: bool) -> str:
        subs = (
            db.query(Group)
            .filter(Group.parent_id == g.id)
            .order_by(Group.name)
            .all()
        )
        sub_html = "".join(
            f'<div class="meta" style="margin:4px 0 4px 12px;">↳ {html.escape(s.name)}</div>'
            for s in subs
        )
        shares = db.query(GroupShare).filter(GroupShare.group_id == g.id).all()
        share_html = ""

        if shares and is_owner:
            share_html = "<ul class='share-list'>" + "".join(
                f"<li>{html.escape(s.shared_with.email if s.shared_with else s.shared_with_user_id)} "
                f"<form method='post' action='/groups/{g.id}/unshare' style='display:inline;'>"
                f"<input type='hidden' name='user_id' value='{s.shared_with_user_id}'>"
                f"<button type='submit' class='btn btn-danger btn-sm' style='width:auto;'>Remove</button></form></li>"
                for s in shares
            ) + "</ul>"

        if is_owner:
            color_opts = color_options(g.color)

            owner_actions = f"""
            <form method="post" action="/groups/{g.id}/update" class="form-row" style="margin-top:10px;">
              <input name="name" value="{html.escape(g.name, quote=True)}" required>
              <select name="color" aria-label="Group colour">{color_opts}</select>
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
            <strong>{html.escape(g.name)}</strong>
          </div>
          {sub_html}
          {owner_actions}
        </div>
        """

    owned_html = "".join(group_card(g, True) for g in groups) or '<div class="empty">No groups yet.</div>'
    shared_html = "".join(group_card(g, False) for g in shared_in)
    color_opts = color_options()

    body = f"""
    <h2>Groups</h2>
    <div class="card">
      <h3>Create group</h3>
      <form method="post" action="/groups" class="form-row">
        <input name="name" placeholder="e.g. Car, Household" required>
        <select name="color" aria-label="Group colour">{color_opts}</select>
        <button type="submit" style="width:auto;">Create</button>
      </form>
    </div>
    <h3>Your groups</h3>
    {owned_html}
    {"<h3>Shared with you</h3>" + shared_html if shared_html else ""}
    """
    return page("Groups", body, user, show_fab=True)


@app.post("/groups")
def create_group(
    name: str = Form(...),
    color: str = Form("#f97316"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    if not is_unlimited(user):
        owned = db.query(Group).filter(Group.user_id == user.id, Group.parent_id == None).count()
        if owned >= 2:
            raise HTTPException(400, "Free plan limited to 2 groups. Upgrade for unlimited.")
    if color not in GROUP_COLORS:
        color = "#f97316"

    db.add(Group(user_id=user.id, name=name.strip(), color=color))
    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/update")
def update_group(
    group_id: str,
    name: str = Form(...),
    color: str = Form("#f97316"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)

    g.name = name.strip()
    if color in GROUP_COLORS:
        g.color = color

    # Keep subgroup colours in sync with their parent group.
    for subgroup in db.query(Group).filter(Group.parent_id == g.id).all():
        subgroup.color = g.color

    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/subgroup")
def create_subgroup(
    group_id: str,
    name: str = Form(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    parent = db.query(Group).filter(
        Group.id == group_id,
        Group.user_id == user.id,
        Group.parent_id == None,
    ).first()

    if not parent:
        raise HTTPException(404)

    db.add(Group(
        user_id=user.id,
        parent_id=parent.id,
        name=name.strip(),
        color=parent.color,
    ))
    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/share")
def share_group(
    group_id: str,
    email: str = Form(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)

    other = db.query(User).filter(User.email == email.strip().lower()).first()
    if not other:
        raise HTTPException(400, detail="User not found. They need to register first.")
    if other.id == user.id:
        raise HTTPException(400, detail="Cannot share with yourself")

    existing = db.query(GroupShare).filter(
        GroupShare.group_id == g.id,
        GroupShare.shared_with_user_id == other.id,
    ).first()

    if not existing:
        db.add(GroupShare(group_id=g.id, shared_with_user_id=other.id))

        for sub in db.query(Group).filter(Group.parent_id == g.id).all():
            already_shared = db.query(GroupShare).filter(
                GroupShare.group_id == sub.id,
                GroupShare.shared_with_user_id == other.id,
            ).first()
            if not already_shared:
                db.add(GroupShare(group_id=sub.id, shared_with_user_id=other.id))

        db.commit()

    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/unshare")
def unshare_group(
    group_id: str,
    user_id: str = Form(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    g = db.query(Group).filter(Group.id == group_id, Group.user_id == user.id).first()
    if not g:
        raise HTTPException(404)

    db.query(GroupShare).filter(
        GroupShare.group_id == g.id,
        GroupShare.shared_with_user_id == user_id,
    ).delete()

    for sub in db.query(Group).filter(Group.parent_id == g.id).all():
        db.query(GroupShare).filter(
            GroupShare.group_id == sub.id,
            GroupShare.shared_with_user_id == user_id,
        ).delete()

    db.commit()
    return RedirectResponse("/groups", status_code=302)


@app.post("/groups/{group_id}/delete")
def delete_group(
    group_id: str,
    request: Request = None,
    db: Session = Depends(get_db),
):
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
        opts += (
            f'<option value="{g.id}" '
            f'{"selected" if selected == g.id else ""}>'
            f'{html.escape(g.name)}</option>'
        )

        for s in [x for x in groups if x.parent_id == g.id]:
            opts += (
                f'<option value="{s.id}" '
                f'{"selected" if selected == s.id else ""}>'
                f'↳ {html.escape(g.name)} / {html.escape(s.name)}</option>'
            )

    return opts


@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    type_fields_js = json.dumps({
        k: [{"key": a, "label": b, "type": c} for a, b, c in v]
        for k, v in TYPE_FIELDS.items()
    })
    colour_opts = color_options(include_default=True)
    icons = icon_options()

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
        <select name="color" aria-label="Item colour">{colour_opts}</select>

        <label>Calendar icon</label>
        <select name="icon" aria-label="Calendar icon">{icons}</select>
        <p class="hint">Choose a symbol to make this item easy to spot on your calendar. The default is a coloured dot.</p>

        <div id="extraFields"></div>

        <label>Notes</label>
        <textarea name="notes" rows="3" placeholder="Optional notes"></textarea>

        <button type="submit">Save item</button>
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
    icon = form.get("icon") or "dot"

    if not name or not expiry_date:
        raise HTTPException(400, "Name and expiry date are required")

    exp = datetime.strptime(expiry_date, "%Y-%m-%d")

    if group_id and group_id not in accessible_group_ids(user, db):
        group_id = None

    if color not in GROUP_COLORS:
        color = None
    if icon not in ICON_MAP or not is_unlimited(user):
        icon = "dot"

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
        icon=icon,
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
    type_fields_js = json.dumps({
        k: [{"key": a, "label": b, "type": c} for a, b, c in v]
        for k, v in TYPE_FIELDS.items()
    })

    # Ensure legacy items without an icon use the original dot.
    current_icon = doc.icon if doc.icon in ICON_MAP else "dot"
    colour_opts = color_options(selected=doc.color, include_default=True)
    icons = icon_options(current_icon)

    body = f"""
    <div class="card">
      <h2>Edit item</h2>
      <p class="hint"><span class="req">*</span> = mandatory.</p>

      <form method="post" action="/documents/{doc.id}/edit">
        <label>Name <span class="req">*</span></label>
        <input name="name" value="{html.escape(doc.name, quote=True)}" required>

        <label>Type <span class="req">*</span></label>
        <select name="doc_type" id="docType" required onchange="updateExtraFields()">
          {_type_options(doc.doc_type)}
        </select>

        <label>Expiry date <span class="req">*</span></label>
        <input name="expiry_date" type="date" value="{doc.expiry_date.strftime('%Y-%m-%d')}" required>

        <label>Remind me this many days before <span class="req">*</span></label>
        <input name="reminder_days" type="number" value="{doc.reminder_days or 30}" min="1" max="365" required>

        <label>Group / subgroup</label>
        <select name="group_id">{_group_options(user, db, doc.group_id)}</select>

        <label>Colour (only if not in a group)</label>
        <select name="color" aria-label="Item colour">{colour_opts}</select>

        <label>Calendar icon</label>
        <select name="icon" aria-label="Calendar icon">{icons}</select>
        <p class="hint">Your saved icon is selected automatically. The default is a coloured dot.</p>

        <div id="extraFields"></div>

        <label>Notes</label>
        <textarea name="notes" rows="3">{html.escape(doc.notes or "")}</textarea>

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
            const safeValue = String(v || '').replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
            html += `<label>${{f.label}}</label><input name="extra_${{f.key}}" value="${{safeValue}}">`;
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
    icon = form.get("icon") or "dot"

    if not name or not expiry_date:
        raise HTTPException(400, "Name and expiry date are required")

    exp = datetime.strptime(expiry_date, "%Y-%m-%d")

    if group_id and group_id not in accessible_group_ids(user, db):
        group_id = doc.group_id

    if color not in GROUP_COLORS:
        color = None
    if icon not in ICON_MAP or not is_unlimited(user):
        icon = "dot"

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
    doc.icon = icon
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
        PushSubscription.endpoint == data["endpoint"],
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
        return {
            "sent": False,
            "error": "No push subscription. Enable push on the dashboard first.",
        }

    send_push_to_user(
        user,
        "Test reminder",
        "This is a test push from Nutify",
        db,
    )
    return {"sent": True}


@app.get("/admin/check-reminders")
def check_reminders(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    today = date.today()
    docs = db.query(Document).all()
    sent = 0

    for doc in docs:
        # Respect snooze
        if doc.snooze_until and doc.snooze_until > now:
            continue

        days_left = (doc.expiry_date.date() - today).days
        if days_left > (doc.reminder_days or 30):
            continue

        # Only notify once per day
        if doc.last_notified_at and (now - doc.last_notified_at).total_seconds() < 20 * 3600:
            continue

        owner = db.query(User).filter(User.id == doc.user_id).first()
        if not owner:
            continue
        if not is_unlimited(owner):
            # Free plan: only remind on the first 3 documents
            owned_docs = db.query(Document).filter(Document.user_id == owner.id).order_by(Document.created_at).all()
            if doc.id not in [d.id for d in owned_docs[:3]]:
                continue

        title = f"{doc.name} expires soon" if days_left >= 0 else f"{doc.name} has expired"
        body = (
            f"{doc.name} ({doc.doc_type}) expires in {days_left} day(s)"
            if days_left >= 0
            else f"{doc.name} ({doc.doc_type}) expired {abs(days_left)} day(s) ago"
        )
        send_push_to_user(owner, title, body, db, extra={"doc_id": doc.id})
        doc.last_notified_at = now
        sent += 1

    db.commit()
    return {"checked": len(docs), "reminders_sent": sent}


@app.get("/documents/{doc_id}/snooze")
def snooze_document(doc_id: str, hours: int = 1, request: Request = None, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(401)
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or doc.user_id != user.id:
        raise HTTPException(404)
    if hours not in (1, 4, 24):
        hours = 1
    doc.snooze_until = datetime.utcnow() + timedelta(hours=hours)
    db.commit()
    return {"ok": True, "snooze_until": doc.snooze_until.isoformat()}


@app.get("/manifest.json")
def manifest():
    return {
        "name": "Nutify",
        "short_name": "Nutify",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#fffaf5",
        "theme_color": "#f97316",
        "icons": [],
    }


@app.get("/sw.js")
def service_worker():
    sw = """
self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {
    title: 'Nutify',
    body: 'You have an item expiring soon'
  };

  const options = {
    body: data.body,
    icon: '/brand-logo',
    data: data,
    actions: [
      { action: 'snooze1', title: 'Snooze 1h' },
      { action: 'snooze4', title: 'Snooze 4h' },
      { action: 'snooze24', title: 'Snooze 24h' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const data = event.notification.data || {};
  const docId = data.doc_id;

  if (event.action && event.action.startsWith('snooze') && docId) {
    const hours = event.action === 'snooze1' ? 1 : event.action === 'snooze4' ? 4 : 24;
    event.waitUntil(
      fetch('/documents/' + docId + '/snooze?hours=' + hours)
        .then(() => clients.openWindow('/dashboard'))
    );
  } else {
    event.waitUntil(clients.openWindow('/dashboard'));
  }
});
"""
    return Response(content=sw, media_type="application/javascript")


@app.get("/health")
def health():
    return {"status": "ok"}
