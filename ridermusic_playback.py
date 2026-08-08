import time
from flask import request, jsonify, g

from ridermusic_sessions import get_db, require_active_session, log_action, get_active_session
from ridermusic_admin import require_admin_auth


def get_config_value(db, key, default):
    row = db.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else default


def get_playback_state(db, session_id):
    row = db.execute(
        "SELECT * FROM playback_state WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row:
        return row

    ceiling = get_config_value(db, "volume_ceiling", 80)
    default_volume = min(50, ceiling)
    db.execute(
        "INSERT INTO playback_state (session_id, current_queue_id, is_playing, "
        "position_ms, volume, updated_at) VALUES (?, NULL, 0, 0, ?, ?)",
        (session_id, default_volume, time.time())
    )
    db.commit()
    return db.execute(
        "SELECT * FROM playback_state WHERE session_id = ?", (session_id,)
    ).fetchone()


def advance_to_next(db, session_id, mark_current_played=True):
    state = get_playback_state(db, session_id)

    if mark_current_played and state["current_queue_id"]:
        db.execute("UPDATE queue SET played = 1 WHERE id = ?", (state["current_queue_id"],))
        db.commit()

    next_row = db.execute(
        "SELECT id FROM queue WHERE session_id = ? AND played = 0 "
        "ORDER BY added_at ASC LIMIT 1",
        (session_id,)
    ).fetchone()
    next_id = next_row["id"] if next_row else None

    db.execute(
        "UPDATE playback_state SET current_queue_id = ?, is_playing = ?, "
        "position_ms = 0, updated_at = ? WHERE session_id = ?",
        (next_id, 1 if next_id else 0, time.time(), session_id)
    )
    db.commit()
    return next_id


def register_playback_routes(app):

    @app.route("/guest/playback")
    @require_active_session
    def guest_playback_view():
        db = get_db()
        session_id = g.session["session_id"]
        state = get_playback_state(db, session_id)

        now_playing = None
        if state["current_queue_id"]:
            row = db.execute(
                "SELECT title, artist, duration_ms FROM queue WHERE id = ?",
                (state["current_queue_id"],)
            ).fetchone()
            if row:
                now_playing = {
                    "title": row["title"],
                    "artist": row["artist"],
                    "duration_ms": row["duration_ms"],
                }

        return jsonify({
            "is_playing": bool(state["is_playing"]),
            "position_ms": state["position_ms"],
            "volume": state["volume"],
            "now_playing": now_playing,
        })

    @app.route("/guest/playback/play_pause", methods=["POST"])
    @require_active_session
    def guest_play_pause():
        data = request.get_json(silent=True) or {}
        action = data.get("action")
        if action not in ("play", "pause"):
            return jsonify({"error": "action must be play or pause"}), 400

        db = get_db()
        session_id = g.session["session_id"]
        get_playback_state(db, session_id)

        db.execute(
            "UPDATE playback_state SET is_playing = ?, updated_at = ? WHERE session_id = ?",
            (1 if action == "play" else 0, time.time(), session_id)
        )
        db.commit()
        log_action(db, session_id, "play_pause", detail=action)
        return jsonify({"is_playing": action == "play"})

    @app.route("/guest/playback/skip", methods=["POST"])
    @require_active_session
    def guest_skip():
        db = get_db()
        session_id = g.session["session_id"]
        state = get_playback_state(db, session_id)

        rate_limit = get_config_value(db, "skip_rate_limit_seconds", 20)
        now = time.time()
        if state["last_skip_at"] and (now - state["last_skip_at"]) < rate_limit:
            wait = rate_limit - (now - state["last_skip_at"])
            return jsonify({"error": "rate_limited", "retry_after_seconds": round(wait, 1)}), 429

        next_id = advance_to_next(db, session_id, mark_current_played=True)
        db.execute(
            "UPDATE playback_state SET last_skip_at = ? WHERE session_id = ?",
            (now, session_id)
        )
        db.commit()
        log_action(db, session_id, "skip")
        return jsonify({"skipped": True, "next_queue_id": next_id})

    @app.route("/guest/playback/volume", methods=["POST"])
    @require_active_session
    def guest_volume():
        data = request.get_json(silent=True) or {}
        volume = data.get("volume")
        if volume is None:
            return jsonify({"error": "volume required"}), 400

        db = get_db()
        session_id = g.session["session_id"]
        ceiling = get_config_value(db, "volume_ceiling", 80)
        clamped = max(0, min(int(volume), ceiling))

        get_playback_state(db, session_id)
        db.execute(
            "UPDATE playback_state SET volume = ?, updated_at = ? WHERE session_id = ?",
            (clamped, time.time(), session_id)
        )
        db.commit()
        log_action(db, session_id, "volume", detail=str(clamped))
        return jsonify({"volume": clamped, "ceiling": ceiling})

    @app.route("/player/next", methods=["POST"])
    @require_admin_auth
    def player_next():
        db = get_db()
        active = get_active_session(db)
        if not active:
            return jsonify({"error": "no_active_session"}), 404

        next_id = advance_to_next(db, active["session_id"], mark_current_played=True)
        return jsonify({"advanced": True, "next_queue_id": next_id})


def register_player_state_route(app):
    @app.route("/player/state")
    @require_admin_auth
    def player_state():
        db = get_db()
        active = get_active_session(db)
        if not active:
            return jsonify({"active": False})

        state = get_playback_state(db, active["session_id"])
        now_playing = None
        if state["current_queue_id"]:
            row = db.execute(
                "SELECT rating_key, title, artist, duration_ms FROM queue WHERE id = ?",
                (state["current_queue_id"],)
            ).fetchone()
            if row:
                now_playing = {
                    "rating_key": row["rating_key"],
                    "title": row["title"],
                    "artist": row["artist"],
                    "duration_ms": row["duration_ms"],
                }

        return jsonify({
            "active": True,
            "is_playing": bool(state["is_playing"]),
            "volume": state["volume"],
            "now_playing": now_playing,
        })


PLAYER_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiderMusic Driver</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
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
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .wrap { max-width: 440px; width: 100%; padding: 1.5em; text-align: center; }
  .logo {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    font-size: 1.1em;
    color: var(--text-muted);
    margin-bottom: 2em;
  }
  .logo span { color: var(--cta); }
  #track {
    font-family: 'Sora', sans-serif;
    font-weight: 700;
    font-size: 1.4em;
    margin-bottom: 0.2em;
  }
  #artist { color: var(--text-muted); margin-bottom: 1.5em; }
  audio { width: 100%; margin-bottom: 1em; }
  #status {
    display: inline-block;
    padding: 0.4em 1em;
    border-radius: 999px;
    background: var(--panel);
    color: var(--accent);
    font-weight: 600;
    font-size: 0.9em;
  }
  #hint { color: var(--text-muted); font-size: 0.85em; margin-top: 1.5em; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Rider<span>Music</span> — Driver</div>
  <div id="track">Nothing playing</div>
  <div id="artist"></div>
  <audio id="player" controls></audio>
  <div id="status">Waiting for a session...</div>
  <div id="hint">Riders control the music from their phones</div>
