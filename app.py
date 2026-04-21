"""
Laundry Lounge Management System — Flask Backend
app.py  (Full update — synced with login, superadmin, admin, operator HTML)

CHANGES vs previous revision:
  NEW1.  /track/name/<name>  — public tracking by customer name (login page)
  NEW2.  /api/admin/issues   — GET all issue reports for admin panel
  NEW3.  /api/admin/issues/<id>/resolve  — PUT to resolve an issue
  NEW4.  /api/staff/issues/report now inserts with reporter name for admin view
  NEW5.  Superadmin /api/superadmin/change-own-password already present, kept.
  NEW6.  db_init and db_migrate updated for issues table reporter_name column.
  FIX1.  Duplicate db_init UI seed block removed (was duplicated at bottom).
  FIX2.  Service rates label returned in /api/staff/my-orders.
  FIX3.  Issues table gets reporter_name VARCHAR column for admin display.
"""

import json
from flask_mail import Mail, Message as MailMessage
import threading
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mysqldb import MySQL
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, flash, make_response)
from flask.cli import with_appcontext
from functools import wraps
from datetime import datetime, timedelta, date
from decimal import Decimal
import string
import secrets
import click
import io
import math
import csv
import os
from dotenv import load_dotenv

load_dotenv()

# ─── App setup ──────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY", "laundry-lounge-secret-2026-CHANGE-ME"
)
app.permanent_session_lifetime = timedelta(days=7)

# ── MySQL ──────────────────────────────────────────────────────
app.config["MYSQL_HOST"] = os.environ.get("MYSQL_HOST",     "localhost")
app.config["MYSQL_USER"] = os.environ.get("MYSQL_USER",     "root")
app.config["MYSQL_PASSWORD"] = os.environ.get("MYSQL_PASSWORD", "")
app.config["MYSQL_DB"] = os.environ.get("MYSQL_DB",       "llms_db_2026")
app.config["MYSQL_CURSORCLASS"] = "DictCursor"
mysql = MySQL(app)

# ── Internal secret ────────────────────────────────────────────
INTERNAL_SECRET = os.environ.get(
    "INTERNAL_SECRET", "change-me-internal-secret")

# ── Flask-Mail ─────────────────────────────────────────────────
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER",  "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.environ.get(
    "MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get(
    "MAIL_SENDER", os.environ.get("MAIL_USERNAME", "noreply@laundrylounge.com")
)
mail = Mail(app)

# ─── Constants ──────────────────────────────────────────────────
MACHINE_CAPACITY = 8
WASH_SECS = 1 * 60   # 10 min (dev); production = 44*60
DRY_SECS = 1 * 60
DOWNY_SECS = 1 * 60
DRYER_SURCHARGE = 20.0
FOLD_MIN_SECS = 10 * 60
FOLD_MAX_SECS = 15 * 60

SERVICE_RATES = {
    "single_wash": {"label": "Single Wash",            "rate": 30},
    "double_wash": {"label": "Double Wash",            "rate": 35},
    "household":   {"label": "Household Items",        "rate": 45},
    "heavy_wash":  {"label": "Heavy Wash (Comforter)", "rate": 75},
    "soak_whites": {"label": "Soak for Whites",        "rate": 50},
}

TRACKING_BASE_URL = os.environ.get(
    "TRACKING_BASE_URL", "http://localhost:5000")

UI_DEFAULTS = {
    # Theme
    "ui_theme_preset": "teal",
    "ui_accent":       "#00B4D8",
    "ui_accent2":      "#0077A8",
    "ui_bg":           "#E8F8FB",
    "ui_text":         "#0A2A35",
    "ui_font":         "dmsans",
    # Brand
    "ui_brand_name":   "Laundry Lounge",
    "ui_tagline":      "Your Local Laundry Partner",
    "ui_year":         "2026",
    "ui_location":     "Sta. Rosa, Nueva Ecija",
    # Login page
    "login_h1":        "Fresher.",
    "login_h2":        "Faster.",
    "login_h3":        "Better.",
    "login_welcome":   "Welcome back",
    "login_sub":       "Sign in — your role is detected automatically",
    "login_f1":        "Real-time Laundry Tracking",
    "login_f2":        "Sales & Expense Reports",
    "login_f3":        "Customer Management",
    "login_f4":        "Fast Service Processing",
    # Ticker
    "ui_ticker_1": "Fast Pickup",
    "ui_ticker_2": "Laundry Tracking",
    "ui_ticker_3": "Smooth Service",
    "ui_ticker_4": "Same-Day Service",
    "ui_ticker_5": "Fresh Every Time",
    "ui_ticker_6": "Laundry Lounge",
    # Customer portal
    "cu_sec_dashboard": "1", "cu_sec_status": "1", "cu_sec_history": "1",
    "cu_sec_feedback": "1",  "cu_sec_profile": "1", "cu_notif": "1",
    "cu_title":    "Welcome Back",
    "cu_greeting": "Here's your laundry status at a glance.",
    "cu_noorder":  "No active service right now.",
    "cu_ticker":   "Laundry Lounge · Monitor Your Laundry Live · Wash · Dry · Fold",
    # Operator panel
    "op_sec_dashboard": "1", "op_sec_machines": "1", "op_sec_queue":   "1",
    "op_sec_encode":    "1", "op_sec_folding":  "1", "op_sec_pickup":  "1",
    "op_sec_promos":    "1", "op_sec_issues":   "1",
    "op_title":   "Machine Operator",
    "op_eyebrow": "Operator Portal · 2026",
    "op_badge":   "Operator Access",
    "op_logout":  "Logout",
    # Admin panel
    "adm_sec_dashboard": "1", "adm_sec_analytics": "1", "adm_sec_revenue": "1",
    "adm_sec_orders":    "1", "adm_sec_staff":     "1", "adm_sec_customers": "1",
    "adm_sec_archives":  "1", "adm_sec_feedback":  "1", "adm_sec_reports":   "1",
    "adm_title":    "Admin Panel",
    "adm_eyebrow":  "Admin Panel · 2026",
    "adm_badge":    "Admin Access",
    "adm_greeting": "Welcome back. Here's today's operational overview.",
    # Permissions
    "perm_admin": json.dumps({
        "analytics": True, "revenue": True, "export": True,
        "staff-manage": True, "staff-edit": True, "staff-archive": True,
        "cust-view": True, "cust-block": True, "cust-delete": True,
        "promos": True, "pricing": True,
        "settings-view": True, "settings-edit": False, "feedback": True,
    }),
    "perm_staff": json.dumps({
        "encode": True, "assign": True, "fold": True, "complete": True,
        "machines-view": True, "machines-toggle": True,
        "promos-view": True, "promos-apply": True,
        "issues": True, "email": True,
    }),
    "perm_customer": json.dumps({
        "status": True, "history": True, "receipts": True,
        "feedback": True, "profile": True, "changepass": True, "register": True,
    }),
}


def get_ui_settings() -> dict:
    rows = query(
        "SELECT setting_key, setting_value FROM system_settings") or []
    db_vals = {r["setting_key"]: r["setting_value"] for r in rows}
    merged = dict(UI_DEFAULTS)
    merged.update(db_vals)
    return merged


# ════════════════════════════════════════════════════════════════
#  BACKGROUND SCHEDULER
# ════════════════════════════════════════════════════════════════

_stage_lock = threading.Lock()


def _run_advance_stages():
    with _stage_lock:
        try:
            with app.app_context():
                _advance_stages_logic()
        except Exception as exc:
            try:
                app.logger.error(f"[scheduler] advance_stages error: {exc}")
            except Exception:
                pass


def _start_scheduler():
    def _loop():
        import time
        while True:
            time.sleep(10)
            _run_advance_stages()

    t = threading.Thread(target=_loop, daemon=True, name="stage-scheduler")
    t.start()
    try:
        app.logger.info(
            "[scheduler] Stage-advancement scheduler started (every 10s).")
    except Exception:
        pass


_start_scheduler()


# ════════════════════════════════════════════════════════════════
#  DATABASE HELPERS
# ════════════════════════════════════════════════════════════════

def query(sql, args=(), one=False, commit=False):
    cur = mysql.connection.cursor()
    try:
        cur.execute(sql, args)
        if commit:
            mysql.connection.commit()
            return cur.lastrowid
        return cur.fetchone() if one else cur.fetchall()
    finally:
        cur.close()


def serialize(obj):
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} not serializable")


def jresp(data, status=200):
    return app.response_class(
        json.dumps(data, default=serialize),
        status=status,
        mimetype="application/json"
    )


# ════════════════════════════════════════════════════════════════
#  DATABASE INIT + MIGRATE
# ════════════════════════════════════════════════════════════════

