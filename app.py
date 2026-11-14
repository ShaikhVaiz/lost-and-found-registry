import os
import sys
import re
import random
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (
    Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import IntegrityError

# Load environment configuration
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join("/tmp", "uploads") if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}

app = Flask(__name__)

class VercelPathMiddleware:
    """WSGI middleware ensuring proper URL routing when deployed under Vercel Serverless rewrites."""
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        query = environ.get("QUERY_STRING", "")
        if "path=" in query:
            params = urllib.parse.parse_qs(query)
            p = params.get("path", [None])[0]
            if p and p not in ("/api/index", "/api/index.py"):
                while p.startswith("//"):
                    p = p[1:]
                if not p.startswith("/"):
                    p = "/" + p
                environ["PATH_INFO"] = p
        elif environ.get("HTTP_X_MATCHED_PATH"):
            environ["PATH_INFO"] = environ.get("HTTP_X_MATCHED_PATH")
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathMiddleware(app.wsgi_app)

# Secure SECRET_KEY configuration (Strictly required from environment)
secret_key_env = os.environ.get("SECRET_KEY")
if not secret_key_env or not secret_key_env.strip():
    raise RuntimeError(
        "SECRET_KEY environment variable is not configured. "
        "A secure SECRET_KEY is required to start the application."
    )
app.secret_key = secret_key_env.strip()
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["WTF_CSRF_TIME_LIMIT"] = 3600

# Enable Global CSRF Protection across all state-changing requests
csrf = CSRFProtect(app)

# Resend API Key for Email OTP (https://resend.com)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()

# Administrator Credentials (Strictly required from secure environment)
admin_id_env = os.environ.get("ADMIN_ID")
admin_pass_env = os.environ.get("ADMIN_PASSWORD")
if not admin_id_env or not admin_id_env.strip() or not admin_pass_env or not admin_pass_env.strip():
    raise RuntimeError(
        "ADMIN_ID and ADMIN_PASSWORD environment variables are not configured. "
        "Secure administrator credentials are required to start the application."
    )
ADMIN_ID = admin_id_env.strip()
ADMIN_PASSWORD = admin_pass_env.strip()

# Database configuration: TiDB Cloud (MySQL compatible) with local SQLite development support
tidb_host = os.environ.get("TIDB_HOST")
tidb_user = os.environ.get("TIDB_USER")
tidb_pass = os.environ.get("TIDB_PASSWORD")
tidb_db   = os.environ.get("TIDB_DATABASE", "lost_found")
tidb_port = os.environ.get("TIDB_PORT", "4000")

raw_db_url = os.environ.get("TIDB_URL") or os.environ.get("DATABASE_URL")

is_production_env = bool(
    os.environ.get("VERCEL") or
    os.environ.get("FLASK_ENV") == "production" or
    os.environ.get("ENVIRONMENT") == "production" or
    os.environ.get("NODE_ENV") == "production"
)

is_remote_db_configured = bool(
    (tidb_host and tidb_user) or
    (raw_db_url and any(proto in raw_db_url.lower() for proto in ["mysql", "tidb", "postgres"]))
)

# In production (such as Vercel), temporary SQLite storage is strictly prohibited
if is_production_env and not is_remote_db_configured:
    raise RuntimeError(
        "Production environment detected (e.g. Vercel) but no persistent remote database (TiDB Cloud / MySQL) "
        "is configured. Running on temporary or ephemeral SQLite storage in production is strictly prohibited."
    )

if tidb_host and tidb_user:
    db_url = f"mysql+pymysql://{tidb_user}:{tidb_pass}@{tidb_host}:{tidb_port}/{tidb_db}"
elif raw_db_url and ("mysql" in raw_db_url or "tidb" in raw_db_url):
    db_url = raw_db_url
    if db_url.startswith("mysql://"):
        db_url = db_url.replace("mysql://", "mysql+pymysql://", 1)
elif raw_db_url and raw_db_url.startswith("postgres://"):
    db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
elif raw_db_url:
    db_url = raw_db_url
else:
    # Local development default SQLite database
    sqlite_file = os.path.join(BASE_DIR, "lost_found.db")
    db_url = f"sqlite:///{sqlite_file}"

engine_options = {}
if "mysql" in db_url:
    engine_options = {
        "connect_args": {
            "ssl": {
                "ssl_mode": "REQUIRED"
            }
        },
        "pool_recycle": 280,
        "pool_pre_ping": True
    }

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_options
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except Exception:
    pass
db = SQLAlchemy(app)

# SQLite Foreign-Key Constraint Enforcement (PRAGMA foreign_keys=ON)
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    import sqlite3
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

# Standard Categories Taxonomy
CATEGORIES = [
    "Electronics & Gadgets",
    "Wallets & Purses",
    "Keys & Keychains",
    "Documents & IDs",
    "Bags & Backpacks",
    "Clothing & Accessories",
    "Jewelry & Watches",
    "Books & Stationery",
    "Personal Accessories",
    "Other Belongings"
]

STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "with", "of",
    "by", "is", "it", "this", "that", "my", "was", "near", "front", "found", "lost"
}

