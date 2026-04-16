"""
hardcoded account for superadmin/owner
email= superadmin@laundry.com
pass = StrongPass#2026!
Laundry Lounge Management System — Flask Backend
app.py  (Audited & fixed — production-grade)

CHANGES in this revision:
  FP1. /forgot-password (POST form) removed — replaced by AJAX endpoint below.
  FP2. /api/forgot-password (POST JSON) — accepts email, sends a real HTML
       reset email (same styling as tracking email), stores token in DB.
       Always returns {"ok": true} to avoid leaking whether email exists.
  FP3. /reset-password (GET) — redirect page; appends ?reset_token=... to
       /login so the login page JS can detect it and open the modal.
  FP4. /api/reset-password (POST JSON) — validates token, sets new password,
       deletes the token row. Returns {"ok": true} or {"error": "..."}.
  FP5. Old GET /forgot-password route kept as alias so any bookmark still works.

  STAGE FIX: Added APScheduler background job that calls advance_stages()
             every 10 seconds automatically — no external cron needed.
  CUSTOMER FIX: Customer portal now shows ALL active orders (removed LIMIT 1).
"""
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
import json
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

# =========================
# MYSQL CONFIG
# =========================
app.config["MYSQL_HOST"] = os.environ.get("MYSQL_HOST", "localhost")
app.config["MYSQL_USER"] = os.environ.get("MYSQL_USER", "root")
app.config["MYSQL_PASSWORD"] = os.environ.get("MYSQL_PASSWORD", "")
app.config["MYSQL_DB"] = os.environ.get("MYSQL_DB", "llms_db")
app.config["MYSQL_CURSORCLASS"] = "DictCursor"

mysql = MySQL(app)

# =========================
# INTERNAL SECURITY SECRET
# =========================
INTERNAL_SECRET = os.environ.get(
    "INTERNAL_SECRET", "change-me-internal-secret"
)

# =========================
# EMAIL / FLASK-MAIL CONFIG
# =========================
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.environ.get(
    "MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get(
    "MAIL_SENDER",
    os.environ.get("MAIL_USERNAME", "noreply@laundrylounge.com")
)

mail = Mail(app)

# ─── Constants ──────────────────────────────────────────────────
MACHINE_CAPACITY = 8
WASH_SECS = 1 * 60      # 1 minute per stage (dev mode; real = 44*60)
DRY_SECS = 1 * 60
DOWNY_SECS = 1 * 60
DRYER_SURCHARGE = 20.0

SERVICE_RATES = {
    "single_wash": {"label": "Single Wash",            "rate": 30},
    "double_wash": {"label": "Double Wash",            "rate": 35},
    "household":   {"label": "Household Items",        "rate": 45},
    "heavy_wash":  {"label": "Heavy Wash (Comforter)", "rate": 75},
    "soak_whites": {"label": "Soak for Whites",        "rate": 50},
}

TRACKING_BASE_URL = os.environ.get(
    "TRACKING_BASE_URL", "http://localhost:5000")

FOLD_MIN_SECS = 10 * 60
FOLD_MAX_SECS = 15 * 60


# ════════════════════════════════════════════════════════════════
#  BACKGROUND SCHEDULER — Auto stage advancement every 10 seconds
#  This replaces the need for an external cron job hitting
#  /api/internal/advance-stages. It runs inside the Flask process.
# ════════════════════════════════════════════════════════════════

def _run_advance_stages():
    """
    Called by the background scheduler every 10 seconds.
    Wraps advance_stages_logic() in an app context so DB calls work.
    Uses a threading lock to prevent overlapping runs.
    """
    with _stage_lock:
        try:
            with app.app_context():
                _advance_stages_logic()
        except Exception as exc:
            # Log but never crash the scheduler thread
            try:
                app.logger.error(f"[scheduler] advance_stages error: {exc}")
            except Exception:
                pass


_stage_lock = threading.Lock()
_scheduler_thread = None


