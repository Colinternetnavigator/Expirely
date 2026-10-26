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
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
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
    groups = relationship("Group", back_populates="owner", cascade="all, delete-orphan", foreign_ke
... 
