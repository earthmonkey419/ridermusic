import secrets
import sqlite3
import time
from functools import wraps
from flask import request, redirect, make_response, jsonify, g

from config import DB_PATH, SESSION_TIMEOUT_SECONDS, COOKIE_SECURE

COOKIE_NAME = "rm_session"


def get_db():
    """Returns a request-scoped db connection, opened once per request
    and closed automatically via teardown_db (registered in app.py)."""
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


def create_session(db):
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    db.execute(
        "INSERT INTO sessions (session_id, started_at, expires_at, "
        "ended_by_admin, device_count) VALUES (?, ?, ?, 0, 1)",
        (session_id, now, now + SESSION_TIMEOUT_SECONDS)
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


def register_join_route(app):
    @app.route("/join")
    def join():
        db = get_db()
        existing_token = request.cookies.get(COOKIE_NAME)
        session_row = validate_session(db, existing_token)

        if session_row:
            return make_response(redirect("/guest"))

        active = get_active_session(db)
        if active:
            attach_device(db, active["session_id"])
            log_action(db, active["session_id"], "join")
            token = active["session_id"]
        else:
            token = create_session(db)
            log_action(db, token, "join")

        resp = make_response(redirect("/guest"))
        resp.set_cookie(
            COOKIE_NAME, token,
            httponly=True, secure=COOKIE_SECURE, samesite="Lax",
            max_age=SESSION_TIMEOUT_SECONDS
        )
        return resp


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