# ==============================================================================
# Database Models
# ==============================================================================

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user", nullable=False) # 'user' or 'admin'
    phone = db.Column(db.String(30), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    reports = db.relationship("Report", backref="owner", lazy=True, cascade="all, delete-orphan")

class Report(db.Model):
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    report_type = db.Column(db.String(10), nullable=False, index=True) # 'lost' or 'found'
    title = db.Column(db.String(150), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    incident_date = db.Column(db.String(20), nullable=False) # YYYY-MM-DD
    incident_time = db.Column(db.String(30), nullable=True) # approximate time or HH:MM
    location = db.Column(db.String(150), nullable=False, index=True)
    image_filename = db.Column(db.Text, nullable=True)
    contact_name = db.Column(db.String(100), nullable=False)
    contact_info = db.Column(db.String(120), nullable=False)
    identifying_details = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="Active", nullable=False, index=True) # 'Active', 'Matched', 'Resolved'
    admin_resolution_notes = db.Column(db.Text, nullable=True) # Audited record for exceptional resolution
    created_at = db.Column(db.DateTime, default=datetime.now)

class PasswordReset(db.Model):
    __tablename__ = "password_resets"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    otp = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

class Claim(db.Model):
    __tablename__ = "claims"
    id = db.Column(db.Integer, primary_key=True)
    lost_report_id = db.Column(db.Integer, db.ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True)
    found_report_id = db.Column(db.Integer, db.ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True)
    claimant_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    # Private claimant verification answers (never disclosed publicly or to finder)
    claim_details = db.Column(db.Text, nullable=False)
    # Status lifecycle: 'Pending', 'Approved', 'Rejected'
    status = db.Column(db.String(20), default="Pending", nullable=False, index=True)
    # Database-level concurrency safeguard: ensures at most ONE approved claim per found report
    approved_found_report_id = db.Column(db.Integer, db.ForeignKey("reports.id", ondelete="RESTRICT"), nullable=True, unique=True, index=True)
    # Audit trail fields
    admin_notes = db.Column(db.Text, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    # Relationships without deletion cascades (Custody Retention Strategy)
    lost_report = db.relationship("Report", foreign_keys=[lost_report_id])
    found_report = db.relationship("Report", foreign_keys=[found_report_id])
    claimant = db.relationship("User", foreign_keys=[claimant_id])
    reviewer = db.relationship("User", foreign_keys=[reviewed_by])

    __table_args__ = (
        db.UniqueConstraint("lost_report_id", "found_report_id", name="uq_lost_found_claim"),
    )

class ClaimAudit(db.Model):
    __tablename__ = "claim_audits"
    id = db.Column(db.Integer, primary_key=True)
    claim_id = db.Column(db.Integer, db.ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False, index=True) # SUBMITTED, APPROVED, REJECTED, REOPENED, AUTO_REJECTED_COMPETING, EXCEPTIONAL_RESOLVE_CLOSED
    reason = db.Column(db.Text, nullable=True) # Administrative justification / review notes
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    claim = db.relationship("Claim", foreign_keys=[claim_id], backref=db.backref("audit_logs", lazy=True, order_by="ClaimAudit.id.asc()"))
    actor = db.relationship("User", foreign_keys=[actor_id])

def record_claim_audit(claim_id, action, actor_id=None, reason=None):
    """
    Appends an audit record to the claim audit history.
    Preserves all previous review history without overwriting. Never stores confidential ownership proof.
    """
    audit = ClaimAudit(
        claim_id=claim_id,
        actor_id=actor_id,
        action=action,
        reason=reason,
        created_at=datetime.now()
    )
    db.session.add(audit)
    return audit

# ==============================================================================
# Email OTP Dispatcher (Resend HTTPS API & Console Fallback)
# ==============================================================================

def send_email_otp(to_email, otp_code):
    """
    Sends real 6-digit OTP verification email via Resend API (DKIM/SPF authenticated).
    Falls back gracefully to console output if RESEND_API_KEY is not configured yet.
    """
    subject = "Verification Code: Lost & Found Registry Password Reset"
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f4eee3; padding: 24px; color: #1f1612;">
        <div style="max-width: 480px; margin: auto; background: #ffffff; border-radius: 12px; padding: 28px; border: 2px solid #c7b6a1; box-shadow: 0 4px 12px rgba(46,29,21,0.08);">
            <div style="text-align: center; margin-bottom: 20px;">
                <h2 style="color: #2e1d15; margin: 0; font-size: 22px;">Lost &amp; Found Registry</h2>
                <p style="color: #8c5d42; font-size: 13px; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px;">Official Public Security Portal</p>
            </div>
            <hr style="border: none; border-top: 1px dashed #ddcfbd; margin: 18px 0;">
            <p style="font-size: 15px; color: #1f1612;">Hello,</p>
            <p style="font-size: 14px; color: #42352f; line-height: 1.5;">
                We received a request to reset the password for your citizen account associated with <strong>{to_email}</strong>.
            </p>
            <p style="font-size: 14px; color: #42352f;">Your 6-digit One-Time Password (OTP) verification code is:</p>
            <div style="background: #fcfaf6; border: 2px dashed #8c5d42; border-radius: 8px; padding: 18px; text-align: center; margin: 22px 0;">
                <span style="font-size: 34px; font-weight: 800; letter-spacing: 8px; color: #5c3b2b; font-family: monospace;">{otp_code}</span>
            </div>
            <p style="color: #6b584d; font-size: 12px; line-height: 1.5;">
                ⏱️ This verification code is valid for <strong>10 minutes</strong>. If you did not request a password reset, you can safely ignore this email.
            </p>
        </div>
    </body>
    </html>
    """

    print(f"\n[EMAIL OTP DISPATCH] =======================================")
    print(f"Recipient: {to_email}")
    print(f"OTP Code: {otp_code} (Valid for 10 minutes)")
    print(f"Resend API Active: {'YES' if RESEND_API_KEY else 'NO (Set RESEND_API_KEY in .env)'}")
    print(f"===========================================================\n")

    if not RESEND_API_KEY:
        return False, f"Verification Code: {otp_code} (Console fallback - add RESEND_API_KEY in Vercel Environment Variables)"

    # 1. Try Resend Python SDK
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        params = {
            "from": "onboarding@resend.dev",
            "to": [to_email],
            "subject": subject,
            "html": html_content
        }
        resp = resend.Emails.send(params)
        print(f">> Resend SDK email dispatched: {resp}")
        return True, "A 6-digit verification code has been dispatched to your email inbox!"
    except Exception as e_sdk:
        print(f">> Resend SDK notice ({e_sdk}), trying direct REST API...")

    # 2. Try Resend REST API via urllib
    try:
        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "LostAndFoundHub/1.0"
        }
        payload = {
            "from": "onboarding@resend.dev",
            "to": [to_email],
            "subject": subject,
            "html": html_content
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                print(">> Resend REST API email sent successfully!")
                return True, "A 6-digit verification code has been dispatched to your email inbox!"
    except Exception as e_rest:
        print(f">> Resend REST API notice: {e_rest}")

    return False, f"Verification Code: {otp_code} (Resend test domain only sends to registered email shaikhv750@gmail.com)"

# ==============================================================================
# Helpers & Heuristic Matching Engine
# ==============================================================================

def sanitize_error_msg(error_obj):
    """
    Sanitizes database and runtime error messages so passwords, credentials,
    and confidential connection strings are never exposed in application logs or outputs.
    """
    if not error_obj:
        return ""
    msg = str(error_obj)
    # Mask database connection strings or URI credentials (e.g. mysql://user:pass@host/db)
    msg = re.sub(r'://([^:/\s]+):([^/\s]+)@', r'://\1:****@', msg)
    # Mask explicit password and secret parameter assignments
    msg = re.sub(r'(password[:=\s]+)([^\s&,;\'"]+)', r'\1****', msg, flags=re.IGNORECASE)
    msg = re.sub(r'(secret[:=\s]+)([^\s&,;\'"]+)', r'\1****', msg, flags=re.IGNORECASE)
    return msg

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def tokenize(text):
    if not text:
        return set()
    words = re.findall(r"\b[a-zA-Z0-9]{3,}\b", text.lower())
    return {w for w in words if w not in STOP_WORDS}

def calculate_match_score(item_a, item_b):
    """
    Transparent Multi-Factor Matching Algorithm:
    1. Category Match: 35 points
    2. Name & Description Keyword Overlap: Up to 35 points
    3. Location Semantic Similarity: Up to 20 points
    4. Date Proximity: Up to 10 points
    """
    score = 0
    reasons = []

    # 1. Category
    if item_a.category.strip().lower() == item_b.category.strip().lower():
        score += 35
        reasons.append(f"Category matches: {item_a.category}")

    # 2. Text Similarity (Title, Description, Details)
    text_a = f"{item_a.title} {item_a.description} {item_a.identifying_details or ''}"
    text_b = f"{item_b.title} {item_b.description} {item_b.identifying_details or ''}"
    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)
    if tokens_a and tokens_b:
        common = tokens_a.intersection(tokens_b)
        if common:
            overlap = len(common) / min(len(tokens_a), len(tokens_b))
            score += min(35, round(overlap * 35))
            reasons.append(f"Keywords match: {', '.join(list(common)[:3])}")

    # 3. Location Similarity
    loc_a = tokenize(item_a.location)
    loc_b = tokenize(item_b.location)
    if loc_a and loc_b:
        loc_common = loc_a.intersection(loc_b)
        if loc_common:
            loc_ratio = len(loc_common) / min(len(loc_a), len(loc_b))
            score += min(20, round(loc_ratio * 20))
            reasons.append(f"Location proximity: {', '.join(loc_common)}")

    # 4. Date Proximity
    try:
        d1 = datetime.strptime(item_a.incident_date[:10], "%Y-%m-%d")
        d2 = datetime.strptime(item_b.incident_date[:10], "%Y-%m-%d")
        diff = abs((d1 - d2).days)
        if diff <= 1:
            score += 10
            reasons.append("Date proximity: Same or next day")
        elif diff <= 3:
            score += 7
            reasons.append("Date proximity: Within 3 days")
        elif diff <= 7:
            score += 4
            reasons.append("Date proximity: Within 1 week")
    except Exception:
        pass

    return min(score, 100), reasons

def get_possible_matches_for_item(item, min_score=25):
    opp_type = "found" if item.report_type == "lost" else "lost"
    candidates = Report.query.filter(
        Report.report_type == opp_type,
        Report.id != item.id,
        Report.status != "Resolved"
    ).order_by(Report.id.desc()).all()

    matches = []
    for cand in candidates:
        score, reasons = calculate_match_score(item, cand)
        if score >= min_score:
            matches.append({"item": cand, "score": score, "reasons": reasons})

    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches

def get_all_possible_matches(min_score=30):
    lost_items = Report.query.filter_by(report_type="lost", status="Active").all()
    found_items = Report.query.filter_by(report_type="found", status="Active").all()

    pairs = []
    for lost in lost_items:
        for found in found_items:
            score, reasons = calculate_match_score(lost, found)
            if score >= min_score:
                pairs.append({
                    "lost": lost,
                    "found": found,
                    "score": score,
                    "reasons": reasons
                })
    pairs.sort(key=lambda x: x["score"], reverse=True)
    return pairs

# ==============================================================================
# Authentication & Authorization Decorators
# ==============================================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to access this page.", "info")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session or session.get("user_role") != "admin":
            flash("Administrator clearance required.", "danger")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.context_processor
def inject_global_user():
    return {
        "current_user": {
            "is_authenticated": "user_id" in session,
            "id": session.get("user_id"),
            "name": session.get("user_name"),
            "email": session.get("user_email"),
            "role": session.get("user_role")
        }
    }

# ==============================================================================
# Database Initialization & Seed Data
# ==============================================================================

def seed_initial_data():
    admin_email = ADMIN_ID.lower() if "@" in ADMIN_ID else f"{ADMIN_ID.lower()}@lostfound.org"
    admin_user = User.query.filter((User.role == "admin") | (User.email == admin_email)).first()
    if not admin_user:
        admin_user = User(
            name="System Administrator",
            email=admin_email,
            password_hash=generate_password_hash(ADMIN_PASSWORD),
            role="admin",
            phone="+91 90000 00001"
        )
        db.session.add(admin_user)
        db.session.commit()
    else:
        # Keep admin credentials in sync with secure environment
        admin_user.password_hash = generate_password_hash(ADMIN_PASSWORD)
        admin_user.email = admin_email
        db.session.commit()

# ==============================================================================
# Web Routes
# ==============================================================================

_db_initialized = False

def run_schema_migrations():
    """
    Safe, idempotent schema migration utility compatible with SQLite and TiDB Cloud / MySQL.
    Inspects existing schema before altering. Adds missing columns and tables without deleting,
    resetting, or mutating existing records.
    """
    from sqlalchemy import inspect
    try:
        # Create any entirely new tables
        db.create_all()

        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        # 1. Check 'reports' table columns
        if "reports" in existing_tables:
            report_cols = [c["name"] for c in inspector.get_columns("reports")]
            if "admin_resolution_notes" not in report_cols:
                with db.engine.connect() as conn:
                    conn.execute(db.text("ALTER TABLE reports ADD COLUMN admin_resolution_notes TEXT;"))
                    conn.commit()

        # 2. Check 'claims' table columns & concurrency safeguard index
        if "claims" in existing_tables:
            claim_cols = [c["name"] for c in inspector.get_columns("claims")]
            expected_claim_cols = {
                "admin_notes": "TEXT",
                "reviewed_by": "INTEGER",
                "reviewed_at": "DATETIME",
                "approved_found_report_id": "INTEGER"
            }
            for col_name, col_type in expected_claim_cols.items():
                if col_name not in claim_cols:
                    with db.engine.connect() as conn:
                        conn.execute(db.text(f"ALTER TABLE claims ADD COLUMN {col_name} {col_type};"))
                        conn.commit()

            # Pre-migration validation and atomic backfill inside a transaction
            with db.engine.connect() as conn:
                try:
                    # 1. Detect whether multiple approved claims reference the same found_report_id
                    dup_query = db.text("""
                        SELECT found_report_id, COUNT(*) as cnt
                        FROM claims
                        WHERE status = 'Approved'
                        GROUP BY found_report_id
                        HAVING COUNT(*) > 1;
                    """)
                    duplicate_rows = conn.execute(dup_query).fetchall()
                    if duplicate_rows:
                        conflicting_ids = [row[0] for row in duplicate_rows]
                        raise RuntimeError(
                            f"Database schema migration halted: Multiple approved claims detected referencing the same found report ID(s): {conflicting_ids}. "
                            "The unique index 'uq_claims_approved_found' cannot be created without administrative reconciliation. "
                            "No records have been modified or deleted."
                        )

                    # 2. Validate non-NULL approved_found_report_id values do not conflict across multiple rows
                    dup_approved_col = conn.execute(db.text("""
                        SELECT approved_found_report_id, COUNT(*) as cnt
                        FROM claims
                        WHERE approved_found_report_id IS NOT NULL
                        GROUP BY approved_found_report_id
                        HAVING COUNT(*) > 1;
                    """)).fetchall()
                    if dup_approved_col:
                        conflicting_approved = [row[0] for row in dup_approved_col]
                        raise RuntimeError(
                            f"Database schema migration halted: Conflicting approved_found_report_id values detected: {conflicting_approved}. "
                            "The unique index 'uq_claims_approved_found' cannot be created."
                        )

                    # 3. For every claim with status = 'Approved':
                    # approved_found_report_id must equal found_report_id if non-NULL
                    mismatched_approved = conn.execute(db.text("""
                        SELECT id, found_report_id, approved_found_report_id
                        FROM claims
                        WHERE status = 'Approved'
                          AND approved_found_report_id IS NOT NULL
                          AND approved_found_report_id != found_report_id;
                    """)).fetchall()
                    if mismatched_approved:
                        details = [
                            f"Claim #{r[0]} (found_report_id={r[1]}, approved_found_report_id={r[2]})"
                            for r in mismatched_approved
                        ]
                        raise RuntimeError(
                            f"Database schema migration halted: Approved claim(s) have invalid non-NULL approved_found_report_id differing from found_report_id: {', '.join(details)}. "
                            "Inconsistent non-NULL values must not be automatically overwritten. "
                            "Please perform manual administrative reconciliation before applying migration."
                        )

                    # 4. For claims whose status is 'Pending' or 'Rejected':
                    # approved_found_report_id must be NULL.
                    invalid_unapproved = conn.execute(db.text("""
                        SELECT id, status, approved_found_report_id
                        FROM claims
                        WHERE status IN ('Pending', 'Rejected')
                          AND approved_found_report_id IS NOT NULL;
                    """)).fetchall()
                    if invalid_unapproved:
                        details = [
                            f"Claim #{r[0]} (status='{r[1]}', approved_found_report_id={r[2]})"
                            for r in invalid_unapproved
                        ]
                        raise RuntimeError(
                            f"Database schema migration halted: Non-approved claim(s) have non-NULL approved_found_report_id: {', '.join(details)}. "
                            "Claims with status 'Pending' or 'Rejected' must have NULL approved_found_report_id. "
                            "Inconsistent non-NULL values must not be automatically overwritten. "
                            "Please perform manual administrative reconciliation before applying migration."
                        )

                    # 5. Execute backfill for approved claims where approved_found_report_id IS NULL
                    conn.execute(db.text("""
                        UPDATE claims
                        SET approved_found_report_id = found_report_id
                        WHERE status = 'Approved' AND approved_found_report_id IS NULL;
                    """))

                    # Verify that every Approved claim has approved_found_report_id = found_report_id
                    unverified_approved = conn.execute(db.text("""
                        SELECT COUNT(*)
                        FROM claims
                        WHERE status = 'Approved'
                          AND (approved_found_report_id IS NULL OR approved_found_report_id != found_report_id);
                    """)).scalar() or 0
                    if unverified_approved > 0:
                        raise RuntimeError(
                            f"Database schema migration verification failed: {unverified_approved} approved claim(s) "
                            "do not have approved_found_report_id equal to found_report_id after backfill."
                        )

                    # Verify that Pending and Rejected claims have NULL approved_found_report_id
                    unverified_unapproved = conn.execute(db.text("""
                        SELECT COUNT(*)
                        FROM claims
                        WHERE status IN ('Pending', 'Rejected')
                          AND approved_found_report_id IS NOT NULL;
                    """)).scalar() or 0
                    if unverified_unapproved > 0:
                        raise RuntimeError(
                            f"Database schema migration verification failed: {unverified_unapproved} non-approved claim(s) "
                            "have non-NULL approved_found_report_id."
                        )

                    # Commit only after all verification succeeds
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

                try:
                    db.session.expire_all()
                except Exception:
                    pass

            # Create the unique index uq_claims_approved_found only after successful verification
            fresh_indexes = [idx["name"] for idx in inspect(db.engine).get_indexes("claims")]
            if "uq_claims_approved_found" not in fresh_indexes:
                with db.engine.connect() as conn:
                    conn.execute(db.text("CREATE UNIQUE INDEX uq_claims_approved_found ON claims (approved_found_report_id);"))
                    conn.commit()

        # 3. Check 'claim_audits' table
        if "claim_audits" not in existing_tables:
            ClaimAudit.__table__.create(db.engine, checkfirst=True)

        # 4. Check 'password_resets' schema safely without dropping tables
        if "password_resets" in existing_tables:
            pr_cols = [c["name"] for c in inspector.get_columns("password_resets")]
            if "id" not in pr_cols:
                raise RuntimeError(
                    "Database schema migration halted: 'password_resets' table exists but is missing primary key 'id'. "
                    "Automatic table destruction is disabled to prevent data loss. "
                    "Please perform a non-destructive manual schema migration."
                )

            with db.engine.connect() as conn:
                row_count = conn.execute(db.text("SELECT COUNT(*) FROM password_resets;")).scalar() or 0

            # Safe column definitions with defaults for existing rows to satisfy model nullability & default constraints
            expected_pr_cols = {
                "used": "BOOLEAN DEFAULT 0",
                "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
                "expires_at": "DATETIME",
                "otp": "VARCHAR(6)",
                "email": "VARCHAR(120)"
            }

            # If critical identifying columns (email, otp, expires_at) are missing and rows already exist:
            critical_cols = ["email", "otp", "expires_at"]
            missing_critical = [col for col in critical_cols if col not in pr_cols]
            if missing_critical and row_count > 0:
                raise RuntimeError(
                    f"Database schema migration halted: 'password_resets' has {row_count} existing record(s) "
                    f"but is missing critical column(s) {missing_critical}. Automatic migration cannot "
                    "safely generate values for existing records without risk of data corruption. "
                    "Please perform a non-destructive manual schema migration."
                )

            for col_name, col_def in expected_pr_cols.items():
                if col_name not in pr_cols:
                    with db.engine.connect() as conn:
                        conn.execute(db.text(f"ALTER TABLE password_resets ADD COLUMN {col_name} {col_def};"))
                        conn.commit()

        print(">> Database schema verified and up to date.")
    except Exception as e:
        safe_msg = sanitize_error_msg(e)
        print(f">> Schema migration error: {safe_msg}")
        raise RuntimeError(f"Database schema migration failed: {safe_msg}") from e

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        try:
            run_schema_migrations()
            seed_initial_data()
            _db_initialized = True
        except Exception as e:
            db.session.rollback()
            safe_msg = sanitize_error_msg(e)
            print(f">> Database initialization failed: {safe_msg}")
            abort(500, description="Database initialization failed. Please contact administrator.")

@app.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/")
def index():
    """Official Entry Point: redirects to Login if unauthenticated, else shows platform"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    stats = {
        "total": Report.query.count(),
        "lost": Report.query.filter_by(report_type="lost", status="Active").count(),
        "found": Report.query.filter_by(report_type="found", status="Active").count(),
        "resolved": Report.query.filter_by(status="Resolved").count()
    }
    recent_reports = Report.query.filter(Report.status != "Resolved").order_by(Report.id.desc()).limit(6).all()
    return render_template("index.html", stats=stats, recent_reports=recent_reports)

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        login_id = request.form.get("login_id", request.form.get("email", "")).strip().lower()
        password = request.form.get("password", "")

        if not login_id or not password:
            flash("Please enter both login identifier and password.", "warning")
            return render_template("login.html", login_id=login_id)

        admin_email = ADMIN_ID.lower() if "@" in ADMIN_ID else f"{ADMIN_ID.lower()}@lostfound.org"

        # 1. Administrator Authentication (via configured ADMIN_ID & ADMIN_PASSWORD)
        if (login_id == ADMIN_ID.lower() or login_id == admin_email) and password == ADMIN_PASSWORD:
            admin_user = User.query.filter((User.role == "admin") | (User.email == admin_email)).first()
            if not admin_user:
                admin_user = User(
                    name="System Administrator",
                    email=admin_email,
                    password_hash=generate_password_hash(ADMIN_PASSWORD),
                    role="admin"
                )
                db.session.add(admin_user)
                db.session.commit()

            session["user_id"] = admin_user.id
            session["user_name"] = admin_user.name
            session["user_email"] = admin_user.email
            session["user_role"] = "admin"

            flash("Welcome, Administrator!", "success")
            return redirect(url_for("admin_dashboard"))

        # 2. Citizen Authentication (via registered email and password hash)
        user = User.query.filter(User.email.ilike(login_id)).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid credentials. Please verify your email / Admin ID and password.", "danger")
            return render_template("login.html", login_id=login_id)

        session["user_id"] = user.id
        session["user_name"] = user.name
        session["user_email"] = user.email
        session["user_role"] = user.role

        flash(f"Welcome back, {user.name}!", "success")
        if user.role == "admin":
            return redirect(url_for("admin_dashboard"))

        next_page = request.args.get("next")
        if next_page and next_page.startswith("/"):
            return redirect(next_page)
        return redirect(url_for("index"))

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone = request.form.get("phone", "").strip()

        # Security Enforcement: Public registration strictly creates standard user accounts.
        # User-submitted role values are never accepted.
        role = "user"

        if not (name and email and password):
            flash("Please complete all required fields (*).", "warning")
            return render_template("register.html", form_data=request.form)

        if User.query.filter(User.email.ilike(email)).first():
            flash("An account with this email address already exists. Please log in.", "warning")
            return redirect(url_for("login"))

        new_user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            phone=phone
        )
        db.session.add(new_user)
        db.session.commit()

        session["user_id"] = new_user.id
        session["user_name"] = new_user.name
        session["user_email"] = new_user.email
        session["user_role"] = new_user.role

        flash(f"Account successfully created as {role.upper()}! Welcome to the Registry.", "success")
        if role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))

    return render_template("register.html", form_data={})

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your registered email address.", "warning")
            return render_template("forgot_password.html")

        user = User.query.filter(User.email.ilike(email)).first()
        if not user:
            flash("If that email is registered, a verification code has been dispatched.", "info")
            session["reset_email"] = email
            return redirect(url_for("verify_otp"))

        otp_code = str(random.randint(100000, 999999))
        expires_at = datetime.now() + timedelta(minutes=10)

        try:
            PasswordReset.query.filter_by(email=email, used=False).update({"used": True})
            pr = PasswordReset(email=email, otp=otp_code, expires_at=expires_at, used=False)
            db.session.add(pr)
            db.session.commit()
        except Exception as e_pr:
            db.session.rollback()
            print(f">> PasswordReset DB notice: {e_pr}")

        sent, msg = send_email_otp(email, otp_code)
        session["reset_email"] = email
        flash(msg, "success" if sent else "info")
        return redirect(url_for("verify_otp"))

    return render_template("forgot_password.html")

