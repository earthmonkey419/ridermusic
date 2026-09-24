import html
import time

from flask import request, jsonify, make_response

from config import COOKIE_SECURE
from ridermusic_sessions import (
    get_db, COOKIE_NAME, BASE_JOIN_STYLE, JOIN_FOOTER_HTML,
)
from ridermusic_admin import (
    require_admin_auth, BASE_STYLE, FOOTER_HTML, SUPPORT_LINK_HTML,
)

SHARE_URL = "https://ridermusic.vp-fun.com"

# How long after a session's expiry its feedback link keeps working.
FEEDBACK_WINDOW_SECONDS = 7 * 24 * 60 * 60
# Hard cap on submissions per session -- a ride has a handful of
# passengers at most, so this only ever stops abuse.
MAX_FEEDBACK_PER_SESSION = 10
MAX_COMMENT_LEN = 1000
MAX_CONTACT_LEN = 200


# --- Lookups ------------------------------------------------------------

def get_ended_session(db, session_cookie):
    """The session a guest's cookie points at, but only if it has ended
    (driver ended it, or it timed out) and is still inside the feedback
    window. None otherwise -- including for a still-active session."""
    if not session_cookie:
        return None
    row = db.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_cookie,)
    ).fetchone()
    if not row:
        return None
    now = time.time()
    ended = row["ended_by_admin"] == 1 or row["expires_at"] <= now
    if not ended:
        return None
    if now - row["expires_at"] > FEEDBACK_WINDOW_SECONDS:
        return None
    return row


def _session_for_feedback_token(db, token):
    if not token:
        return None
    row = db.execute(
        "SELECT * FROM sessions WHERE feedback_token = ?", (token,)
    ).fetchone()
    if not row:
        return None
    if time.time() - row["expires_at"] > FEEDBACK_WINDOW_SECONDS:
        return None
    return row


# --- Shared page pieces -------------------------------------------------

def _head(title):
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>""" + title + """</title>
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
<link rel="shortcut icon" href="/static/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
<link rel="manifest" href="/static/site.webmanifest">
<meta name="theme-color" content="#224248">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
"""


EXTRA_STYLE = """
<style>
  .btn {
    display: block;
    width: 100%;
    padding: 1em;
    border-radius: 12px;
    background: var(--cta);
    color: #1a1a1a;
    font-weight: 700;
    font-family: 'Sora', sans-serif;
    font-size: 1.1em;
    text-decoration: none;
    text-align: center;
    margin-bottom: 0.8em;
  }
  button.secondary {
    background: transparent;
    color: var(--text);
    border: 1px solid var(--accent);
    font-weight: 600;
  }
  .stars {
    position: relative;
    display: flex;
    flex-direction: row-reverse;
    justify-content: center;
    gap: 0.15em;
    margin-bottom: 1.4em;
  }
  .stars input {
    position: absolute;
    opacity: 0;
    width: 1px;
    height: 1px;
  }
  .stars label {
    font-size: 2.8em;
    line-height: 1;
    padding: 0.05em;
    color: rgba(255,255,255,0.25);
    cursor: pointer;
  }
  .stars input:checked ~ label,
  .stars label:hover,
  .stars label:hover ~ label { color: var(--cta); }
  label.field {
    display: block;
    text-align: left;
    color: var(--text-muted);
    font-size: 0.9em;
    margin-bottom: 0.4em;
  }
  textarea, input[type=email] {
    width: 100%;
    padding: 0.85em 1em;
    border-radius: 10px;
    border: none;
    background: var(--panel);
    color: var(--text);
    font-size: 1em;
    font-family: 'Inter', sans-serif;
    text-align: left;
    letter-spacing: normal;
    margin-bottom: 1.1em;
  }
  textarea { min-height: 7em; resize: vertical; }
  .fine { font-size: 0.8em; margin: 1em 0 0 0; color: var(--text-muted); }
  .share-url { color: var(--accent); font-weight: 600; }