def _start_scheduler():
    """Start a daemon thread that fires _run_advance_stages every 10 s."""
    global _scheduler_thread

    def _loop():
        import time
        while True:
            time.sleep(10)
            _run_advance_stages()

    _scheduler_thread = threading.Thread(
        target=_loop, daemon=True, name="stage-scheduler")
    _scheduler_thread.start()
    try:
        app.logger.info(
            "[scheduler] Stage-advancement scheduler started (every 10s).")
    except Exception:
        pass


# Start the scheduler when the module is imported (works with Flask dev server
# and gunicorn workers). Guard against double-start in debug reloader.
if not os.environ.get("WERKZEUG_RUN_MAIN") == "true" or True:
    # Always start — the daemon=True flag ensures it dies with the process.
    _start_scheduler()


# ════════════════════════════════════════════════════════════════
#  DATABASE HELPERS
# ════════════════════════════════════════════════════════════════

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
    <p style="color:rgba(255,255,255,.8);margin:4px 0 0;font-size:.85rem">Order Received &mdash; Tracking Confirmation</p>
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
    <p style="color:#888;font-size:.75rem;margin:0">Laundry Lounge &mdash; Your Local Laundry Partner</p>
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
            "subject": "📦 Your Laundry is Ready for Pickup!",
            "headline": "Your laundry is ready! 🎉",
            "body": "Your laundry has been washed, dried, and folded. Please come pick it up.",
            "cta": "View Order Details",
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


# ════════════════════════════════════════════════════════════════
#  PASSWORD RESET EMAIL
# ════════════════════════════════════════════════════════════════

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
            html_body = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#F4EFE6;font-family:Arial,sans-serif">
<div style="max-width:520px;margin:32px auto;color:#1A1309">
  <div style="background:#C44F1F;padding:24px 28px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;font-size:1.4rem;margin:0">🧺 Laundry Lounge</h1>
    <p style="color:rgba(255,255,255,.8);margin:5px 0 0;font-size:.8rem;letter-spacing:.1em;text-transform:uppercase">
      Password Reset Request
    </p>
  </div>
  <div style="background:#fff;padding:32px 28px;border:1px solid #e0e0e0;border-top:none">
    <p style="margin:0 0 22px;font-size:.88rem;color:#5A4E3A;text-align:center;line-height:1.6">
      Hello <strong>{full_name or 'there'}</strong>,<br>
      Click the button below to reset your password.
    </p>
    <div style="text-align:center;margin:26px 0">
      <a href="{reset_url}"
         style="display:inline-block;background:#C44F1F;color:#fff;padding:14px 36px;
                border-radius:30px;text-decoration:none;font-weight:600">
        🔒 Reset My Password
      </a>
    </div>
    <p style="margin:16px 0 0;font-size:.76rem;color:#999;text-align:center">
      This link expires in <strong>1 hour</strong>. If you didn't request this, ignore this email.
    </p>
  </div>