@app.route("/api/db/init", methods=["GET", "POST"])
def db_init():
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id       INT AUTO_INCREMENT PRIMARY KEY,
            full_name     VARCHAR(120) NOT NULL,
            username      VARCHAR(60)  UNIQUE,
            email         VARCHAR(120) NOT NULL UNIQUE,
            phone         VARCHAR(30),
            password_hash VARCHAR(256) NOT NULL,
            role          ENUM('superadmin','admin','staff','customer') NOT NULL DEFAULT 'customer',
            status        ENUM('active','inactive','blocked') NOT NULL DEFAULT 'active',
            is_archived   TINYINT(1) NOT NULL DEFAULT 0,
            archived_at   DATETIME DEFAULT NULL,
            created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS machines (
            machine_id       INT AUTO_INCREMENT PRIMARY KEY,
            unit_number      INT NOT NULL UNIQUE,
            status           ENUM('free','busy','idle','maintenance') NOT NULL DEFAULT 'free',
            current_order_id INT,
            current_stage    VARCHAR(20),
            stage_ends_at    DATETIME,
            updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS services (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            service_key  VARCHAR(40) NOT NULL UNIQUE,
            name         VARCHAR(80) NOT NULL,
            price        DECIMAL(8,2) NOT NULL DEFAULT 0.00,
            is_active    TINYINT(1) NOT NULL DEFAULT 1
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS promos (
            promo_id   INT AUTO_INCREMENT PRIMARY KEY,
            code       VARCHAR(30) NOT NULL UNIQUE,
            discount   INT NOT NULL DEFAULT 0,
            is_active  TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS orders (
            order_id              INT AUTO_INCREMENT PRIMARY KEY,
            tracking_id           VARCHAR(30) NOT NULL UNIQUE,
            customer_id           INT,
            customer_name_walk_in VARCHAR(120),
            customer_email        VARCHAR(120) DEFAULT NULL,
            email_sent            TINYINT(1) NOT NULL DEFAULT 0,
            service_type          VARCHAR(40) NOT NULL,
            weight_kg             DECIMAL(6,2) NOT NULL DEFAULT 0,
            with_dryer            TINYINT(1) NOT NULL DEFAULT 0,
            with_downy            TINYINT(1) NOT NULL DEFAULT 0,
            amount                DECIMAL(10,2) NOT NULL DEFAULT 0,
            machines_needed       INT NOT NULL DEFAULT 1,
            promo_code            VARCHAR(30),
            discount_pct          DECIMAL(5,2) DEFAULT 0,
            status                VARCHAR(20) NOT NULL DEFAULT 'pending',
            stage_ends_at         DATETIME,
            fold_ends_at          DATETIME,
            started_at            DATETIME,
            completed_at          DATETIME,
            encoded_by            INT,
            created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES users(user_id) ON DELETE SET NULL,
            FOREIGN KEY (encoded_by)  REFERENCES users(user_id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS order_machines (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            order_id   INT NOT NULL,
            machine_id INT NOT NULL,
            UNIQUE KEY uq_om (order_id, machine_id),
            FOREIGN KEY (order_id)   REFERENCES orders(order_id)     ON DELETE CASCADE,
            FOREIGN KEY (machine_id) REFERENCES machines(machine_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS feedbacks (
            feedback_id INT AUTO_INCREMENT PRIMARY KEY,
            order_id    INT NOT NULL,
            customer_id INT NOT NULL,
            rating      TINYINT NOT NULL DEFAULT 5,
            comment     TEXT,
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_feedback (order_id, customer_id),
            FOREIGN KEY (order_id)    REFERENCES orders(order_id) ON DELETE CASCADE,
            FOREIGN KEY (customer_id) REFERENCES users(user_id)   ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS audit_logs (
            log_id     INT AUTO_INCREMENT PRIMARY KEY,
            actor      VARCHAR(120),
            action     VARCHAR(80),
            target     VARCHAR(200),
            ip_address VARCHAR(60),
            timestamp  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        # issues table — reporter_name stored for admin display without JOIN
        """CREATE TABLE IF NOT EXISTS issues (
            issue_id      INT AUTO_INCREMENT PRIMARY KEY,
            issue_type    VARCHAR(40) NOT NULL DEFAULT 'other',
            order_id      INT,
            description   TEXT,
            reported_by   INT,
            reporter_name VARCHAR(120),
            status        ENUM('open','resolved') NOT NULL DEFAULT 'open',
            reported_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reported_by) REFERENCES users(user_id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS system_settings (
            setting_key   VARCHAR(60) PRIMARY KEY,
            setting_value TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS backups (
            backup_id  INT AUTO_INCREMENT PRIMARY KEY,
            created_by VARCHAR(120),
            note       TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS password_resets (
            user_id    INT PRIMARY KEY,
            token      VARCHAR(80) NOT NULL,
            expires_at DATETIME NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]

    for stmt in statements:
        try:
            query(stmt, commit=True)
        except Exception as e:
            return jresp({"error": str(e)}, 500)

    # Seed machines 1-8
    for n in range(1, 9):
        query(
            "INSERT IGNORE INTO machines (unit_number, status) VALUES (%s,'free')",
            (n,), commit=True
        )

    # Seed services
    for key, val in SERVICE_RATES.items():
        query(
            "INSERT IGNORE INTO services (service_key, name, price) VALUES (%s,%s,%s)",
            (key, val["label"], val["rate"]), commit=True
        )

    # Seed core system settings
    for k, v in [("maintenance_mode", "0"), ("allow_registration", "1"), ("promos_enabled", "1")]:
        query(
            "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s,%s)",
            (k, v), commit=True
        )

    # Seed UI defaults
    for _k, _v in UI_DEFAULTS.items():
        query(
            "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s,%s)",
            (_k, _v), commit=True
        )

    # Seed superadmin
    SA_EMAIL = os.getenv("SA_EMAIL",    "superadmin@laundry.com")
    SA_PASSWORD = os.getenv("SA_PASSWORD", "StrongPass#2026!")
    SA_NAME = "Super Admin"
    if not query("SELECT user_id FROM users WHERE email=%s", (SA_EMAIL,), one=True):
        query(
            "INSERT INTO users (full_name, email, password_hash, role, status) "
            "VALUES (%s,%s,%s,'superadmin','active')",
            (SA_NAME, SA_EMAIL, generate_password_hash(SA_PASSWORD)), commit=True
        )

    return jresp({"ok": True, "message": "Database initialised."})


@app.route("/api/db/migrate", methods=["POST", "GET"])
def db_migrate():
    migrations = [
        """ALTER TABLE orders
           ADD COLUMN IF NOT EXISTS customer_email VARCHAR(120) DEFAULT NULL
           AFTER customer_name_walk_in""",
        """ALTER TABLE orders
           ADD COLUMN IF NOT EXISTS email_sent TINYINT(1) NOT NULL DEFAULT 0
           AFTER customer_email""",
        """ALTER TABLE orders
           ADD COLUMN IF NOT EXISTS fold_ends_at DATETIME DEFAULT NULL
           AFTER stage_ends_at""",
        """ALTER TABLE machines
           MODIFY COLUMN status
           ENUM('free','busy','idle','maintenance') NOT NULL DEFAULT 'free'""",
        """ALTER TABLE users
           ADD COLUMN IF NOT EXISTS is_archived TINYINT(1) NOT NULL DEFAULT 0
           AFTER status""",
        """ALTER TABLE users
           ADD COLUMN IF NOT EXISTS archived_at DATETIME DEFAULT NULL
           AFTER is_archived""",
        # NEW: reporter_name column for admin display
        """ALTER TABLE issues
           ADD COLUMN IF NOT EXISTS reporter_name VARCHAR(120) DEFAULT NULL
           AFTER reported_by""",
    ]

    results = []
    for sql in migrations:
        try:
            query(sql, commit=True)
            results.append({"sql": sql[:60] + "...", "ok": True})
        except Exception as e:
            results.append({"sql": sql[:60] + "...", "error": str(e)})

    return jresp({"ok": True, "results": results})


# ════════════════════════════════════════════════════════════════
#  EMAIL HELPERS
# ════════════════════════════════════════════════════════════════

def _send_tracking_email_async(app_ctx, order_id, tracking_id,
                               customer_email, customer_name,
                               service_label, amount, status_label):
    with app_ctx:
        try:
            track_url = f"{TRACKING_BASE_URL}/track/{tracking_id}"
            subject = "🧺 Laundry Service Received — Track Your Order"
            body = f"""Hello {customer_name or 'Valued Customer'},

Your laundry has been successfully received and is being processed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TRACKING CODE:   {tracking_id}
  SERVICE:         {service_label}
  AMOUNT:          ₱{amount:,.2f}
  STATUS:          {status_label}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Track your laundry in real-time:
{track_url}

No login required — just click the link above.

Thank you for choosing Laundry Lounge.

— The Laundry Lounge Team
"""
            html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;color:#1a1a2e">
  <div style="background:#1A5DAA;padding:24px 28px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;font-size:1.4rem;margin:0">🧺 Laundry Lounge</h1>
    <p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:.85rem">Order Received — Tracking Confirmation</p>
  </div>
  <div style="background:#fff;padding:28px;border:1px solid #e0e0e0;border-top:none">
    <p style="margin:0 0 18px">Hello <strong>{customer_name or 'Valued Customer'}</strong>,</p>
    <p style="margin:0 0 20px;color:#444">Your laundry has been received and is now being processed.</p>
    <div style="background:#F5F0E8;border:1.5px solid #1A5DAA;border-radius:10px;padding:18px;margin-bottom:22px">
      <table style="width:100%;border-collapse:collapse;font-size:.9rem">
        <tr><td style="color:#666;padding:4px 0">Tracking Code</td>
            <td style="font-weight:700;color:#1A5DAA;text-align:right">{tracking_id}</td></tr>
        <tr><td style="color:#666;padding:4px 0">Service</td>
            <td style="font-weight:600;text-align:right">{service_label}</td></tr>
        <tr><td style="color:#666;padding:4px 0">Amount</td>
            <td style="font-weight:600;text-align:right">₱{amount:,.2f}</td></tr>
        <tr><td style="color:#666;padding:4px 0">Status</td>
            <td style="text-align:right">{status_label}</td></tr>
      </table>
    </div>
    <div style="text-align:center;margin:24px 0">
      <a href="{track_url}" style="background:#1A5DAA;color:#fff;padding:13px 32px;border-radius:30px;text-decoration:none;font-weight:600">
        📦 Track My Laundry
      </a>
    </div>
  </div>
  <div style="background:#EDE5D8;padding:14px 28px;border-radius:0 0 12px 12px;text-align:center">
    <p style="color:#888;font-size:.75rem;margin:0">Laundry Lounge — Your Local Laundry Partner</p>
  </div>
</div>
"""
            msg = MailMessage(subject=subject, recipients=[customer_email],
                              body=body, html=html_body)
            mail.send(msg)
            query("UPDATE orders SET email_sent=1 WHERE tracking_id=%s",
                  (tracking_id,), commit=True)
        except Exception as e:
            app.logger.error(
                f"Failed to send tracking email to {customer_email}: {e}")


def send_tracking_email(order_id, tracking_id, customer_email,
                        customer_name, service_label, amount,
                        status_label="⏳ Pending"):
    if not customer_email:
        return
    ctx = app.app_context()
    t = threading.Thread(
        target=_send_tracking_email_async,
        args=(ctx, order_id, tracking_id, customer_email,
              customer_name, service_label, amount, status_label),
        daemon=True
    )
    t.start()


def send_status_update_email(tracking_id, customer_email, customer_name,
                             old_status, new_status):
    if not customer_email:
        return
    if new_status not in {"ready_for_pickup"}:
        return

    track_url = f"{TRACKING_BASE_URL}/track/{tracking_id}"
    info = {
        "ready_for_pickup": {
            "subject":  "📦 Your Laundry is Ready for Pickup!",
            "headline": "Your laundry is ready! 🎉",
            "body":     "Your laundry has been washed, dried, and folded. Please come pick it up.",
            "cta":      "View Order Details",
        }
    }.get(new_status)
    if not info:
        return

    def _send():
        with app.app_context():
            try:
                html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;color:#1a1a2e">
  <div style="background:#1B7A4A;padding:24px 28px;border-radius:12px 12px 0 0">
    <h1 style="color:#fff;font-size:1.4rem;margin:0">🧺 Laundry Lounge</h1>
  </div>
  <div style="background:#fff;padding:28px;border:1px solid #e0e0e0;border-top:none">
    <h2 style="color:#1B7A4A;margin:0 0 16px">{info['headline']}</h2>
    <p style="color:#444">Hello <strong>{customer_name or 'Valued Customer'}</strong>,<br><br>{info['body']}</p>
    <div style="text-align:center;margin-top:24px">
      <a href="{track_url}" style="background:#1B7A4A;color:#fff;padding:12px 28px;border-radius:30px;text-decoration:none;font-weight:600">
        {info['cta']}
      </a>
    </div>
  </div>
</div>"""
                msg = MailMessage(subject=info["subject"],
                                  recipients=[customer_email], html=html_body)
                mail.send(msg)
            except Exception as e:
                app.logger.error(f"Status email error for {tracking_id}: {e}")

    threading.Thread(target=_send, daemon=True).start()


# ── Password reset email ───────────────────────────────────────

def _send_reset_email_async(app_ctx, email, token, full_name):
    with app_ctx:
        try:
            reset_url = f"{TRACKING_BASE_URL}/reset-password?token={token}"
            subject = "🔑 Laundry Lounge — Password Reset Request"
            plain_body = f"""Hello {full_name or 'Valued User'},

We received a request to reset the password for your Laundry Lounge account.

Click the link below to set a new password. This link expires in 1 hour.

{reset_url}

If you did not request a password reset, please ignore this email.

— The Laundry Lounge Team
"""
            html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reset Your Password — Laundry Lounge</title>
</head>
<body style="margin:0;padding:0;background:#dff6fc;font-family:'DM Sans',Arial,sans-serif">

<div style="max-width:540px;margin:40px auto;padding:0 16px 40px">

  <!-- Header -->
  <div style="background:#08202c;border-radius:16px 16px 0 0;padding:28px 32px;text-align:center;border-bottom:2px solid #00a8cc">
    <p style="margin:0 0 4px;font-family:'Courier New',monospace;font-size:.52rem;letter-spacing:.3em;text-transform:uppercase;color:#00a8cc">
      ◈ Laundry Lounge
    </p>
    <h1 style="margin:0;font-size:1.8rem;font-weight:700;color:#c8f0fa;letter-spacing:-.02em;line-height:1">
      Password <span style="font-style:italic;font-weight:300;color:#00a8cc">Reset</span>
    </h1>
    <p style="margin:8px 0 0;font-family:'Courier New',monospace;font-size:.55rem;letter-spacing:.18em;text-transform:uppercase;color:rgba(200,240,250,.4)">
      Maharlika Hwy · Sta. Rosa · Nueva Ecija
    </p>
  </div>

  <!-- Body -->
  <div style="background:rgba(252,254,255,.97);border:1px solid rgba(0,160,200,.14);border-top:none;border-radius:0 0 16px 16px;padding:36px 32px 28px">

    <!-- Greeting -->
    <p style="margin:0 0 24px;font-size:.95rem;color:rgba(20,80,100,.7);line-height:1.75;text-align:center;font-weight:300">
      Hello, <strong style="color:#082530;font-weight:600">{full_name or 'there'}</strong> —<br>
      We received a request to reset your Laundry Lounge password.<br>
      Click the button below to proceed.
    </p>

    <!-- Divider -->
    <div style="height:1px;background:linear-gradient(90deg,transparent,rgba(0,168,204,.3),transparent);margin:0 0 28px"></div>

    <!-- CTA Button -->
    <div style="text-align:center;margin:0 0 28px">
      <a href="{reset_url}"
         style="display:inline-flex;align-items:center;gap:10px;background:#00a8cc;color:#fff;
                padding:15px 36px;border-radius:50px;text-decoration:none;
                font-family:'Courier New',monospace;font-size:.7rem;letter-spacing:.18em;
                text-transform:uppercase;font-weight:600;
                box-shadow:0 8px 28px rgba(0,168,204,.38)">
        🔒 Reset My Password &nbsp;→
      </a>
    </div>

    <!-- URL fallback -->
    <div style="background:rgba(0,168,204,.06);border:1px solid rgba(0,168,204,.18);border-radius:10px;padding:12px 16px;margin:0 0 24px;text-align:center">
      <p style="margin:0 0 5px;font-family:'Courier New',monospace;font-size:.52rem;letter-spacing:.2em;text-transform:uppercase;color:rgba(0,168,204,.7)">
        Or copy this link
      </p>
      <p style="margin:0;font-family:'Courier New',monospace;font-size:.65rem;color:#00a8cc;word-break:break-all;line-height:1.5">
        {reset_url}
      </p>
    </div>

    <!-- Expiry notice -->
    <div style="background:rgba(138,96,16,.07);border:1px solid rgba(138,96,16,.22);border-radius:10px;padding:11px 16px;margin:0 0 24px;display:flex;align-items:flex-start;gap:10px">
      <span style="font-size:.9rem;flex-shrink:0">⏱</span>
      <p style="margin:0;font-family:'Courier New',monospace;font-size:.58rem;letter-spacing:.06em;color:#8a6010;line-height:1.6">
        This link expires in <strong>1 hour</strong>. If you didn't request a password reset, you can safely ignore this email — your account remains secure.
      </p>
    </div>

    <!-- Divider -->
    <div style="height:1px;background:rgba(0,160,196,.09);margin:0 0 20px"></div>

    <!-- Footer note -->
    <p style="margin:0;font-family:'Courier New',monospace;font-size:.5rem;letter-spacing:.14em;text-transform:uppercase;color:rgba(20,80,100,.35);text-align:center;line-height:1.8">
      Laundry Lounge Management System · Est. 2026<br>
      Maharlika Hwy · Cojuangco · Sta. Rosa · Nueva Ecija
    </p>

  </div>

  <!-- Email bottom tag -->
  <p style="text-align:center;margin:14px 0 0;font-family:'Courier New',monospace;font-size:.48rem;letter-spacing:.15em;text-transform:uppercase;color:rgba(8,37,48,.35)">
    You're receiving this because a reset was requested for this address.
  </p>

</div>
</body>
</html>"""
            msg = MailMessage(subject=subject, recipients=[email],
                              body=plain_body, html=html_body)
            mail.send(msg)
        except Exception as exc:
            app.logger.error(f"Failed to send reset email to {email}: {exc}")


def send_reset_email(email, token, full_name):
    ctx = app.app_context()
    t = threading.Thread(
        target=_send_reset_email_async,
        args=(ctx, email, token, full_name),
        daemon=True,
    )
    t.start()


# ════════════════════════════════════════════════════════════════
#  CORE HELPERS
# ════════════════════════════════════════════════════════════════

def log_audit(actor, action, target="", ip=""):
    try:
        query(
            "INSERT INTO audit_logs (actor, action, target, ip_address, timestamp) "
            "VALUES (%s,%s,%s,%s,NOW())",
            (actor, action, target, ip), commit=True
        )
    except Exception:
        pass


def generate_tracking_id():
    today = datetime.now().strftime("%Y%m%d")
    for _ in range(5):
        suffix = secrets.token_hex(4).upper()
        candidate = f"TRK-{today}-{suffix}"
        if not query("SELECT order_id FROM orders WHERE tracking_id=%s",
                     (candidate,), one=True):
            return candidate
    raise RuntimeError("Failed to generate unique tracking ID")


def calc_amount(service_type: str, weight_kg: float,
                with_dryer: bool = False, discount_pct: float = 0) -> float:
    rate = SERVICE_RATES.get(service_type, {}).get("rate", 0)
    base = rate * weight_kg
    disc = base * discount_pct / 100
    dryer = DRYER_SURCHARGE if with_dryer else 0.0
    return round((base - disc) + dryer, 2)


def machines_needed(weight_kg: float) -> int:
    return max(1, math.ceil(weight_kg / MACHINE_CAPACITY))


def redirect_by_role(role):
    mapping = {
        "superadmin": "/superadmin",
        "admin":      "/admin",
        "staff":      "/staff",
        "customer":   "/customer",
    }
    return redirect(mapping.get(role, url_for("login")))


def get_system_setting(key: str, default="0"):
    row = query(
        "SELECT setting_value FROM system_settings WHERE setting_key=%s",
        (key,), one=True
    )
    return row["setting_value"] if row else default


def is_maintenance_mode() -> bool:
    return get_system_setting("maintenance_mode", "0") == "1"


# ════════════════════════════════════════════════════════════════
#  AUTH DECORATORS
# ════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jresp({"error": "Not authenticated"}, 401)
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrap(*a, **kw):
            if "user_id" not in session:
                if request.is_json or request.path.startswith("/api/"):
                    return jresp({"error": "Not authenticated"}, 401)
                return redirect(url_for("login"))
            if session.get("role") not in roles:
                if request.is_json or request.path.startswith("/api/"):
                    return jresp({"error": "Forbidden"}, 403)
                flash("Access denied.", "error")
                return redirect(url_for("login"))
            return f(*a, **kw)
        return wrap
    return decorator


def _require_json_or_xhr(f):
    @wraps(f)
    def wrap(*a, **kw):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if (not request.is_json
                    and request.headers.get("X-Requested-With") != "XMLHttpRequest"):
                return jresp({"error": "Invalid request"}, 400)
        return f(*a, **kw)
    return wrap


# ════════════════════════════════════════════════════════════════
#  FORCE-LOGOUT MIDDLEWARE
# ════════════════════════════════════════════════════════════════

@app.before_request
def check_force_logout():
    if "user_id" not in session:
        return
    if session.get("role") == "superadmin":
        return
    if request.endpoint in (
        "login", "login_post", "logout",
        "register", "forgot_password",
        "api_forgot_password", "api_reset_password",
        "reset_password_redirect",
        "public_track_page", "public_track_by_name",
        "api_public_track", "api_maintenance_status",
        "api_machines_status_public", "api_ui_settings_public",
        "db_init", "db_migrate", "static",
    ):
        return

    db_ts_row = query(
        "SELECT setting_value FROM system_settings WHERE setting_key='force_logout_ts'",
        one=True
    )
    if not db_ts_row:
        return
    try:
        db_ts = float(db_ts_row["setting_value"])
    except (ValueError, TypeError):
        return

    session_login_ts = session.get("login_ts", 0)
    if db_ts > session_login_ts:
        session.clear()
        if request.is_json or request.path.startswith("/api/"):
            return jresp({"error": "Session expired. Please log in again.", "force_logout": True}, 401)
        flash("Your session has expired. Please log in again.", "error")
        return redirect(url_for("login"))


# ════════════════════════════════════════════════════════════════
#  PUBLIC — MAINTENANCE + MACHINES + TRACKING
# ════════════════════════════════════════════════════════════════

@app.route("/api/system/maintenance-status")
def api_maintenance_status():
    return jresp({"maintenance": is_maintenance_mode()})


@app.route("/api/machines/status")
def api_machines_status_public():
    """No-auth — used by login page machine status bar."""
    try:
        rows = query("SELECT status FROM machines") or []
        free = sum(1 for m in rows if m["status"] == "free")
        busy = sum(1 for m in rows if m["status"] == "busy")
        idle = sum(1 for m in rows if m["status"] == "idle")
        maintenance = sum(1 for m in rows if m["status"] == "maintenance")
        return jresp({"free": free, "busy": busy, "idle": idle,
                      "maintenance": maintenance, "total": len(rows)})
    except Exception:
        return jresp({"free": 0, "busy": 0, "idle": 0, "maintenance": 0, "total": 0})


@app.route("/api/ui/settings")
def api_ui_settings_public():
    """No-auth — consumed by login/customer/operator/admin HTML pages."""
    all_settings = get_ui_settings()
    SAFE_PREFIXES = ("ui_", "login_", "cu_", "op_", "adm_", "ticker_")
    EXCLUDED = {
        "perm_admin", "perm_staff", "perm_customer",
        "maintenance_mode", "force_logout_ts",
        "allow_registration", "promos_enabled",
        "opening_time", "closing_time",
    }
    public = {
        k: v for k, v in all_settings.items()
        if any(k.startswith(p) for p in SAFE_PREFIXES) and k not in EXCLUDED
    }
    return jresp(public)


# ════════════════════════════════════════════════════════════════
#  PUBLIC TRACKING PAGES
# ════════════════════════════════════════════════════════════════

def _build_tracking_html(order, tracking_id):
    """Shared HTML builder for both tracking routes."""
    SERVICE_LABELS = {
        "single_wash": "Single Wash", "double_wash": "Double Wash",
        "household": "Household Items", "heavy_wash": "Heavy Wash (Comforter)",
        "soak_whites": "Soak for Whites",
    }
    STATUS_DISPLAY = {
        "pending":          {"label": "⏳ Pending",           "color": "#A06010", "bg": "#FDF3E3"},
        "washing":          {"label": "🫧 Washing",           "color": "#1A5DAA", "bg": "#EEF3FB"},
        "drying":           {"label": "💨 Drying",            "color": "#1A8080", "bg": "#E8F5F5"},
        "downy":            {"label": "🌸 Downy",             "color": "#6A35A0", "bg": "#F3EEF8"},
        "folding":          {"label": "👕 Folding",           "color": "#A06010", "bg": "#FDF3E3"},
        "ready_for_pickup": {"label": "📦 Ready for Pickup!", "color": "#B85000", "bg": "#FEF0E6"},
        "completed":        {"label": "✅ Completed",         "color": "#1B7A4A", "bg": "#EBF5EE"},
        "done":             {"label": "✅ Completed",         "color": "#1B7A4A", "bg": "#EBF5EE"},
        "cancelled":        {"label": "❌ Cancelled",         "color": "#C0392B", "bg": "#FDEDED"},
    }

    s = order.get("status", "pending")
    disp = STATUS_DISPLAY.get(s, {"label": s, "color": "#666", "bg": "#eee"})
    svc_lbl = SERVICE_LABELS.get(
        order.get("service_type", ""), order.get("service_type", ""))
    created = order.get("created_at", "")
    if hasattr(created, "strftime"):
        created = created.strftime("%b %d, %Y %I:%M %p")

    stages = ["pending", "washing"]
    if order.get("with_dryer"):
        stages.append("drying")
    if order.get("with_downy"):
        stages.append("downy")
    stages += ["folding", "ready_for_pickup", "completed"]
    cur_idx = stages.index(s) if s in stages else 0

    stage_labels = {
        "pending": "Received", "washing": "Washing", "drying": "Drying",
        "downy": "Downy", "folding": "Folding",
        "ready_for_pickup": "Ready!", "completed": "Done",
    }
    stage_html = "".join(
        f"""<div style="display:flex;flex-direction:column;align-items:center;gap:4px">
          <div style="width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;
                      background:{'#1B7A4A' if i < cur_idx else (
            '#1A5DAA' if i == cur_idx else '#ddd')};
                      color:{'#fff' if i <= cur_idx else '#999'};font-size:.9rem">
            {'✓' if i < cur_idx else ('●' if i == cur_idx else '○')}
          </div>
          <div style="font-size:.6rem;color:{'#1A5DAA' if i == cur_idx else ('#1B7A4A' if i < cur_idx else '#999')};text-align:center;max-width:50px">
            {stage_labels.get(stages[i], stages[i])}
          </div>
        </div>{'<div style="width:20px;height:2px;background:' + ('#1B7A4A' if i < cur_idx else '#ddd') + ';margin-bottom:14px"></div>' if i < len(stages)-1 else ''}"""
        for i in range(len(stages))
    )

    auto_refresh = s not in ("completed", "done", "cancelled")

    live_badge = (
        '<div class="live-section">'
        '<div class="auto-refresh-badge">'
        '<div class="arb-dot"></div>'
        '<span class="arb-text">This page refreshes automatically every 60 seconds</span>'
        '</div>'
        '</div>'
    ) if auto_refresh else (
        '<div class="live-section">'
        '<div class="auto-refresh-badge" style="background:var(--success-bg);border-color:var(--success-border)">'
        '<span style="font-size:1rem">✅</span>'
        '<span class="arb-text">Service complete — thank you for choosing Laundry Lounge!</span>'
        '</div>'
        '</div>'
    )
    trk_url = f"/track/{tracking_id}"

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Track Order — {tracking_id}</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;0,700;1,300;1,400;1,600&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ease:cubic-bezier(0.16,1,0.3,1);--ease-back:cubic-bezier(0.34,1.56,0.64,1)}}
[data-theme="light"]{{
  --bg:#dff6fc;--surface:rgba(255,255,255,0.82);--surface2:rgba(252,254,255,0.97);
  --border:rgba(0,160,200,0.14);--border-acc:rgba(0,180,216,0.38);
  --text:#082530;--text2:#1a5c72;--text3:rgba(20,80,100,0.52);
  --accent:#00a8cc;--accent2:#0077a8;--accent-glow:rgba(0,168,204,0.18);
  --danger:#b03020;--danger-bg:rgba(176,48,32,0.07);--danger-border:rgba(176,48,32,0.22);
  --success:#0077a8;--success-bg:rgba(0,119,168,0.08);--success-border:rgba(0,119,168,0.22);
  --warn:#8a6010;--warn-bg:rgba(138,96,16,0.09);--warn-border:rgba(138,96,16,0.25);
  --mark-bg:#08202c;--mark-text:#e0f6fb;
  --shadow:0 24px 64px rgba(0,80,120,0.13),0 4px 20px rgba(0,80,120,0.07);
  --shadow-btn:0 6px 24px rgba(0,168,204,0.38);
  --surface-input:rgba(255,255,255,0.9);--line-color:rgba(0,160,196,0.09);
  --noise-op:0.022;--toggle-bg:rgba(8,37,48,0.07);
  --hero-grad:linear-gradient(135deg,#c8f0f8 0%,#d8f4fc 35%,#b8ecf8 65%,#caf2fc 100%);
}}
[data-theme="dark"]{{
  --bg:#050f14;--surface:rgba(8,22,32,0.9);--surface2:rgba(10,26,38,0.97);
  --border:rgba(0,200,240,0.10);--border-acc:rgba(0,212,245,0.32);
  --text:#c8f0fa;--text2:#74bcd4;--text3:rgba(100,180,210,0.5);
  --accent:#00c8f0;--accent2:#0090c0;--accent-glow:rgba(0,200,240,0.22);
  --danger:#d84040;--danger-bg:rgba(216,64,64,0.10);--danger-border:rgba(216,64,64,0.28);
  --success:#00c8f0;--success-bg:rgba(0,200,240,0.10);--success-border:rgba(0,200,240,0.28);
  --warn:#c89030;--warn-bg:rgba(200,144,48,0.10);--warn-border:rgba(200,144,48,0.28);
  --mark-bg:#00c8f0;--mark-text:#050f14;
  --shadow:0 24px 64px rgba(0,0,0,0.65),0 4px 20px rgba(0,0,0,0.4);
  --shadow-btn:0 6px 28px rgba(0,200,240,0.42);
  --surface-input:rgba(0,0,0,0.35);--line-color:rgba(0,200,240,0.07);
  --noise-op:0.045;--toggle-bg:rgba(200,240,250,0.07);
  --hero-grad:linear-gradient(135deg,#071820 0%,#0a2030 35%,#081c2c 65%,#0c2234 100%);
}}
html{{scroll-behavior:smooth}}
body{{font-family:'DM Sans',sans-serif;background:var(--hero-grad);color:var(--text);min-height:100vh;display:flex;flex-direction:column;overflow-x:hidden;transition:background .4s,color .4s;font-size:1rem;line-height:1.6;position:relative;}}
body::before{{content:'';position:fixed;inset:0;z-index:1001;pointer-events:none;opacity:var(--noise-op);background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}}
.ambient{{pointer-events:none;position:fixed;inset:0;z-index:0;overflow:hidden}}
.amb-orb{{position:absolute;border-radius:50%;filter:blur(80px);opacity:.35;animation:orbDrift ease-in-out infinite alternate}}
[data-theme="dark"] .amb-orb{{opacity:.18}}
.amb-orb.o1{{width:60vw;height:60vw;background:radial-gradient(circle,rgba(0,180,220,.5),transparent 70%);top:-15%;left:-10%;animation-duration:18s}}
.amb-orb.o2{{width:50vw;height:50vw;background:radial-gradient(circle,rgba(0,100,180,.4),transparent 70%);bottom:-10%;right:-5%;animation-duration:22s}}
.amb-orb.o3{{width:35vw;height:35vw;background:radial-gradient(circle,rgba(0,220,255,.35),transparent 70%);top:30%;left:40%;animation-duration:15s}}
#bubblesCanvas{{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}}
.deco-bg{{pointer-events:none;position:fixed;inset:0;overflow:hidden;z-index:0}}
.deco-word{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:clamp(5rem,12vw,13rem);line-height:.82;color:var(--text);opacity:.025;position:absolute;white-space:nowrap;user-select:none;letter-spacing:-.02em}}
.deco-word.w1{{top:-2%;left:-1%;transform:rotate(-2deg)}}
.deco-word.w2{{bottom:-1%;right:-1%;transform:rotate(2deg);font-style:italic}}
@media(max-width:540px){{.deco-word{{display:none}}}}
#themeToggle{{position:fixed;top:16px;right:16px;z-index:600;width:42px;height:42px;border-radius:11px;background:var(--toggle-bg);border:1px solid var(--border);backdrop-filter:blur(16px);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:1.05rem;transition:all .2s;color:var(--text);-webkit-tap-highlight-color:transparent;}}
#themeToggle:hover{{border-color:var(--accent);transform:scale(1.08);box-shadow:0 0 0 3px var(--accent-glow)}}
.top-bar{{position:relative;z-index:200;background:var(--mark-bg);border-bottom:2px solid var(--accent);padding:14px clamp(16px,5vw,36px);display:flex;align-items:center;gap:14px;justify-content:space-between;animation:slideDown .65s var(--ease) both;box-shadow:0 6px 32px rgba(0,0,0,.28);}}
@keyframes slideDown{{from{{opacity:0;transform:translateY(-12px)}}to{{opacity:1;transform:translateY(0)}}}}
.top-bar-brand{{display:flex;align-items:center;gap:12px}}
.top-bar-logo{{width:32px;height:32px;object-fit:contain;filter:drop-shadow(0 2px 8px var(--accent-glow))}}
.top-bar-name{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:1.25rem;color:var(--mark-text);letter-spacing:-.01em}}
.top-bar-name em{{font-style:italic;font-weight:300;color:var(--accent)}}
.top-bar-back{{display:flex;align-items:center;gap:6px;padding:7px 15px;border-radius:9px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);color:rgba(255,255,255,.7);font-family:'DM Mono',monospace;font-size:.58rem;letter-spacing:.18em;text-transform:uppercase;text-decoration:none;transition:all .2s;}}
.top-bar-back:hover{{background:rgba(255,255,255,.14);color:#fff;border-color:var(--accent)}}
.ticker-wrap{{width:100%;background:var(--accent);overflow:hidden;padding:6px 0;position:relative;z-index:100}}
.ticker-inner{{display:flex;white-space:nowrap;animation:ticker 26s linear infinite}}
.ticker-item{{font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.18em;text-transform:uppercase;padding:0 24px;color:rgba(255,255,255,.92)}}
.ticker-dot{{display:inline-block;width:3px;height:3px;background:rgba(255,255,255,.45);border-radius:50%;vertical-align:middle;margin:0 12px;opacity:.6}}
.page-shell{{position:relative;z-index:10;flex:1;display:flex;flex-direction:column;align-items:center;padding:clamp(1.5rem,4vw,3rem) clamp(1rem,4vw,2rem);gap:1.5rem;}}
.trk-header{{width:100%;max-width:820px;display:flex;align-items:flex-start;justify-content:space-between;gap:16px;animation:fadeUp .8s var(--ease) .1s both;flex-wrap:wrap;}}
.trk-eyebrow{{display:inline-flex;align-items:center;gap:8px;padding:5px 13px;border:1px solid var(--border-acc);border-radius:5px;font-family:'DM Mono',monospace;font-size:.56rem;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);background:var(--accent-glow);margin-bottom:8px;}}
.trk-eyebrow-dot{{width:5px;height:5px;background:var(--accent);border-radius:50%;animation:pulse 1.8s ease-in-out infinite}}
.trk-big-id{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:clamp(2rem,4vw,3.2rem);line-height:.9;letter-spacing:-.03em;color:var(--text)}}
.trk-big-id em{{font-style:italic;font-weight:300;color:var(--accent)}}
.trk-meta{{font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.14em;text-transform:uppercase;color:var(--text3);margin-top:6px}}
.trk-status-pill{{display:inline-flex;align-items:center;gap:8px;padding:10px 20px;border-radius:30px;font-family:'DM Mono',monospace;font-size:.65rem;letter-spacing:.16em;text-transform:uppercase;font-weight:500;border:1px solid;animation:fadeUp .8s var(--ease) .2s both;align-self:flex-start;margin-top:4px;}}
.main-card{{width:100%;max-width:820px;background:var(--surface2);border:1px solid var(--border);border-radius:20px;box-shadow:var(--shadow);overflow:hidden;animation:cardEnter .9s var(--ease) .25s both;position:relative;}}
.main-card::before{{content:'';position:absolute;top:-1px;left:-1px;width:40px;height:40px;border-top:2px solid var(--accent);border-left:2px solid var(--accent);border-radius:20px 0 0 0;pointer-events:none;}}
.main-card::after{{content:'';position:absolute;bottom:-1px;right:-1px;width:40px;height:40px;border-bottom:2px solid var(--accent);border-right:2px solid var(--accent);border-radius:0 0 20px 0;pointer-events:none;}}
.card-drag-handle{{display:flex;align-items:center;justify-content:space-between;padding:10px 20px;border-bottom:1px solid var(--line-color);background:var(--surface);}}
.live-tag{{display:inline-flex;align-items:center;gap:7px;padding:4px 12px 4px 9px;background:var(--accent);color:#fff;border-radius:5px;font-family:'DM Mono',monospace;font-size:.52rem;letter-spacing:.16em;text-transform:uppercase;}}
.live-dot{{width:6px;height:6px;background:rgba(255,255,255,.85);border-radius:50%;animation:pulse 1.8s ease-in-out infinite}}
.card-handle-right{{font-family:'DM Mono',monospace;font-size:.52rem;letter-spacing:.14em;text-transform:uppercase;color:var(--text3)}}
.stages-section{{padding:clamp(1.4rem,3vw,2rem) clamp(1.4rem,3vw,2rem) 1rem;border-bottom:1px solid var(--line-color);}}
.stages-label{{font-family:'DM Mono',monospace;font-size:.56rem;letter-spacing:.24em;text-transform:uppercase;color:var(--text3);margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.stages-label::before{{content:'';flex:none;width:20px;height:1px;background:var(--accent);opacity:.5}}
.stages-pipeline{{display:flex;align-items:center;gap:0;overflow-x:auto;padding-bottom:4px;scrollbar-width:none;}}
.stages-pipeline::-webkit-scrollbar{{display:none}}
.stage-item{{display:flex;flex-direction:column;align-items:center;gap:6px;flex-shrink:0;position:relative}}
.stage-icon-wrap{{position:relative;width:44px;height:44px;display:flex;align-items:center;justify-content:center;border-radius:50%;border:2px solid var(--border);background:var(--surface);transition:all .3s;font-size:.9rem;}}
.stage-item.done .stage-icon-wrap{{border-color:var(--accent);background:var(--accent-glow)}}
.stage-item.active .stage-icon-wrap{{border-color:var(--accent);background:var(--accent);box-shadow:0 0 0 4px var(--accent-glow);animation:stagePulse 2s ease-in-out infinite}}
.stage-item.pending .stage-icon-wrap{{opacity:.38}}
.stage-label{{font-family:'DM Mono',monospace;font-size:.46rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text3);text-align:center;max-width:52px;line-height:1.3}}
.stage-item.done .stage-label,.stage-item.active .stage-label{{color:var(--accent)}}
.stage-connector{{width:28px;height:2px;background:var(--border);flex-shrink:0;margin-bottom:20px;}}
.stage-connector.filled{{background:var(--accent)}}
.info-section{{padding:clamp(1.2rem,2.5vw,1.8rem) clamp(1.4rem,3vw,2rem);}}
.info-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:var(--border);}}
.info-cell{{background:var(--surface2);padding:14px 18px;display:flex;flex-direction:column;gap:3px;transition:background .2s;}}
.info-cell:hover{{background:var(--surface)}}
.info-cell-label{{font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.2em;text-transform:uppercase;color:var(--text3)}}
.info-cell-value{{font-size:.92rem;font-weight:600;color:var(--text);line-height:1.3}}
.info-cell-value.accent{{color:var(--accent)}}
.addons-row{{display:flex;gap:10px;flex-wrap:wrap;padding:0 clamp(1.4rem,3vw,2rem) clamp(1.2rem,2.5vw,1.8rem);}}
.addon-chip{{display:flex;align-items:center;gap:7px;padding:8px 14px;border-radius:8px;border:1px solid var(--border);background:var(--surface);font-family:'DM Mono',monospace;font-size:.58rem;letter-spacing:.12em;text-transform:uppercase;color:var(--text3);transition:all .2s;}}
.addon-chip.on{{border-color:var(--accent);background:var(--accent-glow);color:var(--accent)}}
.addon-chip-dot{{width:5px;height:5px;border-radius:50%;background:var(--border)}}
.addon-chip.on .addon-chip-dot{{background:var(--accent);animation:pulse 1.8s ease-in-out infinite}}
.live-section{{padding:0 clamp(1.4rem,3vw,2rem) clamp(1.2rem,2.5vw,1.8rem);}}
.auto-refresh-badge{{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:10px;background:var(--success-bg);border:1px solid var(--success-border);}}
.arb-dot{{width:7px;height:7px;border-radius:50%;background:var(--success);flex-shrink:0;animation:pulse 1.8s ease-in-out infinite}}
.arb-text{{font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.1em;color:var(--success)}}
.card-footer{{display:flex;align-items:center;justify-content:space-between;padding:14px clamp(1.4rem,3vw,2rem);border-top:1px solid var(--line-color);background:var(--surface);flex-wrap:wrap;gap:8px;}}
.card-footer-left{{font-family:'DM Mono',monospace;font-size:.52rem;letter-spacing:.16em;text-transform:uppercase;color:var(--text3)}}
.refresh-link{{display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:9px;background:var(--accent);color:#fff;font-family:'DM Mono',monospace;font-size:.56rem;letter-spacing:.16em;text-transform:uppercase;text-decoration:none;transition:opacity .2s,transform .15s;box-shadow:var(--shadow-btn);}}
.refresh-link:hover{{opacity:.85;transform:translateY(-1px)}}
.wash-anim{{position:relative;width:32px;height:32px;flex-shrink:0}}
.wash-ring{{position:absolute;inset:0;border-radius:50%;border:1px solid var(--border)}}
.wash-arc{{position:absolute;inset:0;border-radius:50%;border:1.5px solid transparent;border-top-color:var(--accent);border-right-color:var(--accent);animation:spin 2.2s linear infinite}}
.wash-arc2{{position:absolute;inset:6px;border-radius:50%;border:1px solid transparent;border-bottom-color:var(--accent2);animation:spin 3.5s linear infinite reverse;opacity:.55}}
.wash-dot{{position:absolute;width:4px;height:4px;background:var(--accent);border-radius:50%;top:50%;left:50%;transform:translate(-50%,-50%)}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(18px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes cardEnter{{from{{opacity:0;transform:translateY(22px) scale(.97)}}to{{opacity:1;transform:translateY(0) scale(1)}}}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.3;transform:scale(.6)}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes ticker{{from{{transform:translateX(0)}}to{{transform:translateX(-50%)}}}}
@keyframes orbDrift{{0%{{transform:translate(0,0) scale(1)}}100%{{transform:translate(30px,-22px) scale(1.07)}}}}
@keyframes stagePulse{{0%,100%{{box-shadow:0 0 0 4px var(--accent-glow)}}50%{{box-shadow:0 0 0 8px var(--accent-glow)}}}}
@media(max-width:600px){{
  .trk-header{{flex-direction:column}}
  .info-grid{{grid-template-columns:1fr 1fr}}
  .card-footer{{flex-direction:column;align-items:flex-start}}
  .stage-icon-wrap{{width:36px;height:36px;font-size:.75rem}}
  .stage-connector{{width:16px}}
  .stage-label{{font-size:.4rem;max-width:40px}}
}}
</style>
</head>
<body>

<canvas id="bubblesCanvas" aria-hidden="true"></canvas>
<div class="ambient" aria-hidden="true">
  <div class="amb-orb o1"></div>
  <div class="amb-orb o2"></div>
  <div class="amb-orb o3"></div>
</div>
<div class="deco-bg" aria-hidden="true">
  <div class="deco-word w1">FRESH</div>
  <div class="deco-word w2">Clean</div>
</div>

<button id="themeToggle" onclick="toggleTheme()" aria-label="Toggle theme">☾</button>

<div class="top-bar">
  <div class="top-bar-brand">
    <img src="/static/img/icon.png" alt="" class="top-bar-logo" onerror="this.style.display='none'">
    <span class="top-bar-name">Laundry <em>Lounge</em></span>
  </div>
  <a href="/" class="top-bar-back">← Back to Home</a>
</div>

<div class="ticker-wrap" aria-hidden="true">
  <div class="ticker-inner">
    <span class="ticker-item">Tracking Your Laundry</span><span class="ticker-dot"></span>
    <span class="ticker-item">Real-Time Updates</span><span class="ticker-dot"></span>
    <span class="ticker-item">Laundry Lounge</span><span class="ticker-dot"></span>
    <span class="ticker-item">Fresh Every Time</span><span class="ticker-dot"></span>
    <span class="ticker-item">Maharlika Hwy · Sta. Rosa · NE</span><span class="ticker-dot"></span>
    <span class="ticker-item">Tracking Your Laundry</span><span class="ticker-dot"></span>
    <span class="ticker-item">Real-Time Updates</span><span class="ticker-dot"></span>
    <span class="ticker-item">Laundry Lounge</span><span class="ticker-dot"></span>
    <span class="ticker-item">Fresh Every Time</span><span class="ticker-dot"></span>
    <span class="ticker-item">Maharlika Hwy · Sta. Rosa · NE</span><span class="ticker-dot"></span>
  </div>
</div>

<div class="page-shell">

  <div class="trk-header">
    <div>
      <div class="trk-eyebrow"><div class="trk-eyebrow-dot"></div>Live Service Tracking</div>
      <h1 class="trk-big-id">Laundry Service <em>#{tracking_id}</em></h1>
      <p class="trk-meta">Customer: {order.get('customer_name', 'Walk-in')} &nbsp;·&nbsp; Created: {created}</p>
    </div>
    <div class="trk-status-pill" style="background:{disp['bg']};color:{disp['color']};border-color:{disp['color']}44">
      <span style="width:7px;height:7px;border-radius:50%;background:{disp['color']};display:inline-block;flex-shrink:0;animation:pulse 1.8s ease-in-out infinite"></span>
      {disp['label']}
    </div>
  </div>

  <div class="main-card">
    <div class="card-drag-handle">
      <div class="live-tag"><div class="live-dot"></div>Live Tracking</div>
      <span class="card-handle-right">TRK · {tracking_id}</span>
    </div>

    <div class="stages-section">
      <div class="stages-label">Service Progress</div>
      <div class="stages-pipeline">{stage_html}</div>
    </div>

    <div class="info-section">
      <div class="info-grid">
        <div class="info-cell">
          <span class="info-cell-label">Service Type</span>
          <span class="info-cell-value">{svc_lbl}</span>
        </div>
        <div class="info-cell">
          <span class="info-cell-label">Weight</span>
          <span class="info-cell-value">{order.get('weight_kg', 0)} kg</span>
        </div>
        <div class="info-cell">
          <span class="info-cell-label">Amount Due</span>
          <span class="info-cell-value accent">₱{float(order.get('amount', 0)):,.2f}</span>
        </div>
        <div class="info-cell">
          <span class="info-cell-label">Customer</span>
          <span class="info-cell-value">{order.get('customer_name', 'Walk-in')}</span>
        </div>
        <div class="info-cell">
          <span class="info-cell-label">Date Placed</span>
          <span class="info-cell-value">{created}</span>
        </div>
        <div class="info-cell">
          <span class="info-cell-label">Tracking ID</span>
          <span class="info-cell-value" style="font-family:'DM Mono',monospace;font-size:.82rem">{tracking_id}</span>
        </div>
      </div>
    </div>

    <div class="addons-row">
      <div class="addon-chip {'on' if order.get('with_dryer') else ''}">
        <div class="addon-chip-dot"></div>
        🌀 Dryer {'Included' if order.get('with_dryer') else 'Not included'}
      </div>
      <div class="addon-chip {'on' if order.get('with_downy') else ''}">
        <div class="addon-chip-dot"></div>
        💧 Downy {'Included' if order.get('with_downy') else 'Not included'}
      </div>
    </div>

    {live_badge}

    <div class="card-footer">
      <div style="display:flex;align-items:center;gap:10px">
        <div class="wash-anim">
          <div class="wash-ring"></div>
          <div class="wash-arc"></div>
          <div class="wash-arc2"></div>
          <div class="wash-dot"></div>
        </div>
        <span class="card-footer-left">Maharlika Hwy · Sta. Rosa · Nueva Ecija · Est. 2026</span>
      </div>
      <a href="{trk_url}" class="refresh-link">↻ Refresh Status</a>
    </div>
  </div>

</div>

{'<script>setTimeout(()=>location.reload(),60000)</script>' if auto_refresh else ''}
<script>
'use strict';
function toggleTheme(){{
  const html=document.documentElement,isDark=html.dataset.theme==='dark';
  html.dataset.theme=isDark?'light':'dark';
  document.getElementById('themeToggle').textContent=isDark?'☾':'☀';
  localStorage.setItem('ll-theme',html.dataset.theme);
  if(typeof restartBubbles==='function')restartBubbles();
}}
(function initTheme(){{
  const saved=localStorage.getItem('ll-theme');
  if(saved){{document.documentElement.dataset.theme=saved;const b=document.getElementById('themeToggle');if(b)b.textContent=saved==='dark'?'☾':'☀';}}
}})();
let _animFrameID=null;
function restartBubbles(){{if(_animFrameID)cancelAnimationFrame(_animFrameID);initBubbles();}}
function initBubbles(){{
  const canvas=document.getElementById('bubblesCanvas');if(!canvas)return;
  const ctx=canvas.getContext('2d');let W,H,bubbles=[];
  function resize(){{W=canvas.width=window.innerWidth;H=canvas.height=window.innerHeight;}}
  resize();window.addEventListener('resize',resize);
  const isDark=()=>document.documentElement.dataset.theme==='dark';
  const LIGHT=['0,180,216','0,119,168','0,212,240','30,144,255','0,150,200','100,200,240','0,100,180'];
  const DARK=['0,200,240','0,150,200','0,100,160'];
  function getColor(){{return isDark()?DARK[Math.floor(Math.random()*DARK.length)]:LIGHT[Math.floor(Math.random()*LIGHT.length)];}}
  function getOp(){{return isDark()?{{min:.05,max:.14}}:{{min:.14,max:.42}};}}
  function getCount(){{return isDark()?28:55;}}
  function makeBubble(fy){{const r=4+Math.random()*28;const op=getOp();return{{x:Math.random()*W,y:fy!==undefined?fy:H+r+Math.random()*200,r,speed:.22+Math.random()*.7,drift:(Math.random()-.5)*.45,wobble:Math.random()*Math.PI*2,wobbleSpeed:.012+Math.random()*.02,opacity:op.min+Math.random()*(op.max-op.min),color:getColor()}};}}
  const count=getCount();for(let i=0;i<count;i++)bubbles.push(makeBubble(Math.random()*H));
  function drawBubble(b){{ctx.save();ctx.globalAlpha=b.opacity;const g=ctx.createRadialGradient(b.x-b.r*.3,b.y-b.r*.3,b.r*.05,b.x,b.y,b.r);g.addColorStop(0,'rgba(255,255,255,0.55)');g.addColorStop(.45,`rgba(${{b.color}},0.07)`);g.addColorStop(1,`rgba(${{b.color}},0.2)`);ctx.beginPath();ctx.arc(b.x,b.y,b.r,0,Math.PI*2);ctx.fillStyle=g;ctx.fill();ctx.strokeStyle=`rgba(${{b.color}},0.28)`;ctx.lineWidth=isDark()?1:1.2;ctx.stroke();ctx.globalAlpha=b.opacity*.6;ctx.beginPath();ctx.arc(b.x-b.r*.28,b.y-b.r*.28,b.r*.22,0,Math.PI*2);ctx.fillStyle='rgba(255,255,255,0.85)';ctx.fill();ctx.restore();}}
  function animate(){{ctx.clearRect(0,0,W,H);const t=getCount();while(bubbles.length<t)bubbles.push(makeBubble());while(bubbles.length>t)bubbles.pop();bubbles.forEach((b,i)=>{{b.wobble+=b.wobbleSpeed;b.x+=b.drift+Math.sin(b.wobble)*.4;b.y-=b.speed;if(b.y+b.r<-10)bubbles[i]=makeBubble();drawBubble(b);}});_animFrameID=requestAnimationFrame(animate);}}
  animate();
}}
initBubbles();
</script>
</body>
</html>"""


@app.route("/track/<path:tracking_id>")
def public_track_page(tracking_id):
    order = query(
        """SELECT o.tracking_id, o.status, o.service_type,
                  o.weight_kg, o.amount, o.with_dryer, o.with_downy,
                  o.created_at, o.started_at, o.completed_at,
                  o.stage_ends_at, o.fold_ends_at,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.tracking_id=%s""",
        (tracking_id,), one=True
    )
    if not order:
        return f"""<!DOCTYPE html><html><head><title>Order Not Found</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:sans-serif;text-align:center;padding:60px 20px;background:#F5F0E8}}
h1{{color:#C0392B}}p{{color:#666}}</style></head><body>
<h1>❌ Order Not Found</h1>
<p>Tracking ID <strong>{tracking_id}</strong> was not found.</p>
<p><a href="/">Return Home</a></p></body></html>""", 404

    return _build_tracking_html(order, tracking_id)


@app.route("/track/name/<path:name>")
def public_track_by_name(name):
    """
    Public tracking by customer name (no login required).
    Used by the login page tracking bar when a name is entered instead of a code.
    Returns the most recent active or recent order matching the name.
    """
    name_clean = name.strip()
    if not name_clean:
        return f"""<!DOCTYPE html><html><head><title>No Name Provided</title>
<style>body{{font-family:sans-serif;text-align:center;padding:60px 20px;background:#F5F0E8}}</style></head>
<body><h1>Please enter a customer name.</h1><p><a href="/">Return Home</a></p></body></html>""", 400

    # Search by walk-in name or registered user full_name, most recent first
    like = f"%{name_clean}%"
    orders = query(
        """SELECT o.tracking_id, o.status, o.service_type,
                  o.weight_kg, o.amount, o.with_dryer, o.with_downy,
                  o.created_at, o.started_at, o.completed_at,
                  o.stage_ends_at, o.fold_ends_at,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.customer_name_walk_in LIKE %s
              OR u.full_name LIKE %s
           ORDER BY o.created_at DESC
           LIMIT 20""",
        (like, like)
    ) or []

    if not orders:
        return f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>No Name Provided — Laundry Lounge</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,700;1,300;1,400&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ease:cubic-bezier(0.16,1,0.3,1)}}
[data-theme="light"]{{
  --hero-grad:linear-gradient(135deg,#c8f0f8 0%,#d8f4fc 35%,#b8ecf8 65%,#caf2fc 100%);
  --surface2:rgba(252,254,255,.97);--border:rgba(0,160,200,.14);--border-acc:rgba(0,180,216,.38);
  --text:#082530;--text3:rgba(20,80,100,.52);--accent:#00a8cc;--accent-glow:rgba(0,168,204,.18);
  --warn:#8a6010;--warn-bg:rgba(138,96,16,.09);--warn-border:rgba(138,96,16,.25);
  --shadow:0 24px 64px rgba(0,80,120,.13),0 4px 20px rgba(0,80,120,.07);
  --shadow-btn:0 6px 24px rgba(0,168,204,.38);--line-color:rgba(0,160,196,.09);
  --noise-op:.022;--toggle-bg:rgba(8,37,48,.07);--mark-bg:#08202c;--mark-text:#e0f6fb;
}}
[data-theme="dark"]{{
  --hero-grad:linear-gradient(135deg,#071820 0%,#0a2030 35%,#081c2c 65%,#0c2234 100%);
  --surface2:rgba(10,26,38,.97);--border:rgba(0,200,240,.10);--border-acc:rgba(0,212,245,.32);
  --text:#c8f0fa;--text3:rgba(100,180,210,.5);--accent:#00c8f0;--accent-glow:rgba(0,200,240,.22);
  --warn:#c89030;--warn-bg:rgba(200,144,48,.10);--warn-border:rgba(200,144,48,.28);
  --shadow:0 24px 64px rgba(0,0,0,.65),0 4px 20px rgba(0,0,0,.4);
  --shadow-btn:0 6px 28px rgba(0,200,240,.42);--line-color:rgba(0,200,240,.07);
  --noise-op:.045;--toggle-bg:rgba(200,240,250,.07);--mark-bg:#00c8f0;--mark-text:#050f14;
}}
html{{scroll-behavior:smooth}}
body{{font-family:'DM Sans',sans-serif;background:var(--hero-grad);color:var(--text);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow-x:hidden;transition:background .4s,color .4s;position:relative;padding:2rem 1rem;}}
body::before{{content:'';position:fixed;inset:0;z-index:1;pointer-events:none;opacity:var(--noise-op);background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}}
.amb-orb{{position:fixed;border-radius:50%;filter:blur(80px);opacity:.32;animation:orbDrift ease-in-out infinite alternate;pointer-events:none;}}
[data-theme="dark"] .amb-orb{{opacity:.16}}
.o1{{width:55vw;height:55vw;background:radial-gradient(circle,rgba(0,180,220,.5),transparent 70%);top:-10%;left:-8%;animation-duration:18s}}
.o2{{width:45vw;height:45vw;background:radial-gradient(circle,rgba(0,100,180,.4),transparent 70%);bottom:-8%;right:-4%;animation-duration:22s}}
.deco-word{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:clamp(4rem,12vw,11rem);line-height:.82;color:var(--text);opacity:.025;position:fixed;white-space:nowrap;user-select:none;letter-spacing:-.02em;pointer-events:none;}}
.deco-word.w1{{top:-1%;left:-1%;transform:rotate(-2deg)}}
.deco-word.w2{{bottom:-1%;right:-1%;transform:rotate(2deg);font-style:italic}}
@media(max-width:480px){{.deco-word{{display:none}}}}
#themeToggle{{position:fixed;top:16px;right:16px;z-index:600;width:42px;height:42px;border-radius:11px;background:var(--toggle-bg);border:1px solid var(--border);backdrop-filter:blur(16px);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:1.05rem;transition:all .2s;color:var(--text);}}
#themeToggle:hover{{border-color:var(--accent);transform:scale(1.08);box-shadow:0 0 0 3px var(--accent-glow)}}

.error-card{{position:relative;z-index:10;width:100%;max-width:460px;background:var(--surface2);border:1px solid var(--border);border-radius:20px;box-shadow:var(--shadow);overflow:hidden;animation:cardEnter .9s var(--ease) both;}}
.error-card::before{{content:'';position:absolute;top:-1px;left:-1px;width:36px;height:36px;border-top:2px solid var(--accent);border-left:2px solid var(--accent);border-radius:20px 0 0 0;pointer-events:none;}}
.error-card::after{{content:'';position:absolute;bottom:-1px;right:-1px;width:36px;height:36px;border-bottom:2px solid var(--accent);border-right:2px solid var(--accent);border-radius:0 0 20px 0;pointer-events:none;}}

.card-top{{display:flex;align-items:center;justify-content:space-between;padding:10px 18px;background:var(--mark-bg);border-bottom:2px solid var(--accent);}}
.brand-tag{{display:flex;align-items:center;gap:8px}}
.brand-name{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:1.1rem;color:var(--mark-text);letter-spacing:-.01em}}
.brand-name em{{font-style:italic;font-weight:300;color:var(--accent)}}
.live-tag{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;background:var(--warn-bg);border:1px solid var(--warn-border);border-radius:4px;font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.16em;text-transform:uppercase;color:var(--warn);}}

.card-body{{padding:clamp(1.6rem,4vw,2.2rem) clamp(1.6rem,4vw,2.2rem) clamp(1.2rem,3vw,1.8rem);text-align:center;}}
.error-icon-wrap{{position:relative;width:72px;height:72px;margin:0 auto 20px;display:flex;align-items:center;justify-content:center;}}
.error-icon-ring{{position:absolute;inset:0;border-radius:50%;border:1px solid var(--warn-border);animation:ringPulse 2.5s ease-out infinite}}
.error-icon-ring-2{{position:absolute;inset:-8px;border-radius:50%;border:1px solid var(--warn-border);animation:ringPulse 2.5s ease-out infinite .5s;opacity:.5}}
.error-icon-core{{width:48px;height:48px;border-radius:50%;background:var(--warn-bg);border:1px solid var(--warn-border);display:flex;align-items:center;justify-content:center;font-size:1.3rem;}}
.error-eyebrow{{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border:1px solid var(--warn-border);border-radius:4px;font-family:'DM Mono',monospace;font-size:.54rem;letter-spacing:.22em;text-transform:uppercase;color:var(--warn);background:var(--warn-bg);margin-bottom:12px;}}
.error-eyebrow-dot{{width:4px;height:4px;background:var(--warn);border-radius:50%;animation:pulse 1.8s ease-in-out infinite}}
.error-title{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:clamp(1.8rem,4vw,2.4rem);line-height:.95;letter-spacing:-.02em;color:var(--text);margin-bottom:10px;}}
.error-title em{{font-style:italic;font-weight:300;color:var(--accent)}}
.error-msg{{font-size:.88rem;color:var(--text3);font-weight:300;line-height:1.75;margin-bottom:24px;}}

.hint-box{{background:var(--warn-bg);border:1px solid var(--warn-border);border-radius:10px;padding:12px 16px;margin-bottom:24px;text-align:left;display:flex;align-items:flex-start;gap:10px;}}
.hint-icon{{font-size:.85rem;flex-shrink:0;margin-top:1px}}
.hint-text{{font-family:'DM Mono',monospace;font-size:.6rem;letter-spacing:.06em;color:var(--warn);line-height:1.6;}}

.home-btn{{display:inline-flex;align-items:center;gap:9px;width:100%;padding:14px;background:var(--accent);color:#fff;border-radius:11px;text-decoration:none;font-family:'DM Mono',monospace;font-size:.68rem;letter-spacing:.2em;text-transform:uppercase;font-weight:500;justify-content:center;transition:opacity .2s,transform .15s;box-shadow:var(--shadow-btn);margin-bottom:10px;}}
.home-btn:hover{{opacity:.88;transform:translateY(-2px)}}
.home-btn .btn-arrow{{margin-left:auto;opacity:.7}}

.card-footer{{display:flex;align-items:center;justify-content:space-between;padding:12px 18px;border-top:1px solid var(--line-color);}}
.footer-label{{font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.16em;text-transform:uppercase;color:var(--text3)}}
.wash-anim{{position:relative;width:28px;height:28px;flex-shrink:0}}
.wash-ring{{position:absolute;inset:0;border-radius:50%;border:1px solid var(--border)}}
.wash-arc{{position:absolute;inset:0;border-radius:50%;border:1.5px solid transparent;border-top-color:var(--accent);border-right-color:var(--accent);animation:spin 2.2s linear infinite}}
.wash-arc2{{position:absolute;inset:5px;border-radius:50%;border:1px solid transparent;border-bottom-color:var(--accent);animation:spin 3.5s linear infinite reverse;opacity:.55}}
.wash-dot{{position:absolute;width:4px;height:4px;background:var(--accent);border-radius:50%;top:50%;left:50%;transform:translate(-50%,-50%)}}

@keyframes cardEnter{{from{{opacity:0;transform:translateY(20px) scale(.97)}}to{{opacity:1;transform:translateY(0) scale(1)}}}}
@keyframes orbDrift{{0%{{transform:translate(0,0) scale(1)}}100%{{transform:translate(28px,-20px) scale(1.06)}}}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.3;transform:scale(.6)}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
@keyframes ringPulse{{0%{{opacity:.8;transform:scale(1)}}100%{{opacity:0;transform:scale(1.6)}}}}
</style>
</head>
<body>

<div class="amb-orb o1"></div>
<div class="amb-orb o2"></div>
<div class="deco-word w1">FRESH</div>
<div class="deco-word w2">Clean</div>

<button id="themeToggle" onclick="toggleTheme()" aria-label="Toggle theme">☾</button>

<div class="error-card" role="main">
  <div class="card-top">
    <div class="brand-tag">
      <span class="brand-name">Laundry <em>Lounge</em></span>
    </div>
    <div class="live-tag">⚠ Input Required</div>
  </div>

  <div class="card-body">
    <div class="error-icon-wrap">
      <div class="error-icon-ring"></div>
      <div class="error-icon-ring-2"></div>
      <div class="error-icon-core">👤</div>
    </div>

    <div class="error-eyebrow"><div class="error-eyebrow-dot"></div>Missing Information</div>
    <h1 class="error-title">No name <em>provided</em></h1>
    <p class="error-msg">
      Please enter a customer name to track your laundry order. A name is required to look up your service status.
    </p>

    <div class="hint-box">
      <span class="hint-icon">💡</span>
      <span class="hint-text">Use the name you gave when dropping off your laundry. If you're unsure, ask a staff member for your tracking details.</span>
    </div>

    <a href="/" class="home-btn">
      ← Return to Home
      <span class="btn-arrow">→</span>
    </a>
  </div>

  <div class="card-footer">
    <span class="footer-label">Laundry Lounge · Est. 2026 · Sta. Rosa, NE</span>
    <div class="wash-anim">
      <div class="wash-ring"></div>
      <div class="wash-arc"></div>
      <div class="wash-arc2"></div>
      <div class="wash-dot"></div>
    </div>
  </div>
</div>

<script>
function toggleTheme(){{
  const html=document.documentElement,isDark=html.dataset.theme==='dark';
  html.dataset.theme=isDark?'light':'dark';
  document.getElementById('themeToggle').textContent=isDark?'☾':'☀';
  localStorage.setItem('ll-theme',html.dataset.theme);
}}
(function(){{
  const s=localStorage.getItem('ll-theme');
  if(s){{document.documentElement.dataset.theme=s;const b=document.getElementById('themeToggle');if(b)b.textContent=s==='dark'?'☾':'☀';}}
}})();
</script>
</body>
</html>""", 404

    # If only one order, show it directly
    if len(orders) == 1:
        o = orders[0]
        return _build_tracking_html(o, o["tracking_id"])

    # Multiple orders — show a list page
    STATUS_DISPLAY = {
        "pending":          {"label": "⏳ Pending",           "color": "#A06010", "bg": "#FDF3E3"},
        "washing":          {"label": "🫧 Washing",           "color": "#1A5DAA", "bg": "#EEF3FB"},
        "drying":           {"label": "💨 Drying",            "color": "#1A8080", "bg": "#E8F5F5"},
        "downy":            {"label": "🌸 Downy",             "color": "#6A35A0", "bg": "#F3EEF8"},
        "folding":          {"label": "👕 Folding",           "color": "#A06010", "bg": "#FDF3E3"},
        "ready_for_pickup": {"label": "📦 Ready!",            "color": "#B85000", "bg": "#FEF0E6"},
        "completed":        {"label": "✅ Completed",         "color": "#1B7A4A", "bg": "#EBF5EE"},
        "done":             {"label": "✅ Completed",         "color": "#1B7A4A", "bg": "#EBF5EE"},
        "cancelled":        {"label": "❌ Cancelled",         "color": "#C0392B", "bg": "#FDEDED"},
    }
    SERVICE_LABELS = {
        "single_wash": "Single Wash", "double_wash": "Double Wash",
        "household": "Household Items", "heavy_wash": "Heavy Wash",
        "soak_whites": "Soak for Whites",
    }

    rows_html = ""
    for o in orders:
        s = o.get("status", "pending")
        disp = STATUS_DISPLAY.get(
            s, {"label": s, "color": "#666", "bg": "#eee"})
        svc = SERVICE_LABELS.get(
            o.get("service_type", ""), o.get("service_type", ""))
        dt = o.get("created_at", "")
        if hasattr(dt, "strftime"):
            dt = dt.strftime("%b %d, %Y %I:%M %p")
        rows_html += f"""
<a href="/track/{o['tracking_id']}" style="display:block;padding:16px;border:1px solid #e0e0e0;
   border-radius:12px;margin-bottom:10px;text-decoration:none;color:#1a1a2e;
   transition:box-shadow .2s" onmouseover="this.style.boxShadow='0 4px 16px rgba(0,0,0,.1)'"
   onmouseout="this.style.boxShadow=''">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
    <span style="font-family:'DM Mono',monospace;font-size:.75rem;color:#888">{o['tracking_id']}</span>
    <span style="padding:3px 10px;border-radius:20px;font-size:.75rem;font-weight:600;
                 background:{disp['bg']};color:{disp['color']}">{disp['label']}</span>
  </div>
  <div style="font-weight:600;margin-bottom:3px">{svc} · {float(o.get('weight_kg', 0))} kg</div>
  <div style="font-size:.82rem;color:#888">{dt} · ₱{float(o.get('amount', 0)):,.2f}</div>
</a>"""

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laundry Service for {name_clean} — Laundry Lounge</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,700;1,300;1,400&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--ease:cubic-bezier(0.16,1,0.3,1)}}
[data-theme="light"]{{
  --hero-grad:linear-gradient(135deg,#c8f0f8 0%,#d8f4fc 35%,#b8ecf8 65%,#caf2fc 100%);
  --surface:rgba(255,255,255,0.82);--surface2:rgba(252,254,255,.97);
  --border:rgba(0,160,200,.14);--border-acc:rgba(0,180,216,.38);
  --text:#082530;--text3:rgba(20,80,100,.52);
  --accent:#00a8cc;--accent2:#0077a8;--accent-glow:rgba(0,168,204,.18);
  --success:#0077a8;--success-bg:rgba(0,119,168,.08);--success-border:rgba(0,119,168,.22);
  --shadow:0 24px 64px rgba(0,80,120,.13),0 4px 20px rgba(0,80,120,.07);
  --shadow-btn:0 6px 24px rgba(0,168,204,.38);--shadow-sm:0 4px 16px rgba(0,80,120,.08);
  --line-color:rgba(0,160,196,.09);--noise-op:.022;--toggle-bg:rgba(8,37,48,.07);
  --mark-bg:#08202c;--mark-text:#e0f6fb;
}}
[data-theme="dark"]{{
  --hero-grad:linear-gradient(135deg,#071820 0%,#0a2030 35%,#081c2c 65%,#0c2234 100%);
  --surface:rgba(8,22,32,0.9);--surface2:rgba(10,26,38,.97);
  --border:rgba(0,200,240,.10);--border-acc:rgba(0,212,245,.32);
  --text:#c8f0fa;--text3:rgba(100,180,210,.5);
  --accent:#00c8f0;--accent2:#0090c0;--accent-glow:rgba(0,200,240,.22);
  --success:#00c8f0;--success-bg:rgba(0,200,240,.10);--success-border:rgba(0,200,240,.28);
  --shadow:0 24px 64px rgba(0,0,0,.65),0 4px 20px rgba(0,0,0,.4);
  --shadow-btn:0 6px 28px rgba(0,200,240,.42);--shadow-sm:0 4px 16px rgba(0,0,0,.45);
  --line-color:rgba(0,200,240,.07);--noise-op:.045;--toggle-bg:rgba(200,240,250,.07);
  --mark-bg:#00c8f0;--mark-text:#050f14;
}}
html{{scroll-behavior:smooth}}
body{{font-family:'DM Sans',sans-serif;background:var(--hero-grad);color:var(--text);min-height:100vh;display:flex;flex-direction:column;align-items:center;overflow-x:hidden;transition:background .4s,color .4s;position:relative;padding:2rem 1rem 3rem;}}
body::before{{content:'';position:fixed;inset:0;z-index:1;pointer-events:none;opacity:var(--noise-op);background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}}
.amb-orb{{position:fixed;border-radius:50%;filter:blur(80px);opacity:.32;animation:orbDrift ease-in-out infinite alternate;pointer-events:none;}}
[data-theme="dark"] .amb-orb{{opacity:.16}}
.o1{{width:55vw;height:55vw;background:radial-gradient(circle,rgba(0,180,220,.5),transparent 70%);top:-10%;left:-8%;animation-duration:18s}}
.o2{{width:45vw;height:45vw;background:radial-gradient(circle,rgba(0,100,180,.4),transparent 70%);bottom:-8%;right:-4%;animation-duration:22s}}
.deco-word{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:clamp(4rem,11vw,11rem);line-height:.82;color:var(--text);opacity:.025;position:fixed;white-space:nowrap;user-select:none;letter-spacing:-.02em;pointer-events:none;}}
.deco-word.w1{{top:-1%;left:-1%;transform:rotate(-2deg)}}
.deco-word.w2{{bottom:-1%;right:-1%;transform:rotate(2deg);font-style:italic}}
@media(max-width:480px){{.deco-word{{display:none}}}}
#themeToggle{{position:fixed;top:16px;right:16px;z-index:600;width:42px;height:42px;border-radius:11px;background:var(--toggle-bg);border:1px solid var(--border);backdrop-filter:blur(16px);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:1.05rem;transition:all .2s;color:var(--text);-webkit-tap-highlight-color:transparent;}}
#themeToggle:hover{{border-color:var(--accent);transform:scale(1.08);box-shadow:0 0 0 3px var(--accent-glow)}}

/* ══ CARD ══ */
.main-card{{position:relative;z-index:10;width:100%;max-width:520px;background:var(--surface2);border:1px solid var(--border);border-radius:20px;box-shadow:var(--shadow);overflow:hidden;animation:cardEnter .9s var(--ease) both;margin-top:clamp(1rem,3vw,2rem);}}
.main-card::before{{content:'';position:absolute;top:-1px;left:-1px;width:36px;height:36px;border-top:2px solid var(--accent);border-left:2px solid var(--accent);border-radius:20px 0 0 0;pointer-events:none;}}
.main-card::after{{content:'';position:absolute;bottom:-1px;right:-1px;width:36px;height:36px;border-bottom:2px solid var(--accent);border-right:2px solid var(--accent);border-radius:0 0 20px 0;pointer-events:none;}}

/* ══ CARD TOP BAR ══ */
.card-top{{display:flex;align-items:center;justify-content:space-between;padding:10px 20px;background:var(--mark-bg);border-bottom:2px solid var(--accent);}}
.brand-name{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:1.15rem;color:var(--mark-text);letter-spacing:-.01em}}
.brand-name em{{font-style:italic;font-weight:300;color:var(--accent)}}
.live-tag{{display:inline-flex;align-items:center;gap:6px;padding:3px 10px 3px 8px;background:var(--accent);color:#fff;border-radius:4px;font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.16em;text-transform:uppercase;}}
.live-dot{{width:5px;height:5px;background:rgba(255,255,255,.85);border-radius:50%;animation:pulse 1.8s ease-in-out infinite}}

/* ══ CARD HEADER ══ */
.card-header{{padding:clamp(1.4rem,3vw,2rem) clamp(1.4rem,3vw,2rem) 1rem;border-bottom:1px solid var(--line-color);}}
.header-eyebrow{{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border:1px solid var(--border-acc);border-radius:4px;font-family:'DM Mono',monospace;font-size:.54rem;letter-spacing:.2em;text-transform:uppercase;color:var(--accent);background:var(--accent-glow);margin-bottom:10px;}}
.eyebrow-dot{{width:4px;height:4px;background:var(--accent);border-radius:50%;animation:pulse 1.8s ease-in-out infinite}}
.header-title{{font-family:'Cormorant Garamond',serif;font-weight:700;font-size:clamp(1.7rem,4vw,2.4rem);line-height:.92;letter-spacing:-.02em;color:var(--text);margin-bottom:6px;}}
.header-title em{{font-style:italic;font-weight:300;color:var(--accent)}}
.header-meta{{font-family:'DM Mono',monospace;font-size:.58rem;letter-spacing:.14em;text-transform:uppercase;color:var(--text3);}}

/* ══ ORDER ROWS ══ */
.orders-list{{padding:clamp(1rem,2.5vw,1.4rem) clamp(1.4rem,3vw,2rem);display:flex;flex-direction:column;gap:8px;}}
.order-row{{display:flex;align-items:center;gap:12px;padding:13px 16px;border-radius:12px;border:1px solid var(--border);background:var(--surface);text-decoration:none;color:var(--text);transition:border-color .2s,transform .15s,box-shadow .2s;animation:fadeUp .6s var(--ease) both;}}
.order-row:hover{{border-color:var(--border-acc);transform:translateX(4px);box-shadow:var(--shadow-sm)}}
.order-row-icon{{width:36px;height:36px;border-radius:9px;background:var(--accent-glow);border:1px solid var(--border-acc);display:flex;align-items:center;justify-content:center;font-size:.85rem;flex-shrink:0;}}
.order-row-body{{flex:1;min-width:0}}
.order-row-id{{font-family:'DM Mono',monospace;font-size:.65rem;letter-spacing:.1em;color:var(--accent);margin-bottom:2px;}}
.order-row-meta{{font-size:.8rem;color:var(--text3);font-weight:300;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.order-row-status{{flex-shrink:0;display:flex;flex-direction:column;align-items:flex-end;gap:4px;}}
.status-pill{{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-family:'DM Mono',monospace;font-size:.48rem;letter-spacing:.12em;text-transform:uppercase;font-weight:500;border:1px solid;white-space:nowrap;}}
.order-row-arrow{{font-size:.8rem;color:var(--text3);opacity:.5;transition:opacity .2s,transform .2s;}}
.order-row:hover .order-row-arrow{{opacity:1;transform:translateX(3px)}}

/* ══ EMPTY STATE ══ */
.empty-state{{padding:clamp(1.4rem,3vw,2rem);text-align:center;}}
.empty-icon{{font-size:2rem;margin-bottom:12px;opacity:.5}}
.empty-title{{font-family:'Cormorant Garamond',serif;font-size:1.4rem;font-weight:700;color:var(--text);margin-bottom:6px;}}
.empty-sub{{font-size:.84rem;color:var(--text3);font-weight:300;line-height:1.7;}}

/* ══ CARD FOOTER ══ */
.card-footer{{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-top:1px solid var(--line-color);}}
.footer-label{{font-family:'DM Mono',monospace;font-size:.5rem;letter-spacing:.16em;text-transform:uppercase;color:var(--text3)}}
.home-link{{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:8px;background:var(--accent);color:#fff;font-family:'DM Mono',monospace;font-size:.54rem;letter-spacing:.16em;text-transform:uppercase;text-decoration:none;transition:opacity .2s,transform .15s;box-shadow:var(--shadow-btn);}}
.home-link:hover{{opacity:.85;transform:translateY(-1px)}}

/* ══ WASH SPINNER ══ */
.wash-anim{{position:relative;width:26px;height:26px;flex-shrink:0}}
.wash-ring{{position:absolute;inset:0;border-radius:50%;border:1px solid var(--border)}}
.wash-arc{{position:absolute;inset:0;border-radius:50%;border:1.5px solid transparent;border-top-color:var(--accent);border-right-color:var(--accent);animation:spin 2.2s linear infinite}}
.wash-arc2{{position:absolute;inset:5px;border-radius:50%;border:1px solid transparent;border-bottom-color:var(--accent2);animation:spin 3.5s linear infinite reverse;opacity:.55}}
.wash-dot{{position:absolute;width:3px;height:3px;background:var(--accent);border-radius:50%;top:50%;left:50%;transform:translate(-50%,-50%)}}

@keyframes cardEnter{{from{{opacity:0;transform:translateY(20px) scale(.97)}}to{{opacity:1;transform:translateY(0) scale(1)}}}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes orbDrift{{0%{{transform:translate(0,0) scale(1)}}100%{{transform:translate(28px,-20px) scale(1.06)}}}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.3;transform:scale(.6)}}}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>
</head>
<body>

<div class="amb-orb o1"></div>
<div class="amb-orb o2"></div>
<div class="deco-word w1">FRESH</div>
<div class="deco-word w2">Clean</div>

<button id="themeToggle" onclick="toggleTheme()" aria-label="Toggle theme">☾</button>

<div class="main-card">
  <div class="card-top">
    <span class="brand-name">Laundry <em>Lounge</em></span>
    <div class="live-tag"><div class="live-dot"></div>Service Lookup</div>
  </div>

  <div class="card-header">
    <div class="header-eyebrow"><div class="eyebrow-dot"></div>Customer Laundry</div>
    <h1 class="header-title">Results for <em>"{name_clean}"</em></h1>
    <p class="header-meta">{len(orders)} order{'s' if len(orders) != 1 else ''} found &nbsp;·&nbsp; Click any row to track</p>
  </div>

  {'<div class="orders-list">' + rows_html + '</div>' if orders else '''
  <div class="empty-state">
    <div class="empty-icon">🧺</div>
    <div class="empty-title">No laundry service found</div>
    <p class="empty-sub">We couldn\'t find any service under this name.<br>Check the spelling or ask a staff member for help.</p>
  </div>
  '''}

  <div class="card-footer">
    <div style="display:flex;align-items:center;gap:8px">
      <div class="wash-anim">
        <div class="wash-ring"></div>
        <div class="wash-arc"></div>
        <div class="wash-arc2"></div>
        <div class="wash-dot"></div>
      </div>
      <span class="footer-label">Sta. Rosa · Nueva Ecija · Est. 2026</span>
    </div>
    <a href="/" class="home-link">← Home</a>
  </div>
</div>

<script>
function toggleTheme(){{
  const html=document.documentElement,isDark=html.dataset.theme==='dark';
  html.dataset.theme=isDark?'light':'dark';
  document.getElementById('themeToggle').textContent=isDark?'☾':'☀';
  localStorage.setItem('ll-theme',html.dataset.theme);
}}
(function(){{
  const s=localStorage.getItem('ll-theme');
  if(s){{document.documentElement.dataset.theme=s;const b=document.getElementById('themeToggle');if(b)b.textContent=s==='dark'?'☾':'☀';}}
}})();
</script>
</body>
</html>"""


@app.route("/api/public/track/<path:tracking_id>")
def api_public_track(tracking_id):
    order = query(
        """SELECT o.tracking_id, o.status, o.service_type,
                  o.weight_kg, o.amount, o.with_dryer, o.with_downy,
                  o.created_at, o.started_at, o.completed_at, o.stage_ends_at,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.tracking_id=%s""",
        (tracking_id,), one=True
    )
    if not order:
        return jresp({"error": "Order not found"}, 404)
    if order.get("stage_ends_at") and order.get("status") in ("washing", "drying", "downy"):
        rem = (order["stage_ends_at"] - datetime.now()).total_seconds()
        order["remaining_seconds"] = max(0, int(rem))
    return jresp(order)


# ════════════════════════════════════════════════════════════════
#  AUTH — PAGES
# ════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    session.clear()
    return redirect(url_for("login"))


@app.route("/login", methods=["GET"])
def login():
    if "user_id" in session:
        return redirect_by_role(session.get("role"))
    maintenance = is_maintenance_mode()
    return render_template("login.html", maintenance=maintenance)


@app.route("/login", methods=["POST"])
def login_post():
    email = request.form.get("email",    "").strip()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(url_for("login"))

    if is_maintenance_mode():
        user = query(
            "SELECT * FROM users WHERE (email=%s OR username=%s)",
            (email, email), one=True
        )
        if not user or user["role"] != "superadmin":
            flash(
                "🔧 System is currently under maintenance. Only the Super Admin can log in.", "maintenance")
            log_audit(email, "login_blocked_maintenance",
                      email, request.remote_addr)
            return redirect(url_for("login"))
    else:
        user = query(
            "SELECT * FROM users WHERE (email=%s OR username=%s) "
            "AND status!='blocked' AND (is_archived=0 OR is_archived IS NULL)",
            (email, email), one=True
        )

    if not user or not check_password_hash(user["password_hash"], password):
        flash("Incorrect email or password.", "error")
        log_audit(email, "login_failed", email, request.remote_addr)
        return redirect(url_for("login"))

    if user.get("status") == "blocked":
        flash("Your account has been blocked.", "error")
        return redirect(url_for("login"))

    session.clear()
    session["user_id"] = user["user_id"]
    session["full_name"] = user["full_name"]
    session["role"] = user["role"]
    session["email"] = user["email"]
    session["login_ts"] = datetime.now().timestamp()
    session.permanent = bool(request.form.get("remember"))

    log_audit(user["full_name"], "login", user["email"], request.remote_addr)
    return redirect_by_role(user["role"])


@app.route("/logout")
def logout():
    name = session.get("full_name", "User")
    log_audit(name, "logout", session.get("email", ""), request.remote_addr)
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


app.add_url_rule("/admin/logout",      "admin_logout",      logout)
app.add_url_rule("/staff/logout",      "staff_logout",      logout)
app.add_url_rule("/superadmin/logout", "superadmin_logout", logout)


@app.route("/register", methods=["POST"])
def register():
    if is_maintenance_mode():
        flash("🔧 Registrations are currently disabled.", "maintenance")
        return redirect(url_for("login"))

    first = request.form.get("first_name", "").strip()
    last = request.form.get("last_name",  "").strip()
    email = request.form.get("email",      "").strip()
    phone = request.form.get("phone",      "").strip()
    password = request.form.get("password",   "")
    confirm = request.form.get("confirm",    "")

    if not all([first, last, email, password]):
        flash("All required fields must be filled.", "error")
        return redirect(url_for("login"))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("login"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("login"))
    if query("SELECT user_id FROM users WHERE email=%s", (email,), one=True):
        flash("Email already registered.", "error")
        return redirect(url_for("login"))

    full_name = f"{first} {last}"
    query(
        "INSERT INTO users (full_name, email, phone, password_hash, role, status) "
        "VALUES (%s,%s,%s,%s,'customer','active')",
        (full_name, email, phone, generate_password_hash(password)), commit=True
    )
    log_audit(full_name, "register", email, request.remote_addr)
    flash("Account created! You can now sign in.", "success")
    return redirect(url_for("login"))


# ── Forgot / Reset password ────────────────────────────────────

@app.route("/api/forgot-password", methods=["POST"])
def api_forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jresp({"ok": True})

    user = query(
        "SELECT user_id, full_name, email FROM users WHERE email=%s AND status != 'blocked'",
        (email,), one=True
    )
    if user:
        token = secrets.token_urlsafe(40)
        expiry = datetime.now() + timedelta(hours=1)
        query(
            "INSERT INTO password_resets (user_id, token, expires_at) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE token=%s, expires_at=%s",
            (user["user_id"], token, expiry, token, expiry), commit=True,
        )
        send_reset_email(user["email"], token, user["full_name"])
        log_audit(email, "forgot_password_requested",
                  email, request.remote_addr)

    return jresp({"ok": True})


@app.route("/reset-password", methods=["GET"])
def reset_password_redirect():
    token = request.args.get("token", "").strip()
    if not token:
        flash("Invalid or missing reset token.", "error")
        return redirect(url_for("login"))

    row = query(
        "SELECT user_id FROM password_resets WHERE token=%s AND expires_at > NOW()",
        (token,), one=True
    )
    if not row:
        flash("This password reset link is invalid or has expired.", "error")
        return redirect(url_for("login"))

    return redirect(url_for("login") + f"?reset_token={token}")


@app.route("/api/reset-password", methods=["POST"])
def api_reset_password():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    new_password = (data.get("new_password") or "")

    if not token or not new_password:
        return jresp({"error": "Missing token or password."}, 400)
    if len(new_password) < 8:
        return jresp({"error": "Password must be at least 8 characters."}, 400)

    row = query(
        """SELECT pr.user_id, u.email, u.full_name
           FROM password_resets pr
           JOIN users u ON u.user_id = pr.user_id
           WHERE pr.token=%s AND pr.expires_at > NOW()""",
        (token,), one=True
    )
    if not row:
        return jresp({"error": "This reset link is invalid or has expired."}, 400)

    query(
        "UPDATE users SET password_hash=%s WHERE user_id=%s",
        (generate_password_hash(new_password), row["user_id"]), commit=True,
    )
    query("DELETE FROM password_resets WHERE user_id=%s",
          (row["user_id"],), commit=True)
    log_audit(row["email"], "password_reset_completed",
              row["email"], request.remote_addr)
    return jresp({"ok": True})


@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    email = request.form.get("email", "").strip()
    if email:
        user = query(
            "SELECT user_id, full_name FROM users WHERE email=%s AND status != 'blocked'",
            (email,), one=True
        )
        if user:
            token = secrets.token_urlsafe(40)
            expiry = datetime.now() + timedelta(hours=1)
            query(
                "INSERT INTO password_resets (user_id, token, expires_at) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE token=%s, expires_at=%s",
                (user["user_id"], token, expiry, token, expiry), commit=True,
            )
            send_reset_email(email, token, user["full_name"])
    flash("If that email is registered, a reset link has been sent.", "success")
    return redirect(url_for("login"))


# ════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ════════════════════════════════════════════════════════════════

@app.route("/superadmin")
@role_required("superadmin")
def superadmin_dashboard():
    return render_template("superadmin.html")


@app.route("/admin")
@role_required("admin", "superadmin")
def admin_dashboard():
    if is_maintenance_mode() and session.get("role") != "superadmin":
        session.clear()
        flash("🔧 System is under maintenance.", "maintenance")
        return redirect(url_for("login"))
    return render_template("admin.html")


@app.route("/staff")
@role_required("staff", "admin", "superadmin")
def staff_dashboard():
    if is_maintenance_mode() and session.get("role") != "superadmin":
        session.clear()
        flash("🔧 System is under maintenance.", "maintenance")
        return redirect(url_for("login"))
    return render_template("operator.html")


@app.route("/customer")
@role_required("customer")
def customer_dashboard():
    if is_maintenance_mode():
        session.clear()
        flash("🔧 System is under maintenance.", "maintenance")
        return redirect(url_for("login"))
    return render_template("customer.html")


# ════════════════════════════════════════════════════════════════
#  API — SHARED  /api/me
# ════════════════════════════════════════════════════════════════

@app.route("/api/me")
@login_required
def api_me():
    user = query(
        "SELECT user_id, full_name, email, phone, role, status, created_at "
        "FROM users WHERE user_id=%s",
        (session["user_id"],), one=True
    )
    return jresp(user or {})


@app.route("/api/me/update", methods=["PUT"])
@login_required
@_require_json_or_xhr
def api_me_update():
    d = request.get_json(silent=True) or {}
    full_name = d.get("full_name", "").strip()
    phone = d.get("phone",     "").strip()

    if not full_name:
        return jresp({"error": "Name is required"}, 400)

    query(
        "UPDATE users SET full_name=%s, phone=%s WHERE user_id=%s",
        (full_name, phone, session["user_id"]), commit=True
    )
    session["full_name"] = full_name
    log_audit(session["full_name"], "profile_update",
              session["email"], request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/me/change-password", methods=["PUT"])
@login_required
@_require_json_or_xhr
def api_me_change_password():
    d = request.get_json(silent=True) or {}
    current_pass = d.get("current_password", "")
    new_pass = d.get("new_password",     "")
    confirm_pass = d.get("confirm_password", "")

    if not all([current_pass, new_pass, confirm_pass]):
        return jresp({"error": "All password fields are required"}, 400)
    if new_pass != confirm_pass:
        return jresp({"error": "New passwords do not match"}, 400)
    if len(new_pass) < 8:
        return jresp({"error": "New password must be at least 8 characters"}, 400)

    user = query("SELECT password_hash FROM users WHERE user_id=%s",
                 (session["user_id"],), one=True)
    if not user or not check_password_hash(user["password_hash"], current_pass):
        return jresp({"error": "Current password is incorrect"}, 401)

    query(
        "UPDATE users SET password_hash=%s WHERE user_id=%s",
        (generate_password_hash(new_pass), session["user_id"]), commit=True
    )
    log_audit(session["full_name"], "password_changed",
              session["email"], request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — SUPER ADMIN
# ════════════════════════════════════════════════════════════════

@app.route("/api/superadmin/admins")
@role_required("superadmin")
def api_sa_admins():
    rows = query(
        "SELECT user_id, full_name, email, status, created_at "
        "FROM users WHERE role='admin' ORDER BY created_at DESC"
    )
    return jresp(rows or [])


@app.route("/api/superadmin/admins/create", methods=["POST"])
@role_required("superadmin")
def api_sa_create_admin():
    d = request.get_json(silent=True) or {}
    name = d.get("name",     "").strip()
    email = d.get("email",    "").strip()
    password = d.get("password", "")

    if not all([name, email, password]):
        return jresp({"error": "Missing fields"}, 400)
    if len(password) < 8:
        return jresp({"error": "Password must be at least 8 characters"}, 400)
    if query("SELECT user_id FROM users WHERE email=%s", (email,), one=True):
        return jresp({"error": "Email already exists"}, 409)

    query(
        "INSERT INTO users (full_name, email, password_hash, role, status) "
        "VALUES (%s,%s,%s,'admin','active')",
        (name, email, generate_password_hash(password)), commit=True
    )
    log_audit(session["full_name"], "create_admin", email, request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/superadmin/admins/update/<int:uid>", methods=["PUT"])
@role_required("superadmin")
def api_sa_update_admin(uid):
    d = request.get_json(silent=True) or {}
    name = d.get("name",   "").strip()
    email = d.get("email",  "").strip()
    status = d.get("status", "active")

    if status not in ("active", "inactive"):
        return jresp({"error": "Invalid status"}, 400)

    query(
        "UPDATE users SET full_name=%s, email=%s, status=%s "
        "WHERE user_id=%s AND role='admin'",
        (name, email, status, uid), commit=True
    )
    log_audit(session["full_name"], "update_admin",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/superadmin/admins/toggle/<int:uid>", methods=["PUT"])
@role_required("superadmin")
def api_sa_toggle_admin(uid):
    d = request.get_json(silent=True) or {}
    status = d.get("status", "active")
    if status not in ("active", "inactive"):
        return jresp({"error": "Invalid status"}, 400)
    query(
        "UPDATE users SET status=%s WHERE user_id=%s AND role='admin'",
        (status, uid), commit=True
    )
    log_audit(session["full_name"],
              f"admin_{status}", f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/superadmin/admins/delete/<int:uid>", methods=["DELETE"])
@role_required("superadmin")
def api_sa_delete_admin(uid):
    query("DELETE FROM users WHERE user_id=%s AND role='admin'", (uid,), commit=True)
    log_audit(session["full_name"], "delete_admin",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/superadmin/all-users")
@role_required("superadmin")
def api_sa_all_users():
    rows = query(
        "SELECT user_id, full_name, email, role, status, created_at "
        "FROM users ORDER BY created_at DESC"
    )
    return jresp(rows or [])


@app.route("/api/superadmin/audit-logs")
@role_required("superadmin")
def api_sa_audit_logs():
    rows = query("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 500")
    return jresp(rows or [])


@app.route("/api/superadmin/assign-role", methods=["POST"])
@role_required("superadmin")
def api_sa_assign_role():
    d = request.get_json(silent=True) or {}
    email = d.get("email", "").strip()
    role = d.get("role",  "customer")

    if role not in ("customer", "staff", "admin"):
        return jresp({"error": "Invalid role"}, 400)

    user = query("SELECT user_id FROM users WHERE email=%s",
                 (email,), one=True)
    if not user:
        return jresp({"error": "User not found"}, 404)

    query("UPDATE users SET role=%s WHERE email=%s", (role, email), commit=True)
    log_audit(session["full_name"], "assign_role",
              f"{email} → {role}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/superadmin/change-own-password", methods=["POST"])
@role_required("superadmin")
@_require_json_or_xhr
def api_sa_change_own_password():
    d = request.get_json(silent=True) or {}
    current = d.get("current_password",  "")
    new_pw = d.get("new_password",      "")
    confirm = d.get("confirm_password",  "")

    if not all([current, new_pw, confirm]):
        return jresp({"error": "All fields are required."}, 400)
    if new_pw != confirm:
        return jresp({"error": "New passwords do not match."}, 400)
    if len(new_pw) < 8:
        return jresp({"error": "Password must be at least 8 characters."}, 400)

    user = query("SELECT password_hash FROM users WHERE user_id=%s",
                 (session["user_id"],), one=True)
    if not user or not check_password_hash(user["password_hash"], current):
        return jresp({"error": "Current password is incorrect."}, 401)

    query(
        "UPDATE users SET password_hash=%s WHERE user_id=%s",
        (generate_password_hash(new_pw), session["user_id"]), commit=True
    )
    query("DELETE FROM password_resets WHERE user_id=%s",
          (session["user_id"],), commit=True)
    log_audit(session["full_name"], "superadmin_password_changed",
              session["email"], request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/superadmin/request-reset", methods=["POST"])
def api_sa_request_reset():
    d = request.get_json(silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    secret_phrase = (d.get("secret_phrase") or "").strip()
    SA_RECOVERY_PHRASE = os.environ.get("SA_RECOVERY_PHRASE", "")

    if not SA_RECOVERY_PHRASE:
        log_audit(email, "sa_reset_attempt_no_phrase_configured",
                  email, request.remote_addr)
        return jresp({"ok": True})

    phrase_ok = secrets.compare_digest(
        secret_phrase.encode(), SA_RECOVERY_PHRASE.encode()
    )
    if phrase_ok:
        user = query(
            "SELECT user_id, full_name, email FROM users "
            "WHERE email=%s AND role='superadmin' AND status='active'",
            (email,), one=True
        )
        if user:
            token = secrets.token_urlsafe(40)
            expiry = datetime.now() + timedelta(hours=1)
            query(
                "INSERT INTO password_resets (user_id, token, expires_at) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE token=%s, expires_at=%s",
                (user["user_id"], token, expiry, token, expiry), commit=True,
            )
            send_reset_email(user["email"], token, user["full_name"])
            log_audit(email, "sa_reset_email_sent", email, request.remote_addr)
    else:
        log_audit(email, "sa_reset_wrong_phrase", email, request.remote_addr)

    return jresp({"ok": True})


@app.route("/api/superadmin/backup", methods=["POST"])
@role_required("superadmin")
def api_sa_create_backup():
    query(
        "INSERT INTO backups (created_by, note, created_at) VALUES (%s,'Manual backup',NOW())",
        (session["full_name"],), commit=True
    )
    log_audit(session["full_name"], "backup_created",
              "manual", request.remote_addr)
    return jresp({"ok": True, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# ════════════════════════════════════════════════════════════════
#  EMERGENCY ACTIONS
# ════════════════════════════════════════════════════════════════

@app.route("/api/superadmin/emergency/<action>", methods=["POST"])
@role_required("superadmin")
def api_sa_emergency(action):
    allowed = {
        "shutdown", "enable_system", "block_all",
        "reset_admin_passwords", "disable_promos", "force_logout",
    }
    if action not in allowed:
        return jresp({"error": "Invalid action"}, 400)

    extra = {}

    if action == "shutdown":
        query(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES ('maintenance_mode','1') "
            "ON DUPLICATE KEY UPDATE setting_value='1'", commit=True
        )
        ts = str(datetime.now().timestamp())
        query(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES ('force_logout_ts',%s) "
            "ON DUPLICATE KEY UPDATE setting_value=%s",
            (ts, ts), commit=True
        )
        extra["message"] = "System is now in maintenance mode."

    elif action == "enable_system":
        query(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES ('maintenance_mode','0') "
            "ON DUPLICATE KEY UPDATE setting_value='0'", commit=True
        )
        extra["message"] = "System is back online."

    elif action == "disable_promos":
        affected = query(
            "SELECT COUNT(*) AS cnt FROM promos WHERE is_active=1", one=True)
        query("UPDATE promos SET is_active=0", commit=True)
        extra["disabled_count"] = affected["cnt"] if affected else 0
        extra["message"] = "All promo codes deactivated."

    elif action == "force_logout":
        ts = str(datetime.now().timestamp())
        query(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES ('force_logout_ts',%s) "
            "ON DUPLICATE KEY UPDATE setting_value=%s",
            (ts, ts), commit=True
        )
        extra["message"] = "All active sessions invalidated."

    elif action == "reset_admin_passwords":
        chars = string.ascii_letters + string.digits
        admins = query(
            "SELECT user_id, email FROM users WHERE role='admin'") or []
        reset_summary = []
        for a in admins:
            new_pw = "".join(secrets.choice(chars) for _ in range(12))
            query(
                "UPDATE users SET password_hash=%s WHERE user_id=%s",
                (generate_password_hash(new_pw), a["user_id"]), commit=True
            )
            log_audit(session["full_name"], "admin_password_reset",
                      f"email={a['email']} tmp_pw={new_pw}", request.remote_addr)
            reset_summary.append({"email": a["email"], "tmp_password": new_pw})
        extra["reset_summary"] = reset_summary
        extra["message"] = f"Passwords reset for {len(reset_summary)} admin account(s)."

    elif action == "block_all":
        affected = query(
            "SELECT COUNT(*) AS cnt FROM users WHERE role='customer' AND status='active'", one=True
        )
        query("UPDATE users SET status='blocked' WHERE role='customer' AND status='active'", commit=True)
        extra["blocked_count"] = affected["cnt"] if affected else 0
        extra["message"] = f"{extra['blocked_count']} customer account(s) blocked."

    log_audit(session["full_name"], f"emergency_{action}",
              extra.get("message", "system"), request.remote_addr)
    return jresp({"ok": True, "action": action, **extra})


# ════════════════════════════════════════════════════════════════
#  API — SYSTEM CONFIG
# ════════════════════════════════════════════════════════════════

@app.route("/api/system/settings", methods=["GET"])
@role_required("admin", "superadmin")
def api_system_settings_get():
    return jresp(get_ui_settings())


@app.route("/api/system/settings", methods=["POST"])
@role_required("admin", "superadmin")
def api_system_settings_save():
    d = request.get_json(silent=True) or {}
    if not d:
        return jresp({"error": "No settings provided"}, 400)
    for key in d:
        if len(key) > 120:
            return jresp({"error": f"Key too long: {key[:40]}…"}, 400)

    for key, val in d.items():
        query(
            "INSERT INTO system_settings (setting_key, setting_value) "
            "VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value = %s",
            (key, str(val), str(val)), commit=True,
        )

    ui_keys = [k for k in d if k.startswith(
        ("ui_", "login_", "cu_", "op_", "adm_", "ticker_"))]
    perm_keys = [k for k in d if k.startswith("perm_")]
    sys_keys = [k for k in d if k not in ui_keys and k not in perm_keys]

    if ui_keys:
        log_audit(session["full_name"], "ui_customizer_save",
                  f"keys={ui_keys}",   request.remote_addr)
    if perm_keys:
        log_audit(session["full_name"], "permissions_save",
                  f"roles={perm_keys}", request.remote_addr)
    if sys_keys:
        log_audit(session["full_name"], "system_settings_save",
                  f"keys={sys_keys}",  request.remote_addr)

    return jresp({"ok": True, "saved": list(d.keys())})


@app.route("/api/ui/reset", methods=["POST"])
@role_required("superadmin")
def api_ui_reset():
    UI_KEY_PREFIXES = ("ui_", "login_", "cu_", "op_", "adm_", "ticker_")
    placeholders = " OR ".join("setting_key LIKE %s" for _ in UI_KEY_PREFIXES)
    rows = query(
        f"SELECT setting_key FROM system_settings WHERE {placeholders}",
        tuple(p + "%" for p in UI_KEY_PREFIXES)
    ) or []
    deleted = 0
    for row in rows:
        query("DELETE FROM system_settings WHERE setting_key = %s",
              (row["setting_key"],), commit=True)
        deleted += 1
    log_audit(session["full_name"], "ui_customizer_reset",
              f"deleted={deleted} keys", request.remote_addr)
    return jresp({"ok": True, "deleted": deleted, "message": "UI settings reset to factory defaults."})


@app.route("/api/ui/permissions")
@role_required("superadmin")
def api_ui_permissions():
    settings = get_ui_settings()

    def _parse(key: str) -> dict:
        try:
            return json.loads(settings.get(key, "{}"))
        except Exception:
            try:
                return json.loads(UI_DEFAULTS.get(key, "{}"))
            except Exception:
                return {}

    return jresp({
        "admin":    _parse("perm_admin"),
        "staff":    _parse("perm_staff"),
        "customer": _parse("perm_customer"),
    })


# ════════════════════════════════════════════════════════════════
#  API — ADMIN
# ════════════════════════════════════════════════════════════════

@app.route("/api/admin/analytics")
@role_required("admin", "superadmin")
def api_admin_analytics():
    today_row = query(
        "SELECT COUNT(*) AS today_services FROM orders WHERE DATE(created_at)=CURDATE()",
        one=True
    )
    top_svc = query(
        "SELECT service_type, COUNT(*) AS total FROM orders "
        "GROUP BY service_type ORDER BY total DESC LIMIT 1",
        one=True
    )
    peak = query(
        "SELECT HOUR(created_at) AS hour, COUNT(*) AS total "
        "FROM orders GROUP BY HOUR(created_at) ORDER BY total DESC LIMIT 1",
        one=True
    )
    if top_svc:
        top_svc["service_type"] = SERVICE_RATES.get(
            top_svc["service_type"], {}
        ).get("label", top_svc["service_type"])
    return jresp({
        "today_services": today_row["today_services"] if today_row else 0,
        "top_service":    top_svc,
        "peak_hour":      peak,
    })


@app.route("/api/admin/revenue")
@role_required("admin", "superadmin")
def api_admin_revenue():
    daily = query(
        "SELECT COALESCE(SUM(amount),0) AS daily_income FROM orders "
        "WHERE DATE(created_at)=CURDATE() AND status NOT IN ('cancelled')",
        one=True
    )
    monthly = query(
        "SELECT COALESCE(SUM(amount),0) AS monthly_income FROM orders "
        "WHERE MONTH(created_at)=MONTH(CURDATE()) AND YEAR(created_at)=YEAR(CURDATE()) "
        "AND status NOT IN ('cancelled')",
        one=True
    )
    return jresp({
        "daily_income":   float(daily["daily_income"] if daily else 0),
        "monthly_income": float(monthly["monthly_income"] if monthly else 0),
    })


@app.route("/api/admin/orders")
@role_required("admin", "superadmin")
def api_admin_orders():
    rows = query(
        """SELECT o.order_id, o.tracking_id, o.service_type, o.weight_kg,
                  o.amount, o.status, o.with_dryer, o.with_downy,
                  o.promo_code, o.discount_pct,
                  o.created_at, o.completed_at,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           ORDER BY o.created_at DESC
           LIMIT 500"""
    ) or []
    for o in rows:
        o["service_type_label"] = SERVICE_RATES.get(
            o["service_type"], {}
        ).get("label", o["service_type"])
    return jresp(rows)


@app.route("/api/admin/orders/status/<int:oid>", methods=["PUT"])
@role_required("admin", "superadmin", "staff")
def api_update_order_status(oid):
    d = request.get_json(silent=True) or {}
    status = d.get("status", "pending")
    valid = {"pending", "washing", "drying", "downy", "folding",
             "completed", "cancelled", "ongoing", "done"}
    if status not in valid:
        return jresp({"error": "Invalid status"}, 400)

    query("UPDATE orders SET status=%s WHERE order_id=%s",
          (status, oid), commit=True)
    if status in ("completed", "cancelled", "done"):
        query(
            "UPDATE machines SET status='free', current_order_id=NULL, "
            "current_stage=NULL, stage_ends_at=NULL "
            "WHERE current_order_id=%s", (oid,), commit=True
        )
        if status in ("completed", "done"):
            query(
                "UPDATE orders SET completed_at=NOW() WHERE order_id=%s AND completed_at IS NULL",
                (oid,), commit=True
            )
    log_audit(session["full_name"],
              f"order_{status}", f"id={oid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/admin/staff-performance")
@role_required("admin", "superadmin")
def api_admin_staff_performance():
    rows = query(
        """SELECT u.full_name,
                  COUNT(o.order_id) AS total_services,
                  COALESCE(SUM(o.amount),0) AS total_revenue
           FROM users u
           LEFT JOIN orders o ON o.encoded_by=u.user_id
                              AND DATE(o.created_at)>=DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                              AND o.status NOT IN ('cancelled')
           WHERE u.role='staff' AND (u.is_archived=0 OR u.is_archived IS NULL)
           GROUP BY u.user_id, u.full_name
           ORDER BY total_services DESC"""
    )
    return jresp(rows or [])


@app.route("/api/admin/export")
@role_required("admin", "superadmin")
def api_admin_export():
    rows = query(
        """SELECT o.order_id, o.tracking_id,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name,
                  o.service_type, o.weight_kg, o.amount,
                  o.with_dryer, o.with_downy, o.status, o.created_at
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           ORDER BY o.created_at DESC"""
    ) or []

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Order ID", "Tracking ID", "Customer", "Service",
                "Weight(kg)", "Amount(₱)", "Dryer", "Downy", "Status", "Date"])
    for o in rows:
        created = o.get("created_at")
        if isinstance(created, datetime):
            created = created.strftime("%Y-%m-%d %H:%M")
        elif created:
            created = str(created)
        else:
            created = ""
        w.writerow([
            o["order_id"], o["tracking_id"], o.get("customer_name", ""),
            SERVICE_RATES.get(o["service_type"], {}).get(
                "label", o["service_type"]),
            o["weight_kg"], o["amount"],
            "Yes" if o.get("with_dryer") else "No",
            "Yes" if o.get("with_downy") else "No",
            o["status"], created,
        ])

    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=orders_export.csv"
    return resp


@app.route("/api/admin/feedbacks")
@role_required("admin", "superadmin")
def api_admin_feedbacks():
    rows = query(
        """SELECT f.feedback_id, f.order_id, f.rating, f.comment, f.created_at,
                  o.tracking_id, o.service_type,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM feedbacks f
           LEFT JOIN orders  o ON f.order_id    = o.order_id
           LEFT JOIN users   u ON f.customer_id = u.user_id
           ORDER BY f.created_at DESC
           LIMIT 500"""
    ) or []
    for r in rows:
        r["service_type_label"] = SERVICE_RATES.get(
            r.get("service_type", ""), {}
        ).get("label", r.get("service_type", ""))
    return jresp(rows)


# ════════════════════════════════════════════════════════════════
#  API — ADMIN ISSUE REPORTS  (NEW)
# ════════════════════════════════════════════════════════════════

@app.route("/api/admin/issues")
@role_required("admin", "superadmin")
def api_admin_issues():
    """
    Return all issue reports submitted by operators.
    Used by the admin Issue Reports section.
    """
    rows = query(
        """SELECT i.issue_id, i.issue_type, i.order_id,
                  i.description, i.status, i.reported_at,
                  i.reporter_name,
                  u.full_name AS reporter_full_name
           FROM issues i
           LEFT JOIN users u ON i.reported_by = u.user_id
           ORDER BY i.reported_at DESC
           LIMIT 500"""
    ) or []
    # Prefer stored reporter_name, fall back to JOIN
    for r in rows:
        if not r.get("reporter_name") and r.get("reporter_full_name"):
            r["reporter_name"] = r["reporter_full_name"]
        r.pop("reporter_full_name", None)
    return jresp(rows)


@app.route("/api/admin/issues/<int:issue_id>/resolve", methods=["PUT"])
@role_required("admin", "superadmin")
@_require_json_or_xhr
def api_admin_resolve_issue(issue_id):
    """Mark an issue report as resolved."""
    row = query("SELECT issue_id FROM issues WHERE issue_id=%s",
                (issue_id,), one=True)
    if not row:
        return jresp({"error": "Issue not found"}, 404)

    query(
        "UPDATE issues SET status='resolved' WHERE issue_id=%s",
        (issue_id,), commit=True
    )
    log_audit(session["full_name"], "resolve_issue",
              f"issue_id={issue_id}", request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — STAFF MANAGEMENT  (active roster)
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff")
@role_required("admin", "superadmin")
def api_staff_list():
    rows = query(
        "SELECT user_id, full_name, email, status, created_at "
        "FROM users WHERE role='staff' AND (is_archived=0 OR is_archived IS NULL) "
        "ORDER BY full_name"
    ) or []
    for r in rows:
        r["staff_id"] = r["user_id"]
    return jresp(rows)


@app.route("/api/staff/add", methods=["POST"])
@role_required("admin", "superadmin")
def api_staff_add():
    d = request.get_json(silent=True) or {}
    name = d.get("name",     "").strip()
    email = d.get("email",    "").strip()
    password = d.get("password", "")

    if not all([name, email, password]):
        return jresp({"error": "Missing fields"}, 400)
    if len(password) < 8:
        return jresp({"error": "Password too short"}, 400)
    if query("SELECT user_id FROM users WHERE email=%s", (email,), one=True):
        return jresp({"error": "Email already exists"}, 409)

    query(
        "INSERT INTO users (full_name, email, password_hash, role, status) "
        "VALUES (%s,%s,%s,'staff','active')",
        (name, email, generate_password_hash(password)), commit=True
    )
    log_audit(session["full_name"], "add_staff", email, request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/update/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
def api_staff_update(uid):
    d = request.get_json(silent=True) or {}
    name = d.get("name",     "").strip()
    email = d.get("email",    "").strip()
    status = d.get("status",   "active")
    new_pass = d.get("password", "").strip()

    if status not in ("active", "inactive"):
        return jresp({"error": "Invalid status"}, 400)

    if new_pass:
        if len(new_pass) < 8:
            return jresp({"error": "Password must be at least 8 characters"}, 400)
        query(
            "UPDATE users SET full_name=%s, email=%s, status=%s, password_hash=%s "
            "WHERE user_id=%s AND role='staff'",
            (name, email, status, generate_password_hash(new_pass), uid), commit=True
        )
    else:
        query(
            "UPDATE users SET full_name=%s, email=%s, status=%s "
            "WHERE user_id=%s AND role='staff'",
            (name, email, status, uid), commit=True
        )
    log_audit(session["full_name"], "update_staff",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/toggle/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
@_require_json_or_xhr
def api_staff_toggle(uid):
    d = request.get_json(silent=True) or {}
    status = d.get("status", "active")
    if status not in ("active", "inactive"):
        return jresp({"error": "Invalid status"}, 400)
    query(
        "UPDATE users SET status=%s WHERE user_id=%s AND role='staff'",
        (status, uid), commit=True
    )
    log_audit(session["full_name"],
              f"staff_{status}", f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/remove/<int:uid>", methods=["DELETE"])
@role_required("admin", "superadmin")
def api_staff_remove(uid):
    query("DELETE FROM users WHERE user_id=%s AND role='staff'", (uid,), commit=True)
    log_audit(session["full_name"], "remove_staff",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


# ── Staff Archive ──────────────────────────────────────────────

@app.route("/api/staff/archive/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
@_require_json_or_xhr
def api_staff_archive(uid):
    query(
        "UPDATE users SET is_archived=1, archived_at=NOW() "
        "WHERE user_id=%s AND role='staff'",
        (uid,), commit=True
    )
    log_audit(session["full_name"], "archive_staff",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/unarchive/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
@_require_json_or_xhr
def api_staff_unarchive(uid):
    query(
        "UPDATE users SET is_archived=0, archived_at=NULL, status='active' "
        "WHERE user_id=%s AND role='staff'",
        (uid,), commit=True
    )
    log_audit(session["full_name"], "unarchive_staff",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/archived")
@role_required("admin", "superadmin")
def api_staff_archived():
    rows = query(
        "SELECT user_id, full_name, email, status, archived_at, created_at "
        "FROM users WHERE role='staff' AND is_archived=1 "
        "ORDER BY archived_at DESC"
    ) or []
    for r in rows:
        r["staff_id"] = r["user_id"]
    return jresp(rows)


@app.route("/api/staff/delete/<int:uid>", methods=["DELETE"])
@role_required("admin", "superadmin")
def api_staff_delete_permanent(uid):
    row = query(
        "SELECT user_id FROM users WHERE user_id=%s AND role='staff' AND is_archived=1",
        (uid,), one=True
    )
    if not row:
        return jresp({"error": "Staff not found or not archived"}, 404)
    query("UPDATE orders SET encoded_by=NULL WHERE encoded_by=%s", (uid,), commit=True)
    query("DELETE FROM issues   WHERE reported_by=%s",
          (uid,), commit=True)
    query("DELETE FROM users    WHERE user_id=%s",
          (uid,), commit=True)
    log_audit(session["full_name"], "delete_staff_permanent",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — MACHINES
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/machines")
@role_required("staff", "admin", "superadmin")
def api_machines():
    rows = query("SELECT * FROM machines ORDER BY unit_number") or []
    now = datetime.now()
    out = []
    for m in rows:
        e = dict(m)
        if m["status"] == "busy" and m.get("stage_ends_at"):
            rem = (m["stage_ends_at"] - now).total_seconds()
            stage_total = {
                "washing": WASH_SECS, "drying": DRY_SECS, "downy": DOWNY_SECS,
            }.get(m.get("current_stage", "washing"), WASH_SECS)
            e["remaining_seconds"] = max(0, int(rem))
            e["progress_pct"] = max(
                0, min(100, int((1 - rem/stage_total)*100)))
        out.append(e)
    return jresp(out)


# ════════════════════════════════════════════════════════════════
#  API — OPERATOR TASKS
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/tasks")
@role_required("staff", "admin", "superadmin")
def api_staff_tasks():
    active = query(
        """SELECT o.*,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.status NOT IN ('completed','cancelled','done')
           ORDER BY o.created_at ASC"""
    ) or []
    done_today = query(
        """SELECT o.*,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.status IN ('completed','done')
             AND DATE(o.created_at)=CURDATE()
           ORDER BY o.completed_at DESC
           LIMIT 50"""
    ) or []

    combined = list(active) + list(done_today)
    now = datetime.now()
    pos = 1
    for o in combined:
        o["service_type_label"] = SERVICE_RATES.get(
            o["service_type"], {}
        ).get("label", o["service_type"])
        if o.get("stage_ends_at") and o["status"] in ("washing", "drying", "downy"):
            rem = (o["stage_ends_at"] - now).total_seconds()
            stage_total = {
                "washing": WASH_SECS, "drying": DRY_SECS, "downy": DOWNY_SECS,
            }.get(o["status"], WASH_SECS)
            o["remaining_seconds"] = max(0, int(rem))
            o["progress_pct"] = max(
                0, min(100, int((1 - rem/stage_total)*100)))
        if o.get("fold_ends_at") and o["status"] == "folding":
            fe = o["fold_ends_at"]
            if hasattr(fe, "isoformat"):
                o["fold_ends_at"] = fe.isoformat()
        if o["status"] == "pending":
            o["queue_position"] = pos
            pos += 1
    return jresp(combined)


# ════════════════════════════════════════════════════════════════
#  ENCODE SERVICE
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/encode", methods=["POST"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_encode_service():
    encoder = query(
        "SELECT status FROM users WHERE user_id=%s",
        (session["user_id"],), one=True
    )
    if not encoder or encoder["status"] != "active":
        return jresp({"error": "Your account is not active."}, 403)

    d = request.get_json(silent=True) or {}
    customer = d.get("customer_name",  "").strip()
    customer_email = d.get("customer_email", "").strip() or None
    svc_type = d.get("service_type",   "")
    weight = float(d.get("weight_kg", 0))
    with_dryer = bool(d.get("with_dryer", False))
    with_downy = bool(d.get("with_downy", False))
    promo_code = d.get("promo_code", "").strip(
    ).upper() if d.get("promo_code") else None

    if not customer or not svc_type or weight < 0.5:
        return jresp({"error": "Missing or invalid fields"}, 400)
    if svc_type not in SERVICE_RATES:
        return jresp({"error": "Invalid service type"}, 400)

    discount_pct = 0.0
    if promo_code:
        promo = query(
            "SELECT * FROM promos WHERE code=%s AND is_active=1", (promo_code,), one=True
        )
        if not promo:
            return jresp({"error": "Invalid or expired promo code"}, 400)
        discount_pct = float(promo["discount"])

    amount = calc_amount(svc_type, weight, with_dryer, discount_pct)
    m_needed = machines_needed(weight)
    tracking = generate_tracking_id()

    # Link to registered customer if email matches
    linked_customer_id = None
    if customer_email:
        registered = query(
            "SELECT user_id FROM users WHERE email=%s AND role='customer' AND status='active' "
            "AND (is_archived=0 OR is_archived IS NULL)",
            (customer_email,), one=True
        )
        if registered:
            linked_customer_id = registered["user_id"]

    # Warn about multiple active services (non-blocking)
    active_service_count = 0
    if customer_email:
        row = query(
            "SELECT COUNT(*) AS cnt FROM orders "
            "WHERE customer_email=%s AND status NOT IN ('completed','done','cancelled')",
            (customer_email,), one=True
        )
        if row:
            active_service_count = int(row["cnt"] or 0)

    oid = query(
        """INSERT INTO orders
           (tracking_id, customer_id, customer_name_walk_in, customer_email,
            service_type, weight_kg, with_dryer, with_downy, amount,
            machines_needed, promo_code, discount_pct, status, encoded_by, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,NOW())""",
        (tracking, linked_customer_id, customer, customer_email,
         svc_type, weight, with_dryer, with_downy,
         amount, m_needed, promo_code, discount_pct, session["user_id"]),
        commit=True
    )
    log_audit(session["full_name"], "encode_service",
              f"{tracking} linked_customer={linked_customer_id}", request.remote_addr)

    svc_label = SERVICE_RATES.get(svc_type, {}).get("label", svc_type)
    send_tracking_email(
        order_id=oid, tracking_id=tracking, customer_email=customer_email,
        customer_name=customer, service_label=svc_label, amount=amount,
    )

    return jresp({
        "ok":                    True,
        "order_id":              oid,
        "tracking_id":           tracking,
        "amount":                amount,
        "machines_needed":       m_needed,
        "email_queued":          bool(customer_email),
        "linked_to_account":     linked_customer_id is not None,
        "active_services_warning": active_service_count,
    })


@app.route("/api/staff/my-orders")
@role_required("staff", "admin", "superadmin")
def api_staff_my_orders():
    rows = query(
        """SELECT o.*,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.encoded_by=%s AND DATE(o.created_at)=CURDATE()
           ORDER BY o.created_at DESC""",
        (session["user_id"],)
    ) or []
    for o in rows:
        o["service_type_label"] = SERVICE_RATES.get(
            o["service_type"], {}
        ).get("label", o["service_type"])
    return jresp(rows)


# ════════════════════════════════════════════════════════════════
#  ASSIGN & START
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/orders/assign-start/<int:oid>", methods=["POST"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_assign_start(oid):
    d = request.get_json(silent=True) or {}
    machine_ids = d.get("machine_ids", [])

    if not machine_ids or not isinstance(machine_ids, list):
        return jresp({"error": "No machines selected"}, 400)
    try:
        machine_ids = list({int(mid) for mid in machine_ids})
    except (TypeError, ValueError):
        return jresp({"error": "Invalid machine ID format"}, 400)

    order = query(
        "SELECT * FROM orders WHERE order_id=%s AND status='pending'", (oid,), one=True
    )
    if not order:
        return jresp({"error": "Order not found or already started"}, 404)

    needed = int(order["machines_needed"])
    if len(machine_ids) != needed:
        return jresp({
            "error": f"This order requires exactly {needed} machine(s). "
                     f"You selected {len(machine_ids)}."
                     }, 400)

    placeholders = ",".join(["%s"] * len(machine_ids))
    free_machines = query(
        f"SELECT machine_id, unit_number FROM machines "
        f"WHERE machine_id IN ({placeholders}) AND status='free'",
        tuple(machine_ids)
    ) or []

    free_ids = {m["machine_id"] for m in free_machines}
    taken = [mid for mid in machine_ids if mid not in free_ids]
    if taken:
        taken_units = query(
            f"SELECT unit_number FROM machines WHERE machine_id IN ({','.join(['%s']*len(taken))})",
            tuple(taken)
        ) or []
        unit_nums = ", ".join(str(m["unit_number"]) for m in taken_units)
        return jresp({"error": f"Machine(s) Unit {unit_nums} are no longer free."}, 409)

    wash_ends = datetime.now() + timedelta(seconds=WASH_SECS)
    query(
        "UPDATE orders SET status='washing', stage_ends_at=%s, started_at=NOW() "
        "WHERE order_id=%s AND status='pending'",
        (wash_ends, oid), commit=True
    )
    for mid in machine_ids:
        query(
            "UPDATE machines SET status='busy', current_order_id=%s, "
            "current_stage='washing', stage_ends_at=%s "
            "WHERE machine_id=%s AND status='free'",
            (oid, wash_ends, mid), commit=True
        )
        query(
            "INSERT IGNORE INTO order_machines (order_id, machine_id) VALUES (%s,%s)",
            (oid, mid), commit=True
        )

    log_audit(session["full_name"], "start_service",
              f"order_id={oid} machines={machine_ids}", request.remote_addr)
    return jresp({
        "ok":              True,
        "order_id":        oid,
        "machines_needed": needed,
        "machine_ids":     machine_ids,
        "wash_ends_at":    wash_ends.strftime("%Y-%m-%dT%H:%M:%S"),
    })


# ════════════════════════════════════════════════════════════════
#  FOLD / COMPLETE / EMAIL
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/orders/fold-done/<int:oid>", methods=["PUT"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_fold_done(oid):
    query(
        "UPDATE orders SET status='ready_for_pickup', fold_ends_at=NULL "
        "WHERE order_id=%s AND status='folding'",
        (oid,), commit=True
    )
    query(
        "UPDATE machines SET status='free', current_order_id=NULL, "
        "current_stage=NULL, stage_ends_at=NULL "
        "WHERE current_order_id=%s", (oid,), commit=True
    )
    log_audit(session["full_name"], "fold_done",
              f"order_id={oid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/orders/fold-done-bulk", methods=["PUT"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_fold_done_bulk():
    d = request.get_json(silent=True) or {}
    ids = d.get("order_ids", [])
    if not ids:
        return jresp({"error": "No order IDs provided"}, 400)

    completed = 0
    for oid in ids:
        query(
            "UPDATE orders SET status='ready_for_pickup', fold_ends_at=NULL "
            "WHERE order_id=%s AND status='folding'",
            (oid,), commit=True
        )
        query(
            "UPDATE machines SET status='free', current_order_id=NULL, "
            "current_stage=NULL, stage_ends_at=NULL "
            "WHERE current_order_id=%s", (oid,), commit=True
        )
        completed += 1

    log_audit(session["full_name"], "bulk_fold_done",
              f"count={completed}", request.remote_addr)
    return jresp({"ok": True, "completed": completed})


@app.route("/api/staff/orders/complete/<int:oid>", methods=["PUT"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_complete_order(oid):
    query(
        "UPDATE orders SET status='completed', completed_at=NOW() "
        "WHERE order_id=%s AND status='ready_for_pickup'",
        (oid,), commit=True
    )
    query(
        "UPDATE machines SET status='free', current_order_id=NULL, "
        "current_stage=NULL, stage_ends_at=NULL "
        "WHERE current_order_id=%s", (oid,), commit=True
    )
    log_audit(session["full_name"], "order_completed",
              f"order_id={oid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/orders/send-pickup-email/<int:oid>", methods=["POST"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_send_pickup_email(oid):
    order = query(
        """SELECT o.tracking_id, o.customer_email, o.status,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.order_id=%s""",
        (oid,), one=True
    )
    if not order:
        return jresp({"error": "Order not found"}, 404)
    if order["status"] != "ready_for_pickup":
        return jresp({"error": "Order is not in ready_for_pickup status"}, 400)
    if not order.get("customer_email"):
        return jresp({"error": "No customer email on file for this order"}, 400)

    send_status_update_email(
        tracking_id=order["tracking_id"],
        customer_email=order["customer_email"],
        customer_name=order.get("customer_name", ""),
        old_status="folding",
        new_status="ready_for_pickup",
    )
    log_audit(session["full_name"], "send_pickup_email",
              f"order_id={oid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/staff/machines/<int:machine_id>/status", methods=["PUT"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_machine_set_status(machine_id):
    d = request.get_json(silent=True) or {}
    new_status = d.get("status", "")
    valid_ops = {"free", "idle", "maintenance"}

    if new_status not in valid_ops:
        return jresp({"error": f"Invalid status. Allowed: {', '.join(valid_ops)}"}, 400)

    machine = query(
        "SELECT status FROM machines WHERE machine_id=%s", (machine_id,), one=True)
    if not machine:
        return jresp({"error": "Machine not found"}, 404)
    if machine["status"] == "busy":
        return jresp({"error": "Cannot change status of a machine with an active order"}, 409)

    query(
        "UPDATE machines SET status=%s WHERE machine_id=%s",
        (new_status, machine_id), commit=True
    )
    log_audit(session["full_name"], f"machine_status_{new_status}",
              f"machine_id={machine_id}", request.remote_addr)
    return jresp({"ok": True, "status": new_status})


# ════════════════════════════════════════════════════════════════
#  API — OPERATOR ISSUE REPORTS
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/issues")
@role_required("staff", "admin", "superadmin")
def api_staff_issues():
    """Operator's own submitted issues."""
    rows = query(
        "SELECT * FROM issues WHERE reported_by=%s ORDER BY reported_at DESC",
        (session["user_id"],)
    )
    return jresp(rows or [])


@app.route("/api/staff/issues/report", methods=["POST"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_report_issue():
    """
    Operator submits an issue report.
    Stores reporter_name so admin panel can display it without a JOIN.
    """
    d = request.get_json(silent=True) or {}
    issue_type = d.get("issue_type", "other")
    order_id = d.get("order_id") or None
    desc = d.get("description", "").strip()

    if not desc:
        return jresp({"error": "Description required"}, 400)

    reporter_name = session.get("full_name", "Unknown Operator")

    query(
        "INSERT INTO issues "
        "(issue_type, order_id, description, reported_by, reporter_name, reported_at) "
        "VALUES (%s,%s,%s,%s,%s,NOW())",
        (issue_type, order_id, desc,
         session["user_id"], reporter_name), commit=True
    )
    log_audit(reporter_name, "report_issue", issue_type, request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  STAGE ADVANCEMENT LOGIC
# ════════════════════════════════════════════════════════════════

def _advance_stages_logic():
    now = datetime.now()
    advanced = 0

    def _free_machines(oid):
        query(
            "UPDATE machines SET status='free', current_order_id=NULL, "
            "current_stage=NULL, stage_ends_at=NULL "
            "WHERE current_order_id=%s", (oid,), commit=True
        )

    def _set_stage(oid, stage, ends_at):
        query(
            "UPDATE orders SET status=%s, stage_ends_at=%s WHERE order_id=%s",
            (stage, ends_at, oid), commit=True
        )
        query(
            "UPDATE machines SET current_stage=%s, stage_ends_at=%s "
            "WHERE current_order_id=%s",
            (stage, ends_at, oid), commit=True
        )

    # Washing → next
    for o in (query(
        "SELECT * FROM orders WHERE status='washing' AND stage_ends_at <= %s", (
            now,)
    ) or []):
        if o.get("with_dryer"):
            _set_stage(o["order_id"], "drying", now +
                       timedelta(seconds=DRY_SECS))
        elif o.get("with_downy"):
            _set_stage(o["order_id"], "downy",  now +
                       timedelta(seconds=DOWNY_SECS))
        else:
            query(
                "UPDATE orders SET status='folding', stage_ends_at=NULL, fold_ends_at=NULL "
                "WHERE order_id=%s", (o["order_id"],), commit=True
            )
            _free_machines(o["order_id"])
        advanced += 1

    # Drying → next
    for o in (query(
        "SELECT * FROM orders WHERE status='drying' AND stage_ends_at <= %s", (
            now,)
    ) or []):
        if o.get("with_downy"):
            _set_stage(o["order_id"], "downy", now +
                       timedelta(seconds=DOWNY_SECS))
        else:
            query(
                "UPDATE orders SET status='folding', stage_ends_at=NULL, fold_ends_at=NULL "
                "WHERE order_id=%s", (o["order_id"],), commit=True
            )
            _free_machines(o["order_id"])
        advanced += 1

    # Downy → folding
    for o in (query(
        "SELECT * FROM orders WHERE status='downy' AND stage_ends_at <= %s", (
            now,)
    ) or []):
        query(
            "UPDATE orders SET status='folding', stage_ends_at=NULL, fold_ends_at=NULL "
            "WHERE order_id=%s", (o["order_id"],), commit=True
        )
        _free_machines(o["order_id"])
        advanced += 1

    return advanced


@app.route("/api/internal/advance-stages", methods=["POST"])
def api_advance_stages():
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if token != INTERNAL_SECRET:
        return jresp({"error": "Forbidden"}, 403)
    advanced = _advance_stages_logic()
    return jresp({"ok": True, "advanced": advanced})


# ════════════════════════════════════════════════════════════════
#  API — PROMO VALIDATION
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff/validate-promo/<code>")
@role_required("staff", "admin", "superadmin")
def api_staff_validate_promo(code):
    promo = query(
        "SELECT * FROM promos WHERE code=%s AND is_active=1", (code.upper(),), one=True
    )
    if promo:
        return jresp({"valid": True, "discount": promo["discount"]})
    return jresp({"valid": False, "error": "Invalid or expired promo"}, 404)


# ════════════════════════════════════════════════════════════════
#  API — CUSTOMERS  (active / non-archived)
# ════════════════════════════════════════════════════════════════

@app.route("/api/customers")
@role_required("admin", "superadmin")
def api_customers():
    rows = query(
        "SELECT user_id, full_name, email, phone, status, created_at "
        "FROM users WHERE role='customer' AND (is_archived=0 OR is_archived IS NULL) "
        "ORDER BY created_at DESC"
    )
    return jresp(rows or [])


@app.route("/api/customers/search")
@role_required("staff", "admin", "superadmin")
def api_customers_search():
    """Autocomplete for operator encode form."""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        return jresp([])
    like = f"%{q}%"
    rows = query(
        "SELECT user_id, full_name, email FROM users "
        "WHERE role='customer' AND status='active' "
        "  AND (is_archived=0 OR is_archived IS NULL) "
        "  AND (full_name LIKE %s OR email LIKE %s) "
        "ORDER BY full_name ASC LIMIT 10",
        (like, like)
    ) or []
    return jresp(rows)


@app.route("/api/customers/block/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
def api_block_customer(uid):
    query("UPDATE users SET status='blocked' WHERE user_id=%s AND role='customer'",
          (uid,), commit=True)
    log_audit(session["full_name"], "block_customer",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/customers/unblock/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
def api_unblock_customer(uid):
    query("UPDATE users SET status='active' WHERE user_id=%s AND role='customer'",
          (uid,), commit=True)
    log_audit(session["full_name"], "unblock_customer",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


# ── Customer Archive ───────────────────────────────────────────

@app.route("/api/customers/archive/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
@_require_json_or_xhr
def api_customer_archive(uid):
    query(
        "UPDATE users SET is_archived=1, archived_at=NOW() "
        "WHERE user_id=%s AND role='customer'",
        (uid,), commit=True
    )
    log_audit(session["full_name"], "archive_customer",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/customers/unarchive/<int:uid>", methods=["PUT"])
@role_required("admin", "superadmin")
@_require_json_or_xhr
def api_customer_unarchive(uid):
    query(
        "UPDATE users SET is_archived=0, archived_at=NULL, status='active' "
        "WHERE user_id=%s AND role='customer'",
        (uid,), commit=True
    )
    log_audit(session["full_name"], "unarchive_customer",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/customers/archived")
@role_required("admin", "superadmin")
def api_customers_archived():
    rows = query(
        "SELECT user_id, full_name, email, phone, status, archived_at, created_at "
        "FROM users WHERE role='customer' AND is_archived=1 "
        "ORDER BY archived_at DESC"
    ) or []
    return jresp(rows)


@app.route("/api/customers/delete/<int:uid>", methods=["DELETE"])
@role_required("admin", "superadmin")
def api_customer_delete_permanent(uid):
    row = query(
        "SELECT user_id FROM users WHERE user_id=%s AND role='customer' AND is_archived=1",
        (uid,), one=True
    )
    if not row:
        return jresp({"error": "Customer not found or not archived"}, 404)
    query("UPDATE orders     SET customer_id=NULL WHERE customer_id=%s",
          (uid,), commit=True)
    query("DELETE FROM feedbacks WHERE customer_id=%s", (uid,), commit=True)
    query("DELETE FROM password_resets WHERE user_id=%s", (uid,), commit=True)
    query("DELETE FROM users WHERE user_id=%s", (uid,), commit=True)
    log_audit(session["full_name"], "delete_customer_permanent",
              f"id={uid}", request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — PRICING
# ════════════════════════════════════════════════════════════════

@app.route("/api/pricing")
@login_required
def api_pricing():
    rows = query("SELECT * FROM services WHERE is_active=1 ORDER BY id") or []
    if not rows:
        for key, val in SERVICE_RATES.items():
            query(
                "INSERT IGNORE INTO services (service_key, name, price) VALUES (%s,%s,%s)",
                (key, val["label"], val["rate"]), commit=True
            )
        rows = query(
            "SELECT * FROM services WHERE is_active=1 ORDER BY id") or []
    return jresp(rows)


@app.route("/api/pricing/update/<int:sid>", methods=["PUT"])
@role_required("admin", "superadmin")
def api_pricing_update(sid):
    d = request.get_json(silent=True) or {}
    price = float(d.get("price", 0))
    if price < 0:
        return jresp({"error": "Price must be non-negative"}, 400)

    query("UPDATE services SET price=%s WHERE id=%s", (price, sid), commit=True)
    row = query("SELECT service_key FROM services WHERE id=%s",
                (sid,), one=True)
    if row and row["service_key"] in SERVICE_RATES:
        SERVICE_RATES[row["service_key"]]["rate"] = price

    log_audit(session["full_name"], "update_price",
              f"service_id={sid} price={price}", request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — PROMOS
# ════════════════════════════════════════════════════════════════

@app.route("/api/promos")
@login_required
def api_promos():
    rows = query(
        "SELECT * FROM promos WHERE is_active=1 ORDER BY created_at DESC")
    return jresp(rows or [])


@app.route("/api/promos/add", methods=["POST"])
@role_required("admin", "superadmin")
def api_promo_add():
    d = request.get_json(silent=True) or {}
    code = d.get("code",    "").strip().upper()
    discount = int(d.get("discount", 0))

    if not code or not (1 <= discount <= 100):
        return jresp({"error": "Invalid promo data"}, 400)
    if query("SELECT promo_id FROM promos WHERE code=%s AND is_active=1", (code,), one=True):
        return jresp({"error": "Promo code already exists"}, 409)

    query("INSERT INTO promos (code, discount, is_active) VALUES (%s,%s,1)",
          (code, discount), commit=True)
    log_audit(session["full_name"], "add_promo", code, request.remote_addr)
    return jresp({"ok": True})


@app.route("/api/promos/delete/<int:pid>", methods=["DELETE"])
@role_required("admin", "superadmin")
def api_promo_delete(pid):
    query("UPDATE promos SET is_active=0 WHERE promo_id=%s", (pid,), commit=True)
    log_audit(session["full_name"], "delete_promo",
              f"id={pid}", request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — CUSTOMER PORTAL
# ════════════════════════════════════════════════════════════════

def _enrich_order_timer(o):
    if o.get("stage_ends_at") and o.get("status") in ("washing", "drying", "downy"):
        now = datetime.now()
        rem = (o["stage_ends_at"] - now).total_seconds()
        stage_total = {
            "washing": WASH_SECS, "drying": DRY_SECS, "downy": DOWNY_SECS,
        }.get(o["status"], WASH_SECS)
        o["remaining_seconds"] = max(0, int(rem))
        o["progress_pct"] = max(0, min(100, int((1 - rem/stage_total)*100)))


@app.route("/api/customer/dashboard")
@role_required("customer")
def api_customer_dashboard():
    uid = session["user_id"]

    active_orders = query(
        """SELECT o.*, o.customer_email,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.customer_id=%s
             AND o.status NOT IN ('completed','done','cancelled')
           ORDER BY o.created_at ASC""",
        (uid,)
    ) or []

    for o in active_orders:
        o["service_type_label"] = SERVICE_RATES.get(
            o["service_type"], {}
        ).get("label", o["service_type"])
        _enrich_order_timer(o)

    stats = query(
        "SELECT COUNT(*) AS done, COALESCE(SUM(amount),0) AS total_spent "
        "FROM orders WHERE customer_id=%s AND status IN ('completed','done')",
        (uid,), one=True
    )
    active_count = len(active_orders)
    active_order = active_orders[0] if active_orders else None

    return jresp({
        "active_order":  active_order,
        "active_orders": active_orders,
        "stats": {
            "active":      active_count,
            "done":        stats["done"] if stats else 0,
            "total_spent": float(stats["total_spent"] if stats else 0),
        }
    })


@app.route("/api/customer/active-order")
@role_required("customer")
def api_customer_active():
    uid = session["user_id"]
    order = query(
        """SELECT o.*, o.customer_email,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.customer_id=%s
             AND o.status NOT IN ('completed','done','cancelled')
           ORDER BY o.created_at ASC LIMIT 1""",
        (uid,), one=True
    )
    if order:
        order["service_type_label"] = SERVICE_RATES.get(
            order["service_type"], {}
        ).get("label", order["service_type"])
        _enrich_order_timer(order)
    return jresp(order or {})


@app.route("/api/customer/active-orders")
@role_required("customer")
def api_customer_active_orders():
    uid = session["user_id"]
    rows = query(
        """SELECT o.*, o.customer_email,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.customer_id=%s
             AND o.status NOT IN ('completed','done','cancelled')
           ORDER BY o.created_at ASC""",
        (uid,)
    ) or []
    for o in rows:
        o["service_type_label"] = SERVICE_RATES.get(
            o["service_type"], {}
        ).get("label", o["service_type"])
        _enrich_order_timer(o)
    return jresp(rows)


@app.route("/api/customer/orders")
@role_required("customer")
def api_customer_orders():
    uid = session["user_id"]
    rows = query(
        """SELECT o.*, o.customer_email,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.customer_id=%s
           ORDER BY o.created_at DESC""",
        (uid,)
    ) or []
    for o in rows:
        o["service_type_label"] = SERVICE_RATES.get(
            o["service_type"], {}
        ).get("label", o["service_type"])
    return jresp(rows)


@app.route("/api/customer/track/<path:tracking_id>")
@login_required
def api_track_order(tracking_id):
    order = query(
        """SELECT o.*,
                  COALESCE(u.full_name, o.customer_name_walk_in) AS customer_name
           FROM orders o
           LEFT JOIN users u ON o.customer_id=u.user_id
           WHERE o.tracking_id=%s""",
        (tracking_id,), one=True
    )
    if not order:
        return jresp({"error": "Order not found"}, 404)

    role = session.get("role")
    if role == "customer" and order.get("customer_id") != session["user_id"]:
        return jresp({"error": "Forbidden"}, 403)

    order["service_type_label"] = SERVICE_RATES.get(
        order["service_type"], {}
    ).get("label", order["service_type"])
    _enrich_order_timer(order)
    return jresp(order)


@app.route("/api/customer/order", methods=["POST"])
@role_required("customer")
def api_customer_place_order():
    uid = session["user_id"]
    d = request.get_json(silent=True) or {}
    service_id = d.get("service_id")
    weight = float(d.get("weight_kg", 0))
    with_downy = bool(d.get("with_downy", False))
    promo_code = d.get("promo_code", "").strip(
    ).upper() if d.get("promo_code") else None

    if not service_id or weight < 0.5:
        return jresp({"error": "Invalid service or weight"}, 400)

    svc = query("SELECT * FROM services WHERE id=%s AND is_active=1",
                (service_id,), one=True)
    if not svc:
        return jresp({"error": "Service not found"}, 404)

    discount_pct = 0
    if promo_code:
        promo = query("SELECT * FROM promos WHERE code=%s AND is_active=1",
                      (promo_code,), one=True)
        if not promo:
            return jresp({"error": "Invalid or expired promo code"}, 400)
        discount_pct = promo["discount"]

    svc_key = svc["service_key"]
    amount = calc_amount(svc_key, weight, False, discount_pct)
    m_needed = machines_needed(weight)
    tracking = generate_tracking_id()

    oid = query(
        """INSERT INTO orders
           (tracking_id, customer_id, service_type, weight_kg, with_downy,
            amount, machines_needed, promo_code, discount_pct, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
        (tracking, uid, svc_key, weight, with_downy,
         amount, m_needed, promo_code, discount_pct),
        commit=True
    )
    log_audit(session["full_name"], "place_order",
              tracking, request.remote_addr)
    return jresp({"ok": True, "tracking_id": tracking, "order_id": oid, "amount": amount})


@app.route("/api/customer/validate-promo/<code>")
@role_required("customer")
def api_validate_promo(code):
    promo = query(
        "SELECT * FROM promos WHERE code=%s AND is_active=1", (code.upper(),), one=True
    )
    if promo:
        return jresp({"valid": True, "discount": promo["discount"]})
    return jresp({"valid": False, "error": "Invalid or expired promo"}, 404)


@app.route("/api/customer/reavail", methods=["POST"])
@role_required("customer")
def api_reavail():
    d = request.get_json(silent=True) or {}
    order_id = d.get("order_id")
    original = query(
        "SELECT * FROM orders WHERE order_id=%s AND customer_id=%s",
        (order_id, session["user_id"]), one=True
    )
    if not original:
        return jresp({"error": "Order not found"}, 404)

    tracking = generate_tracking_id()
    oid = query(
        """INSERT INTO orders
           (tracking_id, customer_id, service_type, weight_kg,
            with_downy, amount, machines_needed, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'pending')""",
        (tracking, session["user_id"],
         original["service_type"], original["weight_kg"],
         original["with_downy"], original["amount"], original["machines_needed"]),
        commit=True
    )
    log_audit(session["full_name"], "reavail", tracking, request.remote_addr)
    return jresp({"ok": True, "tracking_id": tracking, "order_id": oid})


@app.route("/api/customer/feedbacks")
@role_required("customer")
def api_customer_feedbacks():
    rows = query(
        """SELECT f.*, o.tracking_id
           FROM feedbacks f
           LEFT JOIN orders o ON f.order_id=o.order_id
           WHERE f.customer_id=%s
           ORDER BY f.created_at DESC""",
        (session["user_id"],)
    )
    return jresp(rows or [])


@app.route("/api/customer/feedback", methods=["POST"])
@role_required("customer")
def api_submit_feedback():
    d = request.get_json(silent=True) or {}
    order_id = d.get("order_id")
    rating = int(d.get("rating", 0))
    comment = d.get("comment", "").strip()

    if not order_id or not (1 <= rating <= 5):
        return jresp({"error": "Invalid feedback data"}, 400)

    order = query(
        "SELECT order_id FROM orders "
        "WHERE order_id=%s AND customer_id=%s AND status IN ('completed','done')",
        (order_id, session["user_id"]), one=True
    )
    if not order:
        return jresp({"error": "Order not found or not completed"}, 404)

    existing = query(
        "SELECT feedback_id FROM feedbacks WHERE order_id=%s AND customer_id=%s",
        (order_id, session["user_id"]), one=True
    )
    if existing:
        return jresp({"error": "Feedback already submitted for this order"}, 409)

    query(
        "INSERT INTO feedbacks (order_id, customer_id, rating, comment) VALUES (%s,%s,%s,%s)",
        (order_id, session["user_id"], rating, comment), commit=True
    )
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  CLI COMMANDS
# ════════════════════════════════════════════════════════════════

@app.cli.command("create-superadmin")
@with_appcontext
def create_superadmin_cmd():
    SA_EMAIL = os.getenv("SA_EMAIL",    "superadmin@laundry.com")
    SA_PASSWORD = os.getenv("SA_PASSWORD", "StrongPass#2026!")
    SA_NAME = "Super Admin"

    existing = query("SELECT user_id FROM users WHERE email=%s",
                     (SA_EMAIL,), one=True)
    if existing:
        click.echo("⚠️  Superadmin already exists.")
        return

    query(
        "INSERT INTO users (full_name, username, email, phone, password_hash, role, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (SA_NAME, "superadmin", SA_EMAIL, "09123456789",
         generate_password_hash(SA_PASSWORD), "superadmin", "active"),
        commit=True
    )
    click.echo("✅ Superadmin created successfully!")


@app.cli.command("recover-superadmin")
@with_appcontext
def recover_superadmin_cmd():
    import getpass

    SA_EMAIL = os.getenv("SA_EMAIL", "superadmin@laundry.com")

    click.echo("\n╔══════════════════════════════════════════╗")
    click.echo("║   LAUNDRY LOUNGE — SUPERADMIN RECOVERY   ║")
    click.echo("╚══════════════════════════════════════════╝")
    click.echo(f"  Account : {SA_EMAIL}\n")

    user = query(
        "SELECT user_id, full_name FROM users WHERE email=%s AND role='superadmin'",
        (SA_EMAIL,), one=True
    )
    if not user:
        click.echo("❌  Superadmin account not found.")
        return

    click.echo(f"  Found   : {user['full_name']} (id={user['user_id']})\n")

    while True:
        new_pw = getpass.getpass("  New password (min 8 chars): ")
        if len(new_pw) < 8:
            click.echo("  ⚠️  Too short. Try again.")
            continue
        confirm = getpass.getpass("  Confirm new password      : ")
        if new_pw != confirm:
            click.echo("  ⚠️  Passwords do not match. Try again.")
            continue
        break

    query(
        "UPDATE users SET password_hash=%s WHERE user_id=%s",
        (generate_password_hash(new_pw), user["user_id"]), commit=True
    )
    query("DELETE FROM password_resets WHERE user_id=%s",
          (user["user_id"],), commit=True)
    ts = str(datetime.now().timestamp())
    query(
        "INSERT INTO system_settings (setting_key, setting_value) VALUES ('force_logout_ts',%s) "
        "ON DUPLICATE KEY UPDATE setting_value=%s",
        (ts, ts), commit=True
    )

    click.echo("\n✅  Superadmin password updated successfully.")
    click.echo("    All active sessions invalidated.\n")


# ════════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jresp({"error": "Not found"}, 404)
    return render_template("login.html"), 404


@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith("/api/"):
        return jresp({"error": "Forbidden"}, 403)
    flash("Access denied.", "error")
    return redirect(url_for("login"))


@app.errorhandler(500)
def server_error(e):
    return jresp({"error": "Internal server error"}, 500)


# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