</style>
"""

SHARE_SCRIPT = """
<script>
function shareRiderMusic() {
  var url = '""" + SHARE_URL + """';
  if (navigator.share) {
    navigator.share({title: 'RiderMusic Jukebox', text: 'Be the DJ for your ride', url: url}).catch(function () {});
  } else if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(
      function () { alert('Link copied!'); },
      function () { window.prompt('Copy this link:', url); }
    );
  } else {
    window.prompt('Copy this link:', url);
  }
}
</script>
"""


def _guest_page(title, body_html):
    return (
        _head(title) + BASE_JOIN_STYLE + EXTRA_STYLE + """
</head>
<body>
<div class="wrap">
  <div class="logo">Rider<span>Music</span> Jukebox</div>
""" + body_html + JOIN_FOOTER_HTML + """
</div>
""" + SHARE_SCRIPT + """
</body>
</html>
"""
    )


# --- Guest-facing pages -------------------------------------------------

def render_ended_page(session_row):
    """Shown to a guest whose ride has ended -- either by the JS reload
    when /guest/playback starts returning 401, or on a direct visit to
    /guest with the old cookie."""
    token = session_row["feedback_token"]
    if token:
        msg = "Your ride has ended. Got 30 seconds to tell us how the music went?"
        cta = ('<a class="btn" href="/feedback/' + html.escape(token) +
               '">Leave quick feedback</a>')
    else:
        # Session predates the feedback feature: no token to link to.
        msg = "Your ride has ended. Hope you enjoyed the music!"
        cta = ""
    return _guest_page(
        "Thanks for riding",
        "  <h2>Thanks for riding!</h2>\n  <p>" + msg + "</p>\n  " + cta + """
  <button type="button" class="secondary" onclick="shareRiderMusic()">Share RiderMusic</button>
""",
    )


def _stars_html(selected):
    parts = []
    for n in (5, 4, 3, 2, 1):
        checked = " checked" if n == selected else ""
        parts.append(
            '<input type="radio" id="s%d" name="rating" value="%d"%s>'
            '<label for="s%d" title="%d star%s">&#9733;</label>'
            % (n, n, checked, n, n, "" if n == 1 else "s")
        )
    return "".join(parts)


def _feedback_form(token, error="", comment="", contact="", rating=0):
    err_html = '<p class="error">' + html.escape(error) + "</p>" if error else ""
    return _guest_page(
        "How was your ride?",
        """  <h2>How was the music?</h2>
  <form method="post" action="/feedback/""" + html.escape(token) + """">
    <div class="stars">""" + _stars_html(rating) + """</div>
    <label class="field" for="comment">Anything we could do better? (optional)</label>
    <textarea id="comment" name="comment" maxlength="1000">""" + html.escape(comment) + """</textarea>
    <label class="field" for="contact">Email, only if you'd like a reply (optional)</label>
    <input type="email" id="contact" name="contact" maxlength="200"
           autocomplete="email" value=\"""" + html.escape(contact) + """\">
    <button type="submit">Send feedback</button>
  </form>
  """ + err_html + """
  <p class="fine">Your feedback goes to your driver. No account needed.</p>
""",
    )


def _thanks_page():
    return _guest_page(
        "Thank you",
        """  <h2>Thank you!</h2>
  <p>Your feedback helps make the next ride better.</p>
  <p style="margin-bottom:1em;">Know someone who'd love this?<br>
     <span class="share-url">""" + SHARE_URL.replace("https://", "") + """</span></p>
  <button type="button" onclick="shareRiderMusic()">Share RiderMusic</button>
""",
    )


def _unavailable_page():
    return _guest_page(
        "Link expired",
        """  <h2>This link has expired</h2>
  <p>Feedback links only work for a week after the ride. Thanks for riding!</p>
""",
    )


# --- Admin page ---------------------------------------------------------

def _fmt_ts(ts):
    try:
        return time.strftime("%b %d, %I:%M %p", time.localtime(ts))
    except Exception:
        return ""