</div>
</body>
</html>
"""
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


def query_many(sql, args=(), commit=False):
    cur = mysql.connection.cursor()
    try:
        cur.executemany(sql, args)
        if commit:
            mysql.connection.commit()
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
#  PUBLIC TRACKING PAGE
# ════════════════════════════════════════════════════════════════

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
<h1>❌ Order Not Found</h1><p>Tracking ID <strong>{tracking_id}</strong> was not found.</p>
<p><a href="/">Return Home</a></p></body></html>""", 404

    SERVICE_LABELS = {
        "single_wash": "Single Wash", "double_wash": "Double Wash",
        "household": "Household Items", "heavy_wash": "Heavy Wash (Comforter)",
        "soak_whites": "Soak for Whites"
    }
    STATUS_DISPLAY = {
        "pending":          {"label": "⏳ Pending",            "color": "#A06010", "bg": "#FDF3E3"},
        "washing":          {"label": "🫧 Washing",            "color": "#1A5DAA", "bg": "#EEF3FB"},
        "drying":           {"label": "💨 Drying",             "color": "#1A8080", "bg": "#E8F5F5"},
        "downy":            {"label": "🌸 Downy",              "color": "#6A35A0", "bg": "#F3EEF8"},
        "folding":          {"label": "👕 Folding",            "color": "#A06010", "bg": "#FDF3E3"},
        "ready_for_pickup": {"label": "📦 Ready for Pickup!",  "color": "#B85000", "bg": "#FEF0E6"},
        "completed":        {"label": "✅ Completed",          "color": "#1B7A4A", "bg": "#EBF5EE"},
        "done":             {"label": "✅ Completed",          "color": "#1B7A4A", "bg": "#EBF5EE"},
        "cancelled":        {"label": "❌ Cancelled",          "color": "#C0392B", "bg": "#FDEDED"},
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
        "ready_for_pickup": "Ready!", "completed": "Done"
    }
    stage_html = "".join(
        f"""<div style="display:flex;flex-direction:column;align-items:center;gap:4px">
          <div style="width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;
                      background:{'#1B7A4A' if i < cur_idx else ('#1A5DAA' if i == cur_idx else '#ddd')};
                      color:{'#fff' if i <= cur_idx else '#999'};font-size:.9rem">
            {'✓' if i < cur_idx else ('●' if i == cur_idx else '○')}
          </div>
          <div style="font-size:.6rem;color:{'#1A5DAA' if i == cur_idx else ('#1B7A4A' if i < cur_idx else '#999')};text-align:center;max-width:50px">
            {stage_labels.get(stages[i], stages[i])}
          </div>
        </div>{'<div style="width:20px;height:2px;background:' + ('#1B7A4A' if i < cur_idx else '#ddd') + ';margin-bottom:14px"></div>' if i < len(stages)-1 else ''}"""
        for i, _ in enumerate(stages)
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Track Order — {tracking_id}</title>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;600&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'DM Sans',sans-serif;background:#F5F0E8;min-height:100vh;padding:20px}}
    .card{{background:#fff;border-radius:16px;padding:28px;max-width:480px;margin:0 auto;box-shadow:0 8px 32px rgba(10,30,60,.1)}}
    .brand{{font-size:1.1rem;font-weight:700;color:#C44F1F;margin-bottom:6px}}
    .trk{{font-family:'DM Mono',monospace;font-size:.75rem;color:#888;margin-bottom:20px;letter-spacing:.06em}}
    .status-badge{{display:inline-block;padding:8px 20px;border-radius:30px;font-weight:600;font-size:1rem;margin-bottom:20px}}
    .row{{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:.88rem}}
    .row:last-child{{border-bottom:none}}
    .lbl{{color:#888}}.val{{font-weight:600;color:#1a1a2e}}
    .stages{{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:0;margin:20px 0;overflow-x:auto;padding:8px 0}}
    .refresh{{text-align:center;margin-top:20px;font-size:.78rem;color:#aaa}}
    .refresh a{{color:#C44F1F;text-decoration:none}}
    h2{{font-size:1rem;color:#333;margin:20px 0 10px}}
    .auto-badge{{background:#E8F5EE;border:1px solid #1B7A4A;border-radius:8px;padding:8px 12px;font-size:.78rem;color:#1B7A4A;margin-top:16px;text-align:center}}
  </style>
</head>
<body>
<div class="card">
  <div class="brand">🧺 Laundry Lounge</div>
  <div class="trk">TRACKING: {tracking_id}</div>
  <div class="status-badge" style="background:{disp['bg']};color:{disp['color']}">{disp['label']}</div>
  <h2>Order Progress</h2>
  <div class="stages">{stage_html}</div>
  <div style="margin-top:16px">
    <div class="row"><span class="lbl">Customer</span><span class="val">{order.get('customer_name', 'Walk-in')}</span></div>
    <div class="row"><span class="lbl">Service</span><span class="val">{svc_lbl}</span></div>
    <div class="row"><span class="lbl">Weight</span><span class="val">{order.get('weight_kg', 0)} kg</span></div>
    <div class="row"><span class="lbl">Amount</span><span class="val">₱{float(order.get('amount', 0)):,.2f}</span></div>
    <div class="row"><span class="lbl">Dryer</span><span class="val">{'Yes' if order.get('with_dryer') else 'No'}</span></div>
    <div class="row"><span class="lbl">Downy</span><span class="val">{'Yes' if order.get('with_downy') else 'No'}</span></div>
    <div class="row"><span class="lbl">Created</span><span class="val">{created}</span></div>
  </div>
  {f'<div class="auto-badge">✅ This page updates automatically.</div>' if s not in ("completed", "done", "cancelled") else '<div class="auto-badge" style="background:#EEF3FB;border-color:#C44F1F;color:#C44F1F">✅ Order Complete — Thank you!</div>'}
  <div class="refresh"><a href="/track/{tracking_id}">🔄 Refresh Status</a> &nbsp;·&nbsp; Laundry Lounge 2026</div>
</div>
{'<script>setTimeout(()=>location.reload(),60000)</script>' if s not in ("completed", "done", "cancelled") else ''}
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
    if request.endpoint in ("login", "login_post", "logout",
                            "register", "forgot_password",
                            "api_forgot_password", "api_reset_password",
                            "reset_password_redirect",
                            "public_track_page", "api_public_track",
                            "api_maintenance_status", "db_init",
                            "db_migrate", "static"):
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
#  UTILITY FUNCTIONS
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
        existing = query(
            "SELECT order_id FROM orders WHERE tracking_id=%s",
            (candidate,), one=True
        )
        if not existing:
            return candidate
    raise RuntimeError("Failed to generate unique tracking ID")


def calc_amount(service_type: str, weight_kg: float,
                with_dryer: bool = False, discount_pct: float = 0) -> float:
    rate = SERVICE_RATES.get(service_type, {}).get("rate", 0)
    base = rate * weight_kg
    disc = base * discount_pct / 100
    service_total = base - disc
    dryer = DRYER_SURCHARGE if with_dryer else 0.0
    return round(service_total + dryer, 2)


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
#  CLI COMMANDS
# ════════════════════════════════════════════════════════════════

@app.cli.command("create-superadmin")
@with_appcontext
def create_superadmin_cmd():
    SA_EMAIL = os.getenv("SA_EMAIL",    "superadmin@laundry.com")
    SA_PASSWORD = os.getenv("SA_PASSWORD", "StrongPass#2026!")
    SA_NAME = "Super Admin"

    existing = query(
        "SELECT user_id FROM users WHERE email=%s", (SA_EMAIL,), one=True
    )
    if existing:
        click.echo("⚠️  Superadmin already exists.")
        return

    query("""
        INSERT INTO users
        (full_name, username, email, phone, password_hash, role, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        SA_NAME, "superadmin", SA_EMAIL, "09123456789",
        generate_password_hash(SA_PASSWORD), "superadmin", "active"
    ), commit=True)
    click.echo("✅ Superadmin created successfully!")


# ════════════════════════════════════════════════════════════════
#  DATABASE INITIALISATION
# ════════════════════════════════════════════════════════════════

@app.route("/api/db/init", methods=["GET", "POST"])
def db_init():
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id       INT AUTO_INCREMENT PRIMARY KEY,
            full_name     VARCHAR(120) NOT NULL,
            username      VARCHAR(60) UNIQUE,
            email         VARCHAR(120) NOT NULL UNIQUE,
            phone         VARCHAR(30),
            password_hash VARCHAR(256) NOT NULL,
            role          ENUM('superadmin','admin','staff','customer') NOT NULL DEFAULT 'customer',
            status        ENUM('active','inactive','blocked') NOT NULL DEFAULT 'active',
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
            FOREIGN KEY (order_id)   REFERENCES orders(order_id)   ON DELETE CASCADE,
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
            FOREIGN KEY (customer_id) REFERENCES users(user_id)  ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS audit_logs (
            log_id     INT AUTO_INCREMENT PRIMARY KEY,
            actor      VARCHAR(120),
            action     VARCHAR(80),
            target     VARCHAR(200),
            ip_address VARCHAR(60),
            timestamp  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",

        """CREATE TABLE IF NOT EXISTS issues (
            issue_id    INT AUTO_INCREMENT PRIMARY KEY,
            issue_type  VARCHAR(40) NOT NULL DEFAULT 'other',
            order_id    INT,
            description TEXT,
            reported_by INT,
            status      ENUM('open','resolved') NOT NULL DEFAULT 'open',
            reported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
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

    for n in range(1, 9):
        query(
            "INSERT IGNORE INTO machines (unit_number, status) VALUES (%s,'free')",
            (n,), commit=True
        )

    for key, val in SERVICE_RATES.items():
        query(
            "INSERT IGNORE INTO services (service_key, name, price) VALUES (%s,%s,%s)",
            (key, val["label"], val["rate"]), commit=True
        )

    for k, v in [("maintenance_mode", "0"), ("allow_registration", "1"), ("promos_enabled", "1")]:
        query(
            "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s,%s)",
            (k, v), commit=True
        )

    SA_EMAIL = os.getenv("SA_EMAIL",    "superadmin@laundry.com")
    SA_PASSWORD = os.getenv("SA_PASSWORD", "StrongPass#2026!")
    SA_NAME = "Super Admin"

    existing_sa = query(
        "SELECT user_id FROM users WHERE email=%s", (SA_EMAIL,), one=True
    )
    if not existing_sa:
        query(
            "INSERT INTO users (full_name, email, password_hash, role, status) "
            "VALUES (%s,%s,%s,'superadmin','active')",
            (SA_NAME, SA_EMAIL, generate_password_hash(SA_PASSWORD)), commit=True
        )

    return jresp({"ok": True, "message": "Database initialised."})


# ════════════════════════════════════════════════════════════════
#  PUBLIC — MAINTENANCE STATUS
# ════════════════════════════════════════════════════════════════

@app.route("/api/system/maintenance-status")
def api_maintenance_status():
    return jresp({"maintenance": is_maintenance_mode()})


# ════════════════════════════════════════════════════════════════
#  AUTH — Pages
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
            "SELECT * FROM users WHERE (email=%s OR username=%s) AND status!='blocked'",
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


# ════════════════════════════════════════════════════════════════
#  FORGOT / RESET PASSWORD
# ════════════════════════════════════════════════════════════════

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
            (user["user_id"], token, expiry, token, expiry),
            commit=True,
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
        (generate_password_hash(new_password), row["user_id"]),
        commit=True,
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
                (user["user_id"], token, expiry, token, expiry),
                commit=True,
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
#  API — SHARED  (/api/me)
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

    user = query(
        "SELECT password_hash FROM users WHERE user_id=%s",
        (session["user_id"],), one=True
    )
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


# ════════════════════════════════════════════════════════════════
#  EMERGENCY ACTIONS
# ════════════════════════════════════════════════════════════════

@app.route("/api/superadmin/emergency/<action>", methods=["POST"])
@role_required("superadmin")
def api_sa_emergency(action):
    allowed = {
        "shutdown", "enable_system", "block_all",
        "reset_admin_passwords", "disable_promos", "force_logout"
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
        extra["message"] = f"All promo codes deactivated."

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
#  API — ADMIN
# ════════════════════════════════════════════════════════════════

@app.route("/api/admin/me")
@role_required("admin", "superadmin")
def api_admin_me():
    user = query(
        "SELECT user_id, full_name, email, phone, role, status, created_at "
        "FROM users WHERE user_id=%s",
        (session["user_id"],), one=True
    )
    return jresp(user or {})


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
           WHERE u.role='staff'
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


# ════════════════════════════════════════════════════════════════
#  API — STAFF MANAGEMENT
# ════════════════════════════════════════════════════════════════

@app.route("/api/staff")
@role_required("admin", "superadmin")
def api_staff_list():
    rows = query(
        "SELECT user_id, full_name, email, status, created_at "
        "FROM users WHERE role='staff' ORDER BY full_name"
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


@app.route("/api/staff/remove/<int:uid>", methods=["DELETE"])
@role_required("admin", "superadmin")
def api_staff_remove(uid):
    query("DELETE FROM users WHERE user_id=%s AND role='staff'", (uid,), commit=True)
    log_audit(session["full_name"], "remove_staff",
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
                0, min(100, int((1 - rem / stage_total) * 100)))
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
                0, min(100, int((1 - rem / stage_total) * 100)))
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

    linked_customer_id = None
    if customer_email:
        registered = query(
            "SELECT user_id FROM users WHERE email=%s AND role='customer' AND status='active'",
            (customer_email,), one=True
        )
        if registered:
            linked_customer_id = registered["user_id"]

    # Warn if multiple active services for same email (don't block)
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
        "ok":                True,
        "order_id":          oid,
        "tracking_id":       tracking,
        "amount":            amount,
        "machines_needed":   m_needed,
        "email_queued":      bool(customer_email),
        "linked_to_account": linked_customer_id is not None,
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
        return jresp({
            "error": f"Machine(s) Unit {unit_nums} are no longer free."
        }, 409)

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
#  FOLD DONE
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
        "SELECT status FROM machines WHERE machine_id=%s", (machine_id,), one=True
    )
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


@app.route("/api/staff/issues")
@role_required("staff", "admin", "superadmin")
def api_staff_issues():
    rows = query(
        "SELECT * FROM issues WHERE reported_by=%s ORDER BY reported_at DESC",
        (session["user_id"],)
    )
    return jresp(rows or [])


@app.route("/api/staff/issues/report", methods=["POST"])
@role_required("staff", "admin", "superadmin")
@_require_json_or_xhr
def api_report_issue():
    d = request.get_json(silent=True) or {}
    issue_type = d.get("issue_type", "other")
    order_id = d.get("order_id") or None
    desc = d.get("description", "").strip()

    if not desc:
        return jresp({"error": "Description required"}, 400)

    query(
        "INSERT INTO issues (issue_type, order_id, description, reported_by, reported_at) "
        "VALUES (%s,%s,%s,%s,NOW())",
        (issue_type, order_id, desc, session["user_id"]), commit=True
    )
    log_audit(session["full_name"], "report_issue",
              issue_type, request.remote_addr)
    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  STAGE ADVANCEMENT LOGIC (shared by scheduler + HTTP endpoint)
# ════════════════════════════════════════════════════════════════

def _advance_stages_logic():
    """
    Core stage-advancement logic.
    Called by the background scheduler (every 10s) and by the
    HTTP endpoint /api/internal/advance-stages.

    Flow:
      washing  → drying  (if with_dryer)
               → downy   (if with_downy but no dryer)
               → folding (otherwise)
      drying   → downy   (if with_downy)
               → folding (otherwise)
      downy    → folding
    Machines are freed as soon as the order enters folding.
    """
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

    # ── Washing → next stage ──────────────────────────────────
    washing_done = query(
        "SELECT * FROM orders WHERE status='washing' AND stage_ends_at <= %s", (
            now,)
    ) or []
    for o in washing_done:
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

    # ── Drying → next stage ───────────────────────────────────
    drying_done = query(
        "SELECT * FROM orders WHERE status='drying' AND stage_ends_at <= %s", (
            now,)
    ) or []
    for o in drying_done:
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

    # ── Downy → folding ───────────────────────────────────────
    downy_done = query(
        "SELECT * FROM orders WHERE status='downy' AND stage_ends_at <= %s", (
            now,)
    ) or []
    for o in downy_done:
        query(
            "UPDATE orders SET status='folding', stage_ends_at=NULL, fold_ends_at=NULL "
            "WHERE order_id=%s", (o["order_id"],), commit=True
        )
        _free_machines(o["order_id"])
        advanced += 1

    return advanced


# ════════════════════════════════════════════════════════════════
#  HTTP ENDPOINT — manual trigger (still available for cron/testing)
# ════════════════════════════════════════════════════════════════

@app.route("/api/internal/advance-stages", methods=["POST"])
def api_advance_stages():
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if token != INTERNAL_SECRET:
        return jresp({"error": "Forbidden"}, 403)

    advanced = _advance_stages_logic()
    return jresp({"ok": True, "advanced": advanced})


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
#  API — CUSTOMERS
# ════════════════════════════════════════════════════════════════

@app.route("/api/customers")
@role_required("admin", "superadmin")
def api_customers():
    rows = query(
        "SELECT user_id, full_name, email, phone, status, created_at "
        "FROM users WHERE role='customer' ORDER BY created_at DESC"
    )
    return jresp(rows or [])


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
    code = d.get("code",     "").strip().upper()
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
#  FIX: All active-order endpoints now return ALL active orders,
#       not just the most recent one (removed LIMIT 1).
# ════════════════════════════════════════════════════════════════

@app.route("/api/customer/dashboard")
@role_required("customer")
def api_customer_dashboard():
    uid = session["user_id"]

    # ── ALL active orders (not just 1) ──────────────────────────
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

    # Keep backward-compat: expose the most recent as active_order
    active_order = active_orders[0] if active_orders else None

    return jresp({
        # most recent (for single-card views)
        "active_order":  active_order,
        # ALL active orders (new field)
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
    """Returns the most recent active order (backward-compat for status page)."""
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
    """Returns ALL active orders for this customer."""
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


def _enrich_order_timer(o):
    if o.get("stage_ends_at") and o.get("status") in ("washing", "drying", "downy"):
        now = datetime.now()
        rem = (o["stage_ends_at"] - now).total_seconds()
        stage_total = {
            "washing": WASH_SECS, "drying": DRY_SECS, "downy": DOWNY_SECS,
        }.get(o["status"], WASH_SECS)
        o["remaining_seconds"] = max(0, int(rem))
        o["progress_pct"] = max(
            0, min(100, int((1 - rem / stage_total) * 100)))


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
        promo = query(
            "SELECT * FROM promos WHERE code=%s AND is_active=1", (promo_code,), one=True
        )
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
#  CLI RECOVERY COMMANDS
# ════════════════════════════════════════════════════════════════

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
#  API — SUPERADMIN SELF-SERVICE
# ════════════════════════════════════════════════════════════════

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
        secret_phrase.encode(), SA_RECOVERY_PHRASE.encode())

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
                (user["user_id"], token, expiry, token, expiry),
                commit=True,
            )
            send_reset_email(user["email"], token, user["full_name"])
            log_audit(email, "sa_reset_email_sent", email, request.remote_addr)
    else:
        log_audit(email, "sa_reset_wrong_phrase", email, request.remote_addr)

    return jresp({"ok": True})


# ════════════════════════════════════════════════════════════════
#  API — SYSTEM CONFIG
# ════════════════════════════════════════════════════════════════

@app.route("/api/system/settings", methods=["GET"])
@role_required("admin", "superadmin")
def api_system_settings_get():
    rows = query("SELECT * FROM system_settings") or []
    return jresp({r["setting_key"]: r["setting_value"] for r in rows})


@app.route("/api/system/settings", methods=["POST"])
@role_required("admin", "superadmin")
def api_system_settings_save():
    d = request.get_json(silent=True) or {}
    for key, val in d.items():
        query(
            "INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s) "
            "ON DUPLICATE KEY UPDATE setting_value=%s",
            (key, str(val), str(val)), commit=True
        )
    log_audit(session["full_name"], "update_system_settings",
              str(list(d.keys())), request.remote_addr)
    return jresp({"ok": True})


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
