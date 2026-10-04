import os
import re
import random
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps

# Ensure pure-Python mode for SQLAlchemy to avoid Windows Application Control policy blocks
os.environ["DISABLE_SQLALCHEMY_CEXT"] = "1"

from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import (
    Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
)
from flask_sqlalchemy import SQLAlchemy

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
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-lostfound-hub-2026-key")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Resend API Key for Email OTP (https://resend.com)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()

# Administrator Credentials (Configurable via .env)
ADMIN_ID = os.environ.get("ADMIN_ID", "admin").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123").strip()

# Database configuration: TiDB Cloud (MySQL compatible) with SQLite fallback
tidb_host = os.environ.get("TIDB_HOST")
tidb_user = os.environ.get("TIDB_USER")
tidb_pass = os.environ.get("TIDB_PASSWORD")
tidb_db   = os.environ.get("TIDB_DATABASE", "lost_found")
tidb_port = os.environ.get("TIDB_PORT", "4000")

raw_db_url = os.environ.get("TIDB_URL") or os.environ.get("DATABASE_URL", "sqlite:///lost_found.db")

if tidb_host and tidb_user:
    db_url = f"mysql+pymysql://{tidb_user}:{tidb_pass}@{tidb_host}:{tidb_port}/{tidb_db}"
elif raw_db_url and ("mysql" in raw_db_url or "tidb" in raw_db_url):
    db_url = raw_db_url
    if db_url.startswith("mysql://"):
        db_url = db_url.replace("mysql://", "mysql+pymysql://", 1)
elif raw_db_url and raw_db_url.startswith("postgres://"):
    db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
else:
    db_url = raw_db_url

if os.environ.get("VERCEL") and "sqlite" in db_url:
    db_url = "sqlite:////tmp/lost_found.db"

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
    image_filename = db.Column(db.String(255), nullable=True)
    contact_name = db.Column(db.String(100), nullable=False)
    contact_info = db.Column(db.String(120), nullable=False)
    identifying_details = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="Active", nullable=False, index=True) # 'Active', 'Matched', 'Resolved'
    created_at = db.Column(db.DateTime, default=datetime.now)

class PasswordReset(db.Model):
    __tablename__ = "password_resets"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    otp = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

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
    admin_user = User.query.filter((User.role == "admin") | (User.email == admin_email) | (User.email == "admin@lostfound.org")).first()
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
        # Keep admin credentials in sync with .env
        admin_user.password_hash = generate_password_hash(ADMIN_PASSWORD)
        admin_user.email = admin_email
        db.session.commit()

# ==============================================================================
# Web Routes
# ==============================================================================

_db_initialized = False

@app.before_request
def ensure_db_initialized():
    global _db_initialized
    if not _db_initialized:
        try:
            db.create_all()
            seed_initial_data()
            _db_initialized = True
        except Exception as e:
            db.session.rollback()
            print(f">> DB init notice: {e}")

@app.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/debug-path")
def debug_path():
    import json
    return json.dumps({
        "path_info": request.environ.get("PATH_INFO"),
        "request_path": request.path,
        "matched_path": request.environ.get("HTTP_X_MATCHED_PATH"),
        "query_string": request.environ.get("QUERY_STRING"),
        "url": request.url,
    }, indent=2), 200, {"Content-Type": "application/json"}

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
        if (login_id == ADMIN_ID.lower() or login_id == admin_email or login_id == "admin" or login_id == "admin@lostfound.org") and password == ADMIN_PASSWORD:
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
        role = request.form.get("role", "user").strip().lower()
        if role not in ("user", "admin"):
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
            flash("Account not found. Please register.", "danger")
            return redirect(url_for("register"))

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

    return render_template(
        "dashboard.html",
        my_lost=my_lost,
        my_found=my_found,
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

        image_filename = None
        if "photo" in request.files:
            file = request.files["photo"]
            if file and file.filename and allowed_file(file.filename):
                fname = secure_filename(file.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                image_filename = f"{timestamp}_{fname}"
                try:
                    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
                    file.save(os.path.join(app.config["UPLOAD_FOLDER"], image_filename))
                except Exception as e_up:
                    print(f">> File upload notice: {e_up}")
                    image_filename = None

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

    q = Report.query

    if query:
        q = q.filter(
            db.or_(
                Report.title.ilike(f"%{query}%"),
                Report.description.ilike(f"%{query}%"),
                Report.location.ilike(f"%{query}%"),
                Report.identifying_details.ilike(f"%{query}%")
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
        current_status=status_filter
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
    return render_template("details.html", item=report, matches=matches)

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
                fname = secure_filename(file.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                try:
                    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
                    file.save(os.path.join(app.config["UPLOAD_FOLDER"], f"{timestamp}_{fname}"))
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

    db.session.delete(report)
    db.session.commit()
    flash("Report deleted.", "success")
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

    new_status = request.form.get("status", "Active")
    if new_status in ("Active", "Matched", "Resolved"):
        report.status = new_status
        db.session.commit()
        flash(f"Item status updated to '{new_status}'.", "info")

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
    reports = Report.query.order_by(Report.id.desc()).all()
    users = User.query.order_by(User.id.desc()).all()

    stats = {
        "total_reports": len(reports),
        "total_users": len(users),
        "lost_active": sum(1 for r in reports if r.report_type == "lost" and r.status == "Active"),
        "found_active": sum(1 for r in reports if r.report_type == "found" and r.status == "Active"),
        "resolved": sum(1 for r in reports if r.status == "Resolved")
    }
    return render_template("admin.html", reports=reports, users=users, stats=stats)

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
    db.session.rollback()
    return "<h3>500 Internal Server Error</h3><p>Something went wrong. Please return home.</p>", 500

# ==============================================================================
# Application Entry Point
# ==============================================================================

with app.app_context():
    try:
        db.create_all()
        seed_initial_data()
    except Exception as e_db:
        print(f">> Notice: TiDB/Remote DB connection notice ({e_db}).")
        print(">> Falling back to local SQLite database.")
        sqlite_file = os.path.join("/tmp" if os.environ.get("VERCEL") else BASE_DIR, "lost_found.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{sqlite_file}"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {}
        db.create_all()
        seed_initial_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f">> Digital Lost-and-Found Platform running at http://{host}:{port}")
    app.run(host=host, port=port, debug=True)