</div>

<script>
let currentRatingKey = null;

async function poll() {
  const res = await fetch('/player/state');
  const data = await res.json();
  const audio = document.getElementById('player');
  const track = document.getElementById('track');
  const artist = document.getElementById('artist');
  const status = document.getElementById('status');

  if (!data.active || !data.now_playing) {
    track.textContent = 'Nothing playing';
    artist.textContent = '';
    status.textContent = data.active ? 'Session active — queue empty' : 'No active session';
    audio.pause();
    currentRatingKey = null;
    return;
  }

  track.textContent = data.now_playing.title;
  artist.textContent = data.now_playing.artist;
  status.textContent = data.is_playing ? 'Playing' : 'Paused';
  audio.volume = data.volume / 100;

  if (data.now_playing.rating_key !== currentRatingKey) {
    currentRatingKey = data.now_playing.rating_key;
    audio.src = '/player/stream/' + currentRatingKey;
  }

  if (data.is_playing && audio.paused) {
    audio.play().catch(() => {});
  } else if (!data.is_playing && !audio.paused) {
    audio.pause();
  }
}

document.getElementById('player').addEventListener('ended', async () => {
  await fetch('/player/next', { method: 'POST' });
});

setInterval(poll, 2000);
poll();
</script>
</body>
</html>
"""


def register_player_page_route(app):
    @app.route("/player")
    @require_admin_auth
    def player_page():
        return PLAYER_PAGE
