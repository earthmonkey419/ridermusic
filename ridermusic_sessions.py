import secrets
import sqlite3
import time
from functools import wraps
from flask import request, redirect, make_response, jsonify, g

from config import DB_PATH, SESSION_TIMEOUT_SECONDS, COOKIE_SECURE

COOKIE_NAME = "rm_session"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.execute("PRAGMA busy_timeout = 10000")
        g.db.row_factory = sqlite3.Row
    return g.db


def teardown_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_active_session(db):
    now = time.time()
    return db.execute(
        "SELECT * FROM sessions WHERE ended_by_admin = 0 AND expires_at > ? "
        "ORDER BY started_at DESC LIMIT 1",
        (now,)
    ).fetchone()


def _generate_rejoin_code():
    # 4-digit numeric: easy for a driver to say out loud, easy for a
    # passenger to type on a phone keypad. Not high-security -- it's
    # a "did the driver actually tell you this" check, not encryption.
    return f"{secrets.randbelow(10000):04d}"


def create_session(db):
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    code = _generate_rejoin_code()
    db.execute(
        "INSERT INTO sessions (session_id, started_at, expires_at, "
        "ended_by_admin, device_count, rejoin_code) VALUES (?, ?, ?, 0, 1, ?)",
        (session_id, now, now + SESSION_TIMEOUT_SECONDS, code)
    )
    db.commit()
    return session_id


def attach_device(db, session_id):
    db.execute(
        "UPDATE sessions SET device_count = device_count + 1 WHERE session_id = ?",
        (session_id,)
    )
    db.commit()


def log_action(db, session_id, action_type, detail=""):
    db.execute(
        "INSERT INTO session_actions (session_id, action_type, detail, ts) "
        "VALUES (?, ?, ?, ?)",
        (session_id, action_type, detail, time.time())
    )
    db.commit()


def validate_session(db, token):
    if not token:
        return None
    now = time.time()
    return db.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND ended_by_admin = 0 "
        "AND expires_at > ?",
        (token, now)
    ).fetchone()


REJOIN_FORM = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiderMusic for Plex</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #224248;
    --panel: #325e6a;
    --accent: #44a1a4;
    --cta: #ff9a00;
    --text: #eef6f6;
    --text-muted: #9fc2c4;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
  }
  .wrap { max-width: 420px; margin: 0 auto; padding: 2em 1.25em; text-align: center; }
  .logo {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    font-size: 1.2em;
    margin-bottom: 1.5em;
  }
  .logo span { color: var(--cta); }
  h2 {
    font-family: 'Sora', sans-serif;
    font-weight: 700;
    margin-bottom: 0.5em;
  }
  p { color: var(--text-muted); line-height: 1.5; margin-bottom: 1.5em; }
  input[type=text] {
    width: 100%;
    padding: 0.9em;
    border-radius: 10px;
    border: none;
    background: var(--panel);
    color: var(--text);
    font-size: 1.6em;
    text-align: center;
    letter-spacing: 0.3em;
    margin-bottom: 1em;
  }
  button {
    width: 100%;
    padding: 0.9em;
    border-radius: 10px;
    border: none;
    background: var(--cta);
    color: #1a1a1a;
    font-weight: 600;
    font-size: 1em;
    cursor: pointer;
  }
  .error { color: #ff6b6b; margin-top: 1em; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Rider<span>Music</span></div>
  <h2>A ride is already in progress</h2>
  <p>Ask your driver for the 4-digit code to join.</p>
  <form method="post">
    <input type="text" name="code" inputmode="numeric" pattern="[0-9]*"
           maxlength="4" autofocus placeholder="0000">
    <button type="submit">Join</button>
  </form>
  __ERROR__
</div>
</body>
</html>
"""


def require_active_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        db = get_db()
        token = request.cookies.get(COOKIE_NAME)
        session_row = validate_session(db, token)
        if not session_row:
            return jsonify({"error": "session_expired"}), 401
        g.session = session_row
        return f(*args, **kwargs)
    return wrapper


def register_join_route(app):
    @app.route("/join", methods=["GET", "POST"])
    def join():
        db = get_db()
        existing_token = request.cookies.get(COOKIE_NAME)
        session_row = validate_session(db, existing_token)

        if session_row:
            # Already has a valid cookie for the currently active session.
            return make_response(redirect("/guest"))

        active = get_active_session(db)

        if not active:
            # Nothing in progress -- starting fresh needs no code at all.
            token = create_session(db)
            log_action(db, token, "join")
            resp = make_response(redirect("/guest"))
            resp.set_cookie(
                COOKIE_NAME, token,
                httponly=True, secure=COOKIE_SECURE, samesite="Lax",
                max_age=SESSION_TIMEOUT_SECONDS
            )
            return resp

        # A session is already active -- joining it requires the code.
        if request.method == "GET":
            return REJOIN_FORM.replace("__ERROR__", "")

        submitted = request.form.get("code", "").strip()
        if not submitted or submitted != active["rejoin_code"]:
            return REJOIN_FORM.replace(
                "__ERROR__", '<p class="error">Incorrect code. Try again.</p>'
            ), 401

        attach_device(db, active["session_id"])
        log_action(db, active["session_id"], "join")
        resp = make_response(redirect("/guest"))
        resp.set_cookie(
            COOKIE_NAME, active["session_id"],
            httponly=True, secure=COOKIE_SECURE, samesite="Lax",
            max_age=SESSION_TIMEOUT_SECONDS
        )
        return resp
