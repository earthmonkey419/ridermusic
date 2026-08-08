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


BASE_STYLE = """
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
  .wrap { max-width: 440px; margin: 0 auto; padding: 2em 1.25em; }
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
    margin-bottom: 1em;
  }
</style>
"""

LOGIN_FORM = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiderMusic Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
""" + BASE_STYLE + """
<style>
  input[type=password] {
    width: 100%;
    padding: 0.85em 1em;
    border-radius: 10px;
    border: none;
    background: var(--panel);
    color: var(--text);
    font-size: 1em;
    margin-bottom: 0.8em;
  }
  button {
    width: 100%;
    padding: 0.85em;
    border-radius: 10px;
    border: none;
    background: var(--cta);
    color: #1a1a1a;
    font-weight: 600;
    font-size: 1em;
    cursor: pointer;
  }
  .error { color: #ff6b6b; margin-top: 0.8em; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Rider<span>Music</span></div>
  <h2>Admin Login</h2>
  <form method="post">
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Log in</button>
  </form>
  __ERROR__
</div>
</body>
</html>
"""


def register_admin_routes(app):

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "GET":
            return LOGIN_FORM.replace("__ERROR__", "")

        submitted = request.form.get("password", "")
        if not secrets.compare_digest(submitted, ADMIN_PASSWORD):
            return LOGIN_FORM.replace("__ERROR__", '<p class="error">Incorrect password.</p>'), 401

        db = get_db()
        token = create_admin_session(db)
        resp = make_response(redirect("/admin/dashboard"))
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


ADMIN_DASHBOARD_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiderMusic Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
""" + BASE_STYLE + """
<style>
  .card {
    background: var(--panel);
    border-radius: 14px;
    padding: 1.2em;
    margin-bottom: 1.2em;
  }
  .stat { font-size: 1.05em; margin-bottom: 0.3em; }
  .stat .label { color: var(--text-muted); }
  #end-btn {
    width: 100%;
    padding: 0.9em;
    font-size: 1.05em;
    font-weight: 700;
    font-family: 'Sora', sans-serif;
    background: #c0392b;
    color: white;
    border: none;
    border-radius: 10px;
    cursor: pointer;
  }
  #end-btn:disabled { background: var(--panel); color: var(--text-muted); }
  #log .row {
    padding: 0.5em 0;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    font-size: 0.88em;
    color: var(--text-muted);
  }
  #log .row:last-child { border-bottom: none; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Rider<span>Music</span> Admin</div>

  <div class="card" id="status">Loading...</div>
  <button id="end-btn">End Session</button>

  <h2 style="margin-top:1.5em; font-size:1em; text-transform:uppercase; letter-spacing:0.05em; color:var(--text-muted);">Recent activity</h2>
  <div class="card" id="log"></div>
</div>

<script>
async function refresh() {
  const res = await fetch('/admin/status');
  const data = await res.json();
  const statusEl = document.getElementById('status');
  const btn = document.getElementById('end-btn');

  if (!data.active) {
    statusEl.innerHTML = '<span class="label">No active ride right now.</span>';
    btn.disabled = true;
    document.getElementById('log').innerHTML = '';
    return;
  }

  const mins = Math.floor(data.seconds_remaining / 60);
  statusEl.innerHTML =
    '<div class="stat"><span class="label">Status:</span> Ride in progress</div>' +
    '<div class="stat"><span class="label">Devices:</span> ' + data.device_count + '</div>' +
    '<div class="stat"><span class="label">Time left:</span> ' + mins + ' min</div>';
  btn.disabled = false;

  const logEl = document.getElementById('log');
  logEl.innerHTML = '';
  if (data.recent_actions.length === 0) {
    logEl.innerHTML = '<div class="row">No activity yet</div>';
  }
  for (const a of data.recent_actions) {
    const div = document.createElement('div');
    div.className = 'row';
    const time = new Date(a.ts * 1000).toLocaleTimeString();
    div.textContent = time + ' — ' + a.type + (a.detail ? ': ' + a.detail : '');
    logEl.appendChild(div);
  }
}

document.getElementById('end-btn').addEventListener('click', async () => {
  if (!confirm('End the current ride? Guests will lose access immediately.')) return;
  await fetch('/admin/end_session', { method: 'POST' });
  refresh();
});

setInterval(refresh, 3000);
refresh();
</script>
</body>
</html>
"""


def register_admin_dashboard_route(app):
    @app.route("/admin/dashboard")
    @require_admin_auth
    def admin_dashboard():
        return ADMIN_DASHBOARD_PAGE