@app.route("/verify-otp", methods=["GET", "POST"])
@app.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():
    if "user_id" in session:
        return redirect(url_for("index"))

    email = session.get("reset_email")
    if not email:
        flash("Please submit your registered email to request a reset code.", "info")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        otp_entered = request.form.get("otp", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not (otp_entered and new_password and confirm_password):
            flash("Please complete all required fields.", "warning")
            return render_template("verify_otp.html", email=email)

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return render_template("verify_otp.html", email=email)

        if new_password != confirm_password:
            flash("Passwords do not match. Please re-enter.", "danger")
            return render_template("verify_otp.html", email=email)

        try:
            now = datetime.now()
            reset_record = PasswordReset.query.filter(
                PasswordReset.email.ilike(email),
                PasswordReset.otp == otp_entered,
                PasswordReset.used == False,
                PasswordReset.expires_at >= now
            ).order_by(PasswordReset.id.desc()).first()

            if not reset_record:
                flash("Invalid or expired verification code (OTP). Please check and re-enter, or request a new code.", "danger")
                return render_template("verify_otp.html", email=email)

            user = User.query.filter(User.email.ilike(email)).first()
            if user:
                user.password_hash = generate_password_hash(new_password)
                reset_record.used = True
                db.session.commit()
                session.pop("reset_email", None)
                flash("Your password has been successfully reset! Please sign in with your new password.", "success")
                return redirect(url_for("login"))
            else:
                flash("No citizen account found for this email address. Please register a new account.", "warning")
                return redirect(url_for("register"))
        except Exception as e_verify:
            db.session.rollback()
            print(f">> verify_otp error: {e_verify}")
            flash("An error occurred while resetting your password. Please try again or request a new code.", "danger")
            return render_template("verify_otp.html", email=email)

    return render_template("verify_otp.html", email=email)

@app.route("/forgot-password/resend", methods=["GET", "POST"])
def resend_otp():
    email = session.get("reset_email")
    if not email:
        flash("Password reset session expired. Please enter your email again.", "warning")
        return redirect(url_for("forgot_password"))

    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now() + timedelta(minutes=10)

    try:
        PasswordReset.query.filter_by(email=email, used=False).update({"used": True})
        pr = PasswordReset(email=email, otp=otp_code, expires_at=expires_at, used=False)
        db.session.add(pr)
        db.session.commit()
    except Exception as e_pr:
        db.session.rollback()
        print(f">> PasswordReset DB notice in resend: {e_pr}")

    sent, msg = send_email_otp(email, otp_code)
    flash(msg, "success" if sent else "info")
    return redirect(url_for("verify_otp"))

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out successfully.", "info")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    """User Dashboard displaying submitted reports and match alerts"""
    user_id = session.get("user_id")
    my_lost = Report.query.filter_by(user_id=user_id, report_type="lost").order_by(Report.id.desc()).all()
    my_found = Report.query.filter_by(user_id=user_id, report_type="found").order_by(Report.id.desc()).all()

    # Calculate match count for user reports
    possible_matches_count = 0
    for r in (my_lost + my_found):
        if r.status != "Resolved":
            m = get_possible_matches_for_item(r, min_score=30)
            possible_matches_count += len(m)

    my_claims = Claim.query.filter_by(claimant_id=user_id).order_by(Claim.id.desc()).all()

    return render_template(
        "dashboard.html",
        my_lost=my_lost,
        my_found=my_found,
        my_claims=my_claims,
        possible_matches_count=possible_matches_count
    )

@app.route("/report", methods=["GET", "POST"])
@login_required
def report():
    """Submit Lost or Found Item Report"""
    if request.method == "POST":
        report_type = request.form.get("report_type", "lost").strip().lower()
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        incident_date = request.form.get("incident_date", "").strip()
        incident_time = request.form.get("incident_time", "").strip()
        location = request.form.get("location", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        contact_info = request.form.get("contact_info", "").strip()
        identifying_details = request.form.get("identifying_details", "").strip()

        if not (title and category and description and incident_date and location and contact_name and contact_info):
            flash("Please fill in all mandatory fields marked with *", "danger")
            return render_template("report.html", categories=CATEGORIES, form_data=request.form)

        # Server-side deduplication check: prevent accidental double-click duplicates within 60s
        recent_dup = Report.query.filter_by(
            user_id=session.get("user_id"),
            title=title,
            category=category,
            incident_date=incident_date,
            location=location,
            report_type=report_type
        ).order_by(Report.id.desc()).first()

        if recent_dup:
            diff_secs = (datetime.now() - recent_dup.created_at).total_seconds() if recent_dup.created_at else 999
            if diff_secs < 60:
                flash(f"Duplicate submission prevented. Your report for '{title}' is already recorded in the registry.", "info")
                return redirect(url_for("item_details", item_id=recent_dup.id))

        image_filename = None
        if "photo" in request.files:
            file = request.files["photo"]
            if file and file.filename and allowed_file(file.filename):
                file_bytes = file.read()
                if len(file_bytes) > 0 and len(file_bytes) <= 4 * 1024 * 1024:
                    # Convert to Base64 Data URI for permanent database storage on serverless/cloud
                    ext = file.filename.rsplit(".", 1)[1].lower()
                    mime_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    b64_str = base64.b64encode(file_bytes).decode("utf-8")
                    image_filename = f"data:{mime_type};base64,{b64_str}"
                else:
                    fname = secure_filename(file.filename)
                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    image_filename = f"{timestamp}_{fname}"
                    try:
                        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
                        with open(os.path.join(app.config["UPLOAD_FOLDER"], image_filename), "wb") as f_out:
                            f_out.write(file_bytes)
                    except Exception as e_up:
                        print(f">> File upload notice: {e_up}")

        new_report = Report(
            user_id=session.get("user_id"),
            report_type=report_type,
            title=title,
            category=category,
            description=description,
            incident_date=incident_date,
            incident_time=incident_time,
            location=location,
            image_filename=image_filename,
            contact_name=contact_name,
            contact_info=contact_info,
            identifying_details=identifying_details,
            status="Active"
        )
        db.session.add(new_report)
        db.session.commit()

        flash(f"Report for '{title}' submitted successfully!", "success")
        return redirect(url_for("item_details", item_id=new_report.id))

    default_type = request.args.get("type", "lost")
    prefill = {
        "report_type": default_type,
        "contact_name": session.get("user_name", ""),
        "contact_info": session.get("user_email", "")
    }
    return render_template("report.html", categories=CATEGORIES, form_data=prefill)

@app.route("/search")
@login_required
def search():
    """Full-featured Search & Filter Page"""
    query = request.args.get("q", "").strip()
    r_type = request.args.get("type", "all").strip().lower()
    cat = request.args.get("category", "").strip()
    loc = request.args.get("location", "").strip()
    status_filter = request.args.get("status", "all").strip().title()
    filter_owner = request.args.get("owner", "all").strip().lower()

    q = Report.query

    if filter_owner == "me":
        q = q.filter_by(user_id=session.get("user_id"))

    if query:
        q = q.filter(
            db.or_(
                Report.title.ilike(f"%{query}%"),
                Report.description.ilike(f"%{query}%"),
                Report.location.ilike(f"%{query}%")
            )
        )
    if r_type in ("lost", "found"):
        q = q.filter_by(report_type=r_type)
    if cat:
        q = q.filter_by(category=cat)
    if loc:
        q = q.filter(Report.location.ilike(f"%{loc}%"))
    if status_filter in ("Active", "Matched", "Resolved"):
        q = q.filter_by(status=status_filter)
    else:
        # By default, active reports show first
        q = q.order_by(db.case((Report.status == 'Resolved', 1), else_=0))

    results = q.order_by(Report.id.desc()).all()

    return render_template(
        "search.html",
        results=results,
        categories=CATEGORIES,
        q=query,
        current_type=r_type,
        current_cat=cat,
        current_loc=loc,
        current_status=status_filter,
        current_owner=filter_owner
    )

def get_report_or_404(item_id):
    report = db.session.get(Report, item_id)
    if not report:
        abort(404)
    return report

@app.route("/item/<int:item_id>")
@login_required
def item_details(item_id):
    """Detailed Item View with Ownership Verification and Possible Matches"""
    report = get_report_or_404(item_id)
    matches = get_possible_matches_for_item(report, min_score=25)
    user_claim = None
    if "user_id" in session:
        user_claim = Claim.query.filter_by(found_report_id=item_id, claimant_id=session.get("user_id")).first()
    return render_template("details.html", item=report, matches=matches, user_claim=user_claim)

@app.route("/item/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_report(item_id):
    report = get_report_or_404(item_id)
    # Only owner or admin can edit
    if report.user_id != session.get("user_id") and session.get("user_role") != "admin":
        flash("You are not authorized to edit this report.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    if request.method == "POST":
        report.title = request.form.get("title", report.title).strip()
        report.category = request.form.get("category", report.category).strip()
        report.description = request.form.get("description", report.description).strip()
        report.incident_date = request.form.get("incident_date", report.incident_date).strip()
        report.incident_time = request.form.get("incident_time", report.incident_time).strip()
        report.location = request.form.get("location", report.location).strip()
        report.contact_name = request.form.get("contact_name", report.contact_name).strip()
        report.contact_info = request.form.get("contact_info", report.contact_info).strip()
        report.identifying_details = request.form.get("identifying_details", report.identifying_details).strip()

        if "photo" in request.files:
            file = request.files["photo"]
            if file and file.filename and allowed_file(file.filename):
                file_bytes = file.read()
                if len(file_bytes) > 0 and len(file_bytes) <= 4 * 1024 * 1024:
                    ext = file.filename.rsplit(".", 1)[1].lower()
                    mime_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    b64_str = base64.b64encode(file_bytes).decode("utf-8")
                    report.image_filename = f"data:{mime_type};base64,{b64_str}"
                else:
                    fname = secure_filename(file.filename)
                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    try:
                        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
                        with open(os.path.join(app.config["UPLOAD_FOLDER"], f"{timestamp}_{fname}"), "wb") as f_out:
                            f_out.write(file_bytes)
                        report.image_filename = f"{timestamp}_{fname}"
                    except Exception as e_up:
                        print(f">> File upload notice: {e_up}")

        db.session.commit()
        flash("Report updated successfully.", "success")
        return redirect(url_for("item_details", item_id=item_id))

    return render_template("report.html", categories=CATEGORIES, form_data=report, is_edit=True)

@app.route("/item/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_report(item_id):
    report = get_report_or_404(item_id)
    if report.user_id != session.get("user_id") and session.get("user_role") != "admin":
        flash("You are not authorized to delete this report.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    # Foreign-Key Custody Retention Guard: Prevent deletion if formal claims exist
    has_claims = Claim.query.filter(
        (Claim.lost_report_id == item_id) | (Claim.found_report_id == item_id)
    ).first() is not None

    if has_claims:
        flash("Permanent deletion prohibited: This report is linked to official ownership claims and custody audit history.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    try:
        db.session.delete(report)
        db.session.commit()
        flash("Report deleted.", "success")
    except Exception as e_del:
        db.session.rollback()
        flash("Deletion prohibited by database constraints: Associated claim history exists.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    if session.get("user_role") == "admin":
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("dashboard"))

@app.route("/item/<int:item_id>/status", methods=["POST"])
@login_required
def update_status(item_id):
    report = get_report_or_404(item_id)
    if report.user_id != session.get("user_id") and session.get("user_role") != "admin":
        flash("You are not authorized to modify this status.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    new_status = request.form.get("status", "Active").strip()

    # Anti-Bypass Security Rule: Direct manual resolution is strictly prohibited
    if new_status == "Resolved":
        flash("Direct resolution prohibited. Reports can only be marked 'Resolved' through an approved ownership claim or audited exceptional administrative resolution.", "warning")
        return redirect(request.referrer or url_for("item_details", item_id=item_id))

    if new_status in ("Active", "Matched"):
        report.status = new_status
        db.session.commit()
        flash(f"Item status updated to '{new_status}'.", "info")

    return redirect(request.referrer or url_for("item_details", item_id=item_id))

# ==============================================================================
# Item Claim & Ownership Verification Routes
# ==============================================================================

@app.route("/claim/<int:found_id>", methods=["GET", "POST"])
@login_required
def claim_item(found_id):
    """Submit an Ownership Claim against a Found Item"""
    found_report = get_report_or_404(found_id)
    user_id = session.get("user_id")

    if found_report.report_type != "found":
        flash("Claims can only be filed against Found item reports.", "warning")
        return redirect(url_for("item_details", item_id=found_id))

    if found_report.status == "Resolved":
        flash("This found item has already been Resolved and cannot be claimed.", "danger")
        return redirect(url_for("item_details", item_id=found_id))

    if found_report.user_id == user_id:
        flash("You cannot file an ownership claim on your own report.", "warning")
        return redirect(url_for("item_details", item_id=found_id))

    if request.method == "POST":
        lost_report_id = request.form.get("lost_report_id")
        claim_details = request.form.get("claim_details", "").strip()

        if not lost_report_id or not claim_details:
            flash("Please select your matching lost report and provide private ownership verification details.", "warning")
            return redirect(url_for("claim_item", found_id=found_id))

        try:
            lost_report_id = int(lost_report_id)
        except (ValueError, TypeError):
            flash("Invalid lost report selection.", "danger")
            return redirect(url_for("claim_item", found_id=found_id))

        lost_report = db.session.get(Report, lost_report_id)
        if not lost_report or lost_report.report_type != "lost":
            flash("Selected lost report is invalid.", "danger")
            return redirect(url_for("claim_item", found_id=found_id))

        # Authorization Check: Must be the verified owner of the lost report
        if lost_report.user_id != user_id:
            abort(403)

        if lost_report.status == "Resolved":
            flash("The selected lost report is already marked as Resolved.", "danger")
            return redirect(url_for("claim_item", found_id=found_id))

        # Check for existing claim on this lost/found pair (Unique Constraint Enforcement)
        existing_claim = Claim.query.filter_by(
            lost_report_id=lost_report.id,
            found_report_id=found_report.id
        ).first()

        if existing_claim:
            flash(f"A claim has already been registered for this report pair (Claim #{existing_claim.id}, Status: {existing_claim.status}).", "warning")
            return redirect(url_for("my_claims"))

        try:
            new_claim = Claim(
                lost_report_id=lost_report.id,
                found_report_id=found_report.id,
                claimant_id=user_id,
                claim_details=claim_details,
                status="Pending"
            )
            # Safeguard 1: Report statuses remain UNCHANGED when claim is submitted
            db.session.add(new_claim)
            db.session.flush() # Populate new_claim.id
            record_claim_audit(
                new_claim.id,
                "SUBMITTED",
                actor_id=user_id,
                reason="Ownership claim submitted by registered citizen owner."
            )
            db.session.commit()
            flash("Your ownership claim has been submitted securely and is pending administrative review.", "success")
            return redirect(url_for("my_claims"))
        except Exception as e_claim:
            db.session.rollback()
            flash("A claim for this lost/found report pair already exists or could not be created.", "warning")
            return redirect(url_for("my_claims"))

    # GET Request: Fetch user's active lost reports
    my_lost_reports = Report.query.filter_by(
        user_id=user_id,
        report_type="lost"
    ).filter(Report.status != "Resolved").order_by(Report.id.desc()).all()

    existing_user_claim = Claim.query.filter_by(
        found_report_id=found_id,
        claimant_id=user_id
    ).first()

    return render_template(
        "claim.html",
        found_item=found_report,
        my_lost_reports=my_lost_reports,
        existing_claim=existing_user_claim
    )

@app.route("/my-claims")
@login_required
def my_claims():
    """User view of submitted ownership claims"""
    user_id = session.get("user_id")
    claims = Claim.query.filter_by(claimant_id=user_id).order_by(Claim.id.desc()).all()
    return render_template("my_claims.html", claims=claims)

@app.route("/admin/claim/<int:claim_id>/review", methods=["POST"])
@admin_required
def admin_review_claim(claim_id):
    """Admin Claim Approval or Rejection with Concurrency Control & Audit Trail"""
    action = request.form.get("action", "").strip().lower()
    admin_notes = request.form.get("admin_notes", "").strip()
    admin_id = session.get("user_id")

    if action not in ("approve", "reject"):
        flash("Invalid review action specified.", "danger")
        return redirect(url_for("admin_dashboard"))

    try:
        claim = Claim.query.filter_by(id=claim_id).first()
        if not claim:
            abort(404)

        if claim.status != "Pending":
            flash(f"Concurrency Notice: Claim #{claim.id} has already been reviewed (Status: {claim.status}).", "warning")
            return redirect(url_for("admin_dashboard"))

        found_report = db.session.get(Report, claim.found_report_id)
        lost_report = db.session.get(Report, claim.lost_report_id)

        if action == "approve":
            # Concurrency check 1: Atomic conditional update on found report
            # Only succeeds if found_report is not already Resolved
            rows_found = Report.query.filter(
                Report.id == claim.found_report_id,
                Report.status != "Resolved"
            ).update({"status": "Resolved"}, synchronize_session=False)

            if rows_found == 0:
                db.session.rollback()
                flash("Conflict: This found report has already been Resolved by another review.", "danger")
                return redirect(url_for("admin_dashboard"))

            # Concurrency check 2: Atomic conditional update on lost report
            rows_lost = Report.query.filter(
                Report.id == claim.lost_report_id,
                Report.status != "Resolved"
            ).update({"status": "Resolved"}, synchronize_session=False)

            if rows_lost == 0:
                db.session.rollback()
                flash("Conflict: This lost report has already been Resolved.", "danger")
                return redirect(url_for("admin_dashboard"))

            reason_text = admin_notes or "Ownership verified and approved by Administrator."

            # Concurrency check 3: Atomic conditional update on claim + set approved_found_report_id
            # The database unique constraint uq_claims_approved_found guarantees at storage engine level
            # that only ONE claim for this found report can be approved across concurrent transactions.
            rows_claim = Claim.query.filter(
                Claim.id == claim.id,
                Claim.status == "Pending"
            ).update({
                "status": "Approved",
                "approved_found_report_id": claim.found_report_id,
                "reviewed_by": admin_id,
                "reviewed_at": datetime.now(),
                "admin_notes": reason_text
            }, synchronize_session=False)

            if rows_claim == 0:
                db.session.rollback()
                flash(f"Concurrency Notice: Claim #{claim.id} is no longer Pending.", "warning")
                return redirect(url_for("admin_dashboard"))

            # Append-Only Claim Audit History
            record_claim_audit(claim.id, "APPROVED", actor_id=admin_id, reason=reason_text)

            # Concurrently reject any competing pending claims on the same found item
            competing_claims = Claim.query.filter(
                Claim.found_report_id == claim.found_report_id,
                Claim.id != claim.id,
                Claim.status == "Pending"
            ).order_by(Claim.id).all()

            for comp in competing_claims:
                comp.status = "Rejected"
                comp.approved_found_report_id = None
                comp.reviewed_by = admin_id
                comp.reviewed_at = datetime.now()
                comp_reason = f"Automatically closed: Property ownership verified and resolved via approved Claim #{claim.id}."
                comp.admin_notes = comp_reason
                record_claim_audit(comp.id, "AUTO_REJECTED_COMPETING", actor_id=admin_id, reason=comp_reason)

            db.session.commit()
            flash(f"Claim #{claim.id} APPROVED! Reports #{claim.found_report_id} and #{claim.lost_report_id} marked as Resolved.", "success")

        elif action == "reject":
            reason_text = admin_notes or "Claim rejected: Ownership proof details did not match."
            rows_claim = Claim.query.filter(
                Claim.id == claim.id,
                Claim.status == "Pending"
            ).update({
                "status": "Rejected",
                "approved_found_report_id": None,
                "reviewed_by": admin_id,
                "reviewed_at": datetime.now(),
                "admin_notes": reason_text
            }, synchronize_session=False)

            if rows_claim == 0:
                db.session.rollback()
                flash(f"Concurrency Notice: Claim #{claim.id} is no longer Pending.", "warning")
                return redirect(url_for("admin_dashboard"))

            # Append-Only Claim Audit History
            record_claim_audit(claim.id, "REJECTED", actor_id=admin_id, reason=reason_text)

            # Safeguard: Report statuses remain UNCHANGED upon rejection
            db.session.commit()
            flash(f"Claim #{claim.id} has been REJECTED. Reports remain active in the registry.", "info")

    except IntegrityError as e_int:
        db.session.rollback()
        safe_msg = sanitize_error_msg(e_int)
        print(f">> Claim review concurrency conflict: {safe_msg}")
        flash("Concurrency Conflict: Another administrator approved a claim for this found property simultaneously. Only one claim can be approved.", "danger")
    except Exception as e_rev:
        db.session.rollback()
        safe_msg = sanitize_error_msg(e_rev)
        print(f">> Claim review transaction error: {safe_msg}")
        flash(f"Transaction conflict during claim review. Please refresh and retry.", "danger")

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/claim/<int:claim_id>/reopen", methods=["POST"])
@admin_required
def admin_reopen_claim(claim_id):
    """Admin Reopening of a Rejected Claim with Audit Trail"""
    reopen_notes = request.form.get("reopen_notes", "").strip()
    admin_id = session.get("user_id")

    try:
        claim = Claim.query.filter_by(id=claim_id).first()
        if not claim:
            abort(404)

        if claim.status != "Rejected":
            flash("Only rejected claims can be reopened for re-evaluation.", "warning")
            return redirect(url_for("admin_dashboard"))

        found_report = db.session.get(Report, claim.found_report_id)
        if found_report.status == "Resolved":
            flash("Cannot reopen claim: The associated found item has already been Resolved.", "danger")
            return redirect(url_for("admin_dashboard"))

        # Concurrency check: Ensure no other claim is already approved for this found report
        existing_approved = Claim.query.filter_by(found_report_id=claim.found_report_id, status="Approved").first()
        if existing_approved:
            flash(f"Cannot reopen claim: Found item already has an approved claim (Claim #{existing_approved.id}).", "danger")
            return redirect(url_for("admin_dashboard"))

        claim.status = "Pending"
        claim.approved_found_report_id = None
        claim.reviewed_by = None
        claim.reviewed_at = None
        reopen_entry = f"Reopened by Admin #{admin_id}: {reopen_notes}" if reopen_notes else f"Reopened for re-evaluation by Admin #{admin_id}."
        claim.admin_notes = f"{claim.admin_notes}\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {reopen_entry}" if claim.admin_notes else reopen_entry

        # Append-Only Claim Audit History
        record_claim_audit(claim.id, "REOPENED", actor_id=admin_id, reason=reopen_notes or "Reopened for re-evaluation by Administrator.")

        db.session.commit()
        flash(f"Claim #{claim.id} has been reopened to Pending review.", "success")
    except Exception as e_reop:
        db.session.rollback()
        safe_msg = sanitize_error_msg(e_reop)
        print(f">> Claim reopen error: {safe_msg}")
        flash("Transaction error during claim reopen.", "danger")

    return redirect(url_for("admin_dashboard"))

@app.route("/admin/item/<int:item_id>/exceptional-resolution", methods=["POST"])
@admin_required
def exceptional_resolution(item_id):
    """Audited Exceptional In-Person Resolution Without Bypassing Ownership Verification"""
    report = get_report_or_404(item_id)
    if report.status == "Resolved":
        flash("This report is already Resolved.", "warning")
        return redirect(url_for("item_details", item_id=item_id))

    outcome_reason = request.form.get("outcome_reason", "").strip()
    if not outcome_reason:
        flash("An administrative verification outcome explanation is required for exceptional resolution.", "warning")
        return redirect(request.referrer or url_for("item_details", item_id=item_id))

    # Safeguard 4: Personal identification - Do not collect or store government ID numbers or copies.
    # Record only that an authorized administrator performed in-person verification and the outcome.
    admin_id = session.get("user_id")

    try:
        # Prevent leaving related pending claims open: close them with audited records
        open_claims = []
        if report.report_type == "found":
            open_claims = Claim.query.filter_by(found_report_id=report.id, status="Pending").all()
        elif report.report_type == "lost":
            open_claims = Claim.query.filter_by(lost_report_id=report.id, status="Pending").all()

        for cl in open_claims:
            cl.status = "Rejected"
            cl.approved_found_report_id = None
            cl.reviewed_by = admin_id
            cl.reviewed_at = datetime.now()
            cl_reason = f"Closed: Associated {report.report_type} report #{report.id} resolved via exceptional in-person administrative handover: {outcome_reason}"
            cl.admin_notes = cl_reason
            record_claim_audit(cl.id, "EXCEPTIONAL_RESOLVE_CLOSED", actor_id=admin_id, reason=cl_reason)

        audit_entry = f"Exceptional In-Person Verification: Authorized Administrator #{admin_id} verified owner in person and completed handover. Outcome: {outcome_reason}."
        report.status = "Resolved"
        report.admin_resolution_notes = audit_entry
        db.session.commit()
        flash(f"Report #{report.id} and {len(open_claims)} associated claim(s) resolved via audited exceptional in-person verification.", "success")
    except Exception as e_exp:
        db.session.rollback()
        safe_msg = sanitize_error_msg(e_exp)
        print(f">> Exceptional resolution error: {safe_msg}")
        flash("Transaction conflict during exceptional resolution.", "danger")

    return redirect(request.referrer or url_for("item_details", item_id=item_id))

@app.route("/matches")
@login_required
def matches_matrix():
    """System-wide Possible Matches Matrix"""
    pairs = get_all_possible_matches(min_score=30)
    return render_template("matches.html", pairs=pairs)

@app.route("/admin")
@admin_required
def admin_dashboard():
    """Admin Control Panel"""
    try:
        reports = Report.query.order_by(Report.id.desc()).all()
        users = User.query.order_by(User.id.desc()).all()
        claims = Claim.query.order_by(Claim.id.desc()).all()

        stats = {
            "total_reports": len(reports),
            "total_users": len(users),
            "total_claims": len(claims),
            "pending_claims": sum(1 for c in claims if c.status == "Pending"),
            "approved_claims": sum(1 for c in claims if c.status == "Approved"),
            "rejected_claims": sum(1 for c in claims if c.status == "Rejected"),
            "lost_active": sum(1 for r in reports if r.report_type == "lost" and r.status == "Active"),
            "found_active": sum(1 for r in reports if r.report_type == "found" and r.status == "Active"),
            "resolved": sum(1 for r in reports if r.status == "Resolved")
        }
        return render_template("admin.html", reports=reports, users=users, claims=claims, stats=stats)
    except Exception as e_adm:
        db.session.rollback()
        print(f">> Admin dashboard notice: {e_adm}")
        flash("Admin dashboard loaded with fallback.", "warning")
        return render_template("admin.html", reports=[], users=[], claims=[], stats={"total_reports":0, "total_users":0, "total_claims":0, "pending_claims":0, "approved_claims":0, "rejected_claims":0, "lost_active":0, "found_active":0, "resolved":0})

@app.route("/api/stats")
def api_stats():
    return jsonify({
        "total": Report.query.count(),
        "lost": Report.query.filter_by(report_type="lost").count(),
        "found": Report.query.filter_by(report_type="found").count(),
        "resolved": Report.query.filter_by(status="Resolved").count()
    })

# ==============================================================================
# Error Handlers
# ==============================================================================

@app.errorhandler(404)
def not_found_error(error):
    return render_template("details.html", not_found=True), 404

@app.errorhandler(500)
def internal_error(error):
    try:
        db.session.rollback()
    except Exception:
        pass

    # Check whether running in a production environment or if debug is disabled
    is_prod = bool(
        is_production_env or
        os.environ.get("VERCEL") or
        os.environ.get("FLASK_ENV") == "production" or
        os.environ.get("ENVIRONMENT") == "production" or
        os.environ.get("NODE_ENV") == "production" or
        not app.debug
    )

    if not is_prod and app.debug:
        # Development-only debugging: strictly disabled in production
        import traceback
        tb = traceback.format_exc()
        safe_err = sanitize_error_msg(error)
        safe_tb = sanitize_error_msg(tb)
        print(f">> 500 Internal Error (Dev Mode): {safe_err}\n{safe_tb}")
    else:
        # Production: Safe, generic notice. Never print raw exceptions, tracebacks, or sensitive data.
        print(">> 500 Internal Server Error: An unexpected error occurred. Details suppressed in production.")

    return "<h3>500 Internal Server Error</h3><p>Something went wrong. Please <a href='/'>return to the registry portal</a>.</p>", 500

# ==============================================================================
# Application Entry Point
# ==============================================================================

with app.app_context():
    try:
        run_schema_migrations()
        seed_initial_data()
        _db_initialized = True
    except Exception as e_db:
        safe_msg = sanitize_error_msg(e_db)
        if is_production_env or is_remote_db_configured:
            # Production environment: fail initialization clearly and never switch to temporary SQLite
            print(f">> CRITICAL: Production database initialization failed: {safe_msg}")
            _db_initialized = False
        else:
            # Local development fallback only when explicitly allowed and not in production
            dev_fallback_allowed = os.environ.get("ALLOW_SQLITE_DEV_FALLBACK", "false").lower() in ("true", "1", "yes")
            if dev_fallback_allowed and not os.environ.get("VERCEL"):
                print(f">> Notice: Remote DB unavailable ({safe_msg}).")
                print(">> Falling back to local development SQLite database as permitted by configuration.")
                sqlite_file = os.path.join(BASE_DIR, "lost_found.db")
                app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{sqlite_file}"
                app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {}
                try:
                    run_schema_migrations()
                    seed_initial_data()
                    _db_initialized = True
                except Exception as e_sql:
                    safe_sql = sanitize_error_msg(e_sql)
                    print(f">> Local SQLite database initialization notice ({safe_sql}).")
                    _db_initialized = False
            else:
                print(f">> Database initialization failed: {safe_msg}")
                _db_initialized = False

def get_debug_mode():
    """
    Determines whether Flask debug mode should be enabled.
    In production environments (as configured by is_production_env), debug mode is strictly disabled (False).
    In local development environments, debug mode is retained (default True, configurable via FLASK_DEBUG).
    """
    prod = bool(
        is_production_env or
        os.environ.get("VERCEL") or
        os.environ.get("FLASK_ENV") == "production" or
        os.environ.get("ENVIRONMENT") == "production" or
        os.environ.get("NODE_ENV") == "production"
    )
    if prod:
        return False
    return os.environ.get("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    debug_mode = get_debug_mode()
    print(f">> Digital Lost-and-Found Platform running at http://{host}:{port} (debug={debug_mode})")
    app.run(host=host, port=port, debug=debug_mode)