def _render_admin_feedback(stats, rows):
    if stats["n"]:
        summary = ("%d response%s &middot; average %.1f &#9733;"
                   % (stats["n"], "" if stats["n"] == 1 else "s", stats["avg"]))
    else:
        summary = "No feedback yet."

    cards = []
    for r in rows:
        rating = r["rating"] or 0
        stars = "&#9733;" * rating + "&#9734;" * (5 - rating)
        comment = html.escape(r["comment"] or "")
        contact = html.escape(r["contact"] or "")
        card = ('<div class="card"><div class="fb-top"><span class="fb-stars">' +
                stars + '</span><span class="fb-when">' + _fmt_ts(r["submitted_at"]) +
                "</span></div>")
        if comment:
            card += '<div class="fb-comment">' + comment + "</div>"
        if contact:
            card += '<div class="fb-contact">Reply to: ' + contact + "</div>"
        card += "</div>"
        cards.append(card)

    return (
        _head("RiderMusic Jukebox for Plex - Feedback") + BASE_STYLE + """
<style>
  .card { background: var(--panel); border-radius: 14px; padding: 1em 1.2em; margin-bottom: 1em; }
  .fb-top { display: flex; justify-content: space-between; align-items: center; }
  .fb-stars { color: var(--cta); font-size: 1.2em; letter-spacing: 0.1em; }
  .fb-when { color: var(--text-muted); font-size: 0.8em; }
  .fb-comment { margin-top: 0.6em; white-space: pre-wrap; line-height: 1.5; }
  .fb-contact { margin-top: 0.6em; color: var(--accent); font-size: 0.85em; }
  .back { display: inline-block; margin-bottom: 1em; color: var(--accent); text-decoration: none; }
  .summary { color: var(--text-muted); margin-bottom: 1.2em; }
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/admin/dashboard">&larr; Back to dashboard</a>
  <div class="logo">Rider<span class="accent">Music</span> Jukebox for Plex - Feedback</div>
  <div class="summary">""" + summary + """</div>
  """ + SUPPORT_LINK_HTML + """
  """ + "".join(cards) + FOOTER_HTML + """
</div>
</body>
</html>
"""
    )


# --- Routes -------------------------------------------------------------

def register_feedback_routes(app):

    @app.route("/guest/ended")
    def guest_ended():
        # Deliberately NOT behind require_active_session: this is how a
        # guest's page finds out its session has ended.
        db = get_db()
        row = get_ended_session(db, request.cookies.get(COOKIE_NAME))
        return jsonify({"ended": bool(row)})

    @app.route("/feedback/<token>", methods=["GET", "POST"])
    def feedback_page(token):
        db = get_db()
        row = _session_for_feedback_token(db, token)
        if not row:
            return _unavailable_page(), 404

        done_cookie = "rm_fb_" + token[:12]
        if request.cookies.get(done_cookie):
            return _thanks_page()

        if request.method == "GET":
            return _feedback_form(token)

        rating = request.form.get("rating", type=int)
        comment = (request.form.get("comment") or "").strip()[:MAX_COMMENT_LEN]
        contact = (request.form.get("contact") or "").strip()[:MAX_CONTACT_LEN]

        if rating not in (1, 2, 3, 4, 5):
            return _feedback_form(
                token, error="Please tap a star rating.",
                comment=comment, contact=contact,
            ), 400

        count = db.execute(
            "SELECT COUNT(*) AS c FROM feedback WHERE session_id = ?",
            (row["session_id"],)
        ).fetchone()["c"]
        if count >= MAX_FEEDBACK_PER_SESSION:
            return _thanks_page()

        db.execute(
            "INSERT INTO feedback (session_id, rating, comment, contact, submitted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["session_id"], rating, comment, contact, time.time())
        )
        db.commit()

        resp = make_response(_thanks_page())
        resp.set_cookie(
            done_cookie, "1",
            httponly=True, secure=COOKIE_SECURE, samesite="Lax",
            max_age=FEEDBACK_WINDOW_SECONDS
        )
        return resp

    @app.route("/admin/feedback")
    @require_admin_auth
    def admin_feedback():
        db = get_db()
        stats = db.execute(
            "SELECT COUNT(*) AS n, AVG(rating) AS avg FROM feedback"
        ).fetchone()
        rows = db.execute(
            "SELECT rating, comment, contact, submitted_at FROM feedback "
            "ORDER BY submitted_at DESC LIMIT 100"
        ).fetchall()
        return _render_admin_feedback(stats, rows)
