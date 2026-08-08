import time
from flask import request, jsonify, g

from config import MAX_QUEUE_ADDS_PER_SESSION
from ridermusic_sessions import get_db, require_active_session, log_action
from ridermusic_player import get_plex
from ridermusic_playback import get_playback_state, advance_to_next


def register_guest_routes(app):

    @app.route("/guest/search")
    @require_active_session
    def guest_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"results": []})

        plex = get_plex()
        # Plex's hub search: relevance-ranked, not strict substring matching.
        # Known limitation: can surface closely-related artists (e.g. band
        # members) alongside literal matches. Acceptable for v1; revisit if
        # guest feedback shows it's confusing in practice.
        tracks = plex.search(q, mediatype="track", limit=25)

        results = [
            {
                "rating_key": t.ratingKey,
                "title": t.title,
                "artist": getattr(t, "grandparentTitle", None),
                "album": getattr(t, "parentTitle", None),
                "duration_ms": getattr(t, "duration", None),
            }
            for t in tracks
        ]
        return jsonify({"results": results})

    @app.route("/guest/queue/add", methods=["POST"])
    @require_active_session
    def guest_queue_add():
        data = request.get_json(silent=True) or {}
        rating_key = data.get("rating_key")
        if not rating_key:
            return jsonify({"error": "rating_key required"}), 400

        db = get_db()
        session_id = g.session["session_id"]

        current_count = db.execute(
            "SELECT COUNT(*) AS c FROM queue WHERE session_id = ?",
            (session_id,)
        ).fetchone()["c"]

        if current_count >= MAX_QUEUE_ADDS_PER_SESSION:
            return jsonify({"error": "queue_limit_reached"}), 429

        plex = get_plex()
        try:
            track = plex.fetchItem(int(rating_key))
        except Exception:
            return jsonify({"error": "track_not_found"}), 404

        if track.type != "track":
            return jsonify({"error": "not_a_music_track"}), 403

        db.execute(
            "INSERT INTO queue (session_id, rating_key, title, artist, "
            "duration_ms, added_at, played) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (session_id, track.ratingKey, track.title, track.grandparentTitle,
             track.duration, time.time())
        )
        db.commit()
        log_action(db, session_id, "queue_add", detail=track.title)

        state = get_playback_state(db, session_id)
        if not state["current_queue_id"]:
            advance_to_next(db, session_id, mark_current_played=False)

        return jsonify({"added": True, "title": track.title, "artist": track.grandparentTitle})

    @app.route("/guest/queue")
    @require_active_session
    def guest_queue_view():
        db = get_db()
        session_id = g.session["session_id"]
        rows = db.execute(
            "SELECT rating_key, title, artist, duration_ms, added_at FROM queue "
            "WHERE session_id = ? AND played = 0 ORDER BY added_at ASC",
            (session_id,)
        ).fetchall()

        return jsonify({
            "queue": [
                {
                    "rating_key": r["rating_key"],
                    "title": r["title"],
                    "artist": r["artist"],
                    "duration_ms": r["duration_ms"],
                }
                for r in rows
            ]
        })


GUEST_PAGE = """
<!doctype html>
<title>RiderMusic</title>
<style>
  body { font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 1em; }
  h2 { margin-bottom: 0.2em; }
  #now-playing { padding: 1em; background: #f2f2f2; border-radius: 8px; margin-bottom: 1em; }
  #controls button { font-size: 1.3em; padding: 0.4em 0.8em; margin-right: 0.5em; }
  #search-results div, #queue div { padding: 0.5em; border-bottom: 1px solid #ddd; }
  #search-results button, #queue-add-hint { font-size: 0.9em; }
  input[type=text] { width: 70%; padding: 0.5em; font-size: 1em; }
  #search-btn { padding: 0.5em 1em; font-size: 1em; }
</style>

<h2>🎵 Be the DJ</h2>

<div id="now-playing">Loading...</div>
<div id="controls">
  <button id="play-pause-btn">⏯</button>
  <button id="skip-btn">⏭</button>
  <input type="range" id="volume" min="0" max="100" value="50">
</div>

<h3>Search</h3>
<input type="text" id="search-box" placeholder="Artist or song...">
<button id="search-btn">Search</button>
<div id="search-results"></div>

<h3>Up next</h3>
<div id="queue"></div>

<script>
async function refreshPlayback() {
  const res = await fetch('/guest/playback');
  const data = await res.json();
  const np = document.getElementById('now-playing');
  if (data.now_playing) {
    np.textContent = (data.is_playing ? '▶ ' : '⏸ ') + data.now_playing.title + ' — ' + data.now_playing.artist;
  } else {
    np.textContent = 'Nothing playing yet — search and add a song!';
  }
  document.getElementById('volume').value = data.volume;
}

async function refreshQueue() {
  const res = await fetch('/guest/queue');
  const data = await res.json();
  const el = document.getElementById('queue');
  el.innerHTML = '';
  if (data.queue.length === 0) {
    el.innerHTML = '<div>Queue is empty</div>';
  }
  for (const t of data.queue) {
    const div = document.createElement('div');
    div.textContent = t.title + ' — ' + t.artist;
    el.appendChild(div);
  }
}

document.getElementById('search-btn').addEventListener('click', async () => {
  const q = document.getElementById('search-box').value.trim();
  if (!q) return;
  const res = await fetch('/guest/search?q=' + encodeURIComponent(q));
  const data = await res.json();
  const el = document.getElementById('search-results');
  el.innerHTML = '';
  for (const t of data.results) {
    const div = document.createElement('div');
    div.textContent = t.title + ' — ' + t.artist + ' (' + t.album + ') ';
    const btn = document.createElement('button');
    btn.textContent = '+ Add';
    btn.addEventListener('click', async () => {
      const r = await fetch('/guest/queue/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({rating_key: t.rating_key})
      });
      const result = await r.json();
      if (result.error) {
        alert(result.error === 'queue_limit_reached' ? 'Queue is full!' : result.error);
      } else {
        refreshQueue();
        refreshPlayback();
      }
    });
    div.appendChild(btn);
    el.appendChild(div);
  }
});

document.getElementById('play-pause-btn').addEventListener('click', async () => {
  const res = await fetch('/guest/playback');
  const data = await res.json();
  const action = data.is_playing ? 'pause' : 'play';
  await fetch('/guest/playback/play_pause', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: action})
  });
  refreshPlayback();
});

document.getElementById('skip-btn').addEventListener('click', async () => {
  const res = await fetch('/guest/playback/skip', {method: 'POST'});
  const data = await res.json();
  if (data.error === 'rate_limited') {
    alert('Slow down! Try again in ' + Math.ceil(data.retry_after_seconds) + 's');
  }
  refreshPlayback();
  refreshQueue();
});

document.getElementById('volume').addEventListener('change', async (e) => {
  await fetch('/guest/playback/volume', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({volume: parseInt(e.target.value)})
  });
});

setInterval(() => { refreshPlayback(); refreshQueue(); }, 3000);
refreshPlayback();
refreshQueue();
</script>
"""


def register_guest_page_route(app):
    from ridermusic_sessions import require_active_session

    @app.route("/guest")
    @require_active_session
    def guest_page():
        return GUEST_PAGE
