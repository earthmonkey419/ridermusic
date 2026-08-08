import secrets
import time
from functools import wraps
from flask import request, redirect, make_response, jsonify, g

from config import ADMIN_PASSWORD, ADMIN_SESSION_DAYS, COOKIE_SECURE
from ridermusic_sessions import get_db, get_active_session

ADMIN_COOKIE_NAME = "rm_admin"
ADMIN_SESSION_SECONDS = ADMIN_SESSION_DAYS * 24 * 60 * 60


def create_admin_session(db):
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute(
        "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
        (token, now, now + ADMIN_SESSION_SECONDS)
    )
    db.commit()
    return token


def validate_admin_session(db, token):
    if not token:
        return None
    now = time.time()
    return db.execute(
        "SELECT * FROM admin_sessions WHERE token = ? AND expires_at > ?",
        (token, now)
    ).fetchone()


def require_admin_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        db = get_db()
        token = request.cookies.get(ADMIN_COOKIE_NAME)
        session_row = validate_admin_session(db, token)
        if not session_row:
            return redirect("/admin/login")
        g.admin_session = session_row
        return f(*args, **kwargs)
    return wrapper


LOGIN_FORM = """
<!doctype html>
<title>RiderMusic Admin</title>
<form method="post">
  <label>Password: <input type="password" name="password" autofocus></label>
  <button type="submit">Log in</button>
</form>
"""


def register_admin_routes(app):

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "GET":
            return LOGIN_FORM

        submitted = request.form.get("password", "")
        if not secrets.compare_digest(submitted, ADMIN_PASSWORD):
            return LOGIN_FORM + "<p>Incorrect password.</p>", 401

        db = get_db()
        token = create_admin_session(db)
        resp = make_response(redirect("/admin/status"))
        resp.set_cookie(
            ADMIN_COOKIE_NAME, token,
            httponly=True, secure=COOKIE_SECURE, samesite="Lax",
            max_age=ADMIN_SESSION_SECONDS
        )
        return resp

    @app.route("/admin/status")
    @require_admin_auth
    def admin_status():
        db = get_db()
        active = get_active_session(db)
        if not active:
            return jsonify({"active": False})

        now = time.time()
        remaining = max(0, active["expires_at"] - now)
        recent_actions = db.execute(
            "SELECT action_type, detail, ts FROM session_actions "
            "WHERE session_id = ? ORDER BY ts DESC LIMIT 20",
            (active["session_id"],)
        ).fetchall()

        return jsonify({
            "active": True,
            "session_id": active["session_id"],
            "device_count": active["device_count"],
            "seconds_remaining": int(remaining),
            "recent_actions": [
                {"type": r["action_type"], "detail": r["detail"], "ts": r["ts"]}
                for r in recent_actions
            ]
        })

    @app.route("/admin/end_session", methods=["POST"])
    @require_admin_auth
    def admin_end_session():
        db = get_db()
        active = get_active_session(db)
        if not active:
            return jsonify({"ended": False, "reason": "no_active_session"})

        db.execute(
            "UPDATE sessions SET ended_by_admin = 1 WHERE session_id = ?",
            (active["session_id"],)
        )
        db.commit()
        return jsonify({"ended": True, "session_id": active["session_id"]})
