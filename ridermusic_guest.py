import time
import random
from flask import request, jsonify, g

from config import MAX_QUEUE_ADDS_PER_SESSION, MUSIC_LIB
from ridermusic_sessions import get_db, require_active_session, log_action
from ridermusic_player import get_plex
from ridermusic_playback import get_playback_state, advance_to_next


def _track_to_dict(t):
    return {
        "rating_key": t.ratingKey,
        "title": t.title,
        "artist": getattr(t, "grandparentTitle", None),
        "album": getattr(t, "parentTitle", None),
        "duration_ms": getattr(t, "duration", None),
    }


# Broad mood buckets mapped to keyword substrings, matched against the
# real (very granular) genre tags in the library — e.g. "Disco" needs to
# catch "disco", "cosmic disco", "italo disco", not just a literal tag
# named exactly "Disco", which may not exist.
MOOD_BUCKETS = {
    "chill": ["chill", "mellow", "lounge", "ambient", "downtempo", "easy listening"],
    "dance": ["dance", "disco", "house", "electro", "funk"],
    "r&b": ["r&b", "soul", "motown"],
    "disco": ["disco"],
    "80s": ["80s"],
    "country": ["country", "bluegrass", "americana"],
    "hip hop": ["hip hop", "hip-hop", "rap"],
    "classical": ["classical", "orchestral", "symphony", "baroque"],
    "electronic": ["electronic", "edm", "synth", "techno", "house"],
    "rock": ["rock"],
    "pop": ["pop"],
    "jazz": ["jazz"],
    "reggae": ["reggae", "ska", "dub"],
    "latin": ["latin", "salsa", "bossa", "cumbia"],
}


def _genre_bucket_tracks(section, bucket_key, limit=25, pool_size=60):
    """Pulls a pool of popular tracks (sorted by ratingCount, Plex's
    own documented popularity metric) across every real genre tag
    matching this bucket's keywords, then randomly samples from that
    pool -- so repeated clicks on the same mood pill return varied
    results, biased toward popular tracks rather than always the
    exact same top matches in the same order."""
    bucket_key = bucket_key.lower()
    keywords = MOOD_BUCKETS.get(bucket_key, [bucket_key])
    all_genres = section.listFilterChoices("genre", libtype="track")
    matching_tags = [
        g.title for g in all_genres
        if any(kw in g.title.lower() for kw in keywords)
    ]

    pool = []
    seen_keys = set()
    for tag in matching_tags:
        if len(pool) >= pool_size:
            break
        tracks = section.searchTracks(
            **{"track.genre": tag}, limit=15, sort="ratingCount:desc"
        )
        for t in tracks:
            if t.ratingKey not in seen_keys:
                seen_keys.add(t.ratingKey)
                pool.append(t)
            if len(pool) >= pool_size:
                break

    sample_size = min(limit, len(pool))
    sampled = random.sample(pool, sample_size) if pool else []
    return [_track_to_dict(t) for t in sampled]


def register_guest_routes(app):

    @app.route("/guest/search")
    @require_active_session
    def guest_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"results": []})

        plex = get_plex()
        section = plex.library.section(MUSIC_LIB)
        q_lower = q.lower()

        results = []
        seen_keys = set()

        # Layer 1: literal artist name match -> that artist's own tracks.
        all_artists = section.searchArtists()
        matching_artists = [a for a in all_artists if q_lower in a.title.lower()]
        for artist in matching_artists[:5]:
            for t in artist.tracks()[:15]:
                if t.ratingKey not in seen_keys:
                    seen_keys.add(t.ratingKey)
                    results.append(_track_to_dict(t))

        # Layer 2: literal track title match.
        if len(results) < 25:
            title_matches = section.searchTracks(title__icontains=q, limit=25)
            for t in title_matches:
                if t.ratingKey not in seen_keys:
                    seen_keys.add(t.ratingKey)
                    results.append(_track_to_dict(t))

        # Layer 3: fuzzy hub search fallback, only when nothing literal matched.
        if not results:
            hub_results = plex.search(q, mediatype="track", limit=25)
            for t in hub_results:
                if t.ratingKey not in seen_keys:
                    seen_keys.add(t.ratingKey)
                    results.append(_track_to_dict(t))

        return jsonify({"results": results[:25]})

    @app.route("/guest/mood")
    @require_active_session
    def guest_mood():
        bucket = request.args.get("bucket", "").strip().lower()
        if bucket not in MOOD_BUCKETS:
            return jsonify({"results": []})

        plex = get_plex()
        section = plex.library.section(MUSIC_LIB)
        results = _genre_bucket_tracks(section, bucket)
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

        # Exclude the currently-playing track: it's marked played=0 while
        # it plays (only flipped to played=1 when something advances past
        # it), so without this it shows up as both "Now Playing" and the
        # first item in "Up Next".
        state = get_playback_state(db, session_id)
        current_id = state["current_queue_id"]

        rows = db.execute(
            "SELECT rating_key, title, artist, duration_ms, added_at FROM queue "
            "WHERE session_id = ? AND played = 0 AND id != COALESCE(?, -1) "
            "ORDER BY added_at ASC",
            (session_id, current_id)
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
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiderMusic for Plex</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">

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
    padding-bottom: 3em;
  }
  .wrap { max-width: 480px; margin: 0 auto; padding: 1.25em 1em; }

  header {
    display: flex;
    align-items: center;
    gap: 0.5em;
    margin-bottom: 1.25em;
  }
  header .logo {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    font-size: 1.2em;
  }
  header .logo span { color: var(--cta); }

  h1 {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    font-size: 1.7em;
    line-height: 1.15;
    margin: 0 0 0.6em 0;
  }
  h1 .hl { color: var(--cta); }

  h3 {
    font-family: 'Sora', sans-serif;
    font-weight: 700;
    font-size: 1em;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    margin: 1.4em 0 0.6em 0;
  }

  .search-row {
    display: flex;
    gap: 0.5em;
    margin-bottom: 0.8em;
  }
  #search-box {
    flex: 1;
    padding: 0.85em 1em;
    border-radius: 10px;
    border: none;
    background: var(--panel);
    color: var(--text);
    font-size: 1em;
    font-family: 'Inter', sans-serif;
  }
  #search-box::placeholder { color: var(--text-muted); }
  #search-btn {
    padding: 0.85em 1.2em;
    border-radius: 10px;
    border: none;
    background: var(--cta);
    color: #1a1a1a;
    font-weight: 600;
    font-size: 1em;
    cursor: pointer;
  }

  .mood-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5em;
    margin-bottom: 1em;
  }
  .mood-pill {
    padding: 0.5em 1em;
    border-radius: 999px;
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--accent);
    font-size: 0.9em;
    font-family: 'Inter', sans-serif;
    cursor: pointer;
    white-space: nowrap;
  }
  .mood-pill:active { background: var(--accent); }

  .card {
    background: var(--panel);
    border-radius: 14px;
    padding: 1em;
    margin-bottom: 1em;
  }

  #now-playing-title {
    font-family: 'Sora', sans-serif;
    font-weight: 700;
    font-size: 1.15em;
    margin-bottom: 0.1em;
  }
  #now-playing-artist { color: var(--text-muted); font-size: 0.95em; }
  #now-playing-empty { color: var(--text-muted); }

  .controls {
    display: flex;
    align-items: center;
    gap: 0.8em;
    margin-top: 0.9em;
  }
  .ctrl-btn {
    background: var(--cta);
    border: none;
    color: #1a1a1a;
    width: 3em;
    height: 3em;
    border-radius: 50%;
    font-size: 1.1em;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .ctrl-btn.secondary { background: var(--accent); color: var(--bg); width: 2.5em; height: 2.5em; }
  #volume {
    flex: 1;
    accent-color: var(--cta);
  }

  .result-row, .queue-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.7em 0;
    border-bottom: 1px solid rgba(255,255,255,0.08);
  }
  .result-row:last-child, .queue-row:last-child { border-bottom: none; }
  .result-info .t { font-weight: 500; }
  .result-info .a { color: var(--text-muted); font-size: 0.85em; }
  .add-btn {
    background: var(--cta);
    border: none;
    color: #1a1a1a;
    padding: 0.4em 0.8em;
    border-radius: 8px;
    font-size: 0.85em;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
  }
  .queue-idx { color: var(--accent); font-weight: 700; margin-right: 0.6em; }
  .empty-hint { color: var(--text-muted); padding: 0.6em 0; }

  .spinner {
    display: flex;
    justify-content: center;
    padding: 1.5em 0;
  }
  .spinner div {
    width: 28px;
    height: 28px;
    border: 3px solid rgba(255,255,255,0.15);
    border-top-color: var(--cta);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  #footer {
    margin-top: 2em;
    padding-top: 1em;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-size: 0.75em;
    color: var(--text-muted);
    text-align: center;
    line-height: 1.6;
  }
  #footer a { color: var(--accent); text-decoration: none; }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <span class="logo">Rider<span>Music</span> for Plex</span>
  </header>

  <h1>Be the <span class="hl">DJ</span> for your ride</h1>

  <div class="search-row">
    <input type="text" id="search-box" placeholder="Search songs, artists...">
    <button id="search-btn">Go</button>
  </div>

  <div class="mood-row">
    <button class="mood-pill" data-bucket="chill">Chill</button>
    <button class="mood-pill" data-bucket="dance">Dance</button>
    <button class="mood-pill" data-bucket="r&b">R&amp;B</button>
    <button class="mood-pill" data-bucket="disco">Disco</button>
    <button class="mood-pill" data-bucket="80s">80s</button>
    <button class="mood-pill" data-bucket="country">Country</button>
    <button class="mood-pill" data-bucket="hip hop">Hip Hop</button>
    <button class="mood-pill" data-bucket="classical">Classical</button>
    <button class="mood-pill" data-bucket="electronic">Electronic</button>
    <button class="mood-pill" data-bucket="rock">Rock</button>
    <button class="mood-pill" data-bucket="pop">Pop</button>
    <button class="mood-pill" data-bucket="jazz">Jazz</button>
    <button class="mood-pill" data-bucket="reggae">Reggae</button>
    <button class="mood-pill" data-bucket="latin">Latin</button>
  </div>

  <div id="search-results"></div>

  <h3>Now Playing</h3>
  <div class="card">
    <div id="now-playing-title">Nothing yet</div>
    <div id="now-playing-artist" class="empty-hint" style="display:none"></div>
    <div id="now-playing-empty">Search above and add a song to get started</div>
    <div class="controls" id="controls" style="display:none">
      <button class="ctrl-btn" id="play-pause-btn">⏯</button>
      <button class="ctrl-btn secondary" id="skip-btn">⏭</button>
      <input type="range" id="volume" min="0" max="100" value="50">
    </div>
  </div>

  <h3>Up Next</h3>
  <div class="card" id="queue"></div>

  <div id="footer">
    © 2026 <a href="https://verbenaprojects.com">Verbena Projects LLC</a> ·
    <a href="https://vp-fun.com">vp-fun.com</a> ·
    From the makers of <a href="https://musicmind.vp-fun.com/">MusicMind for Plex</a> ·
    Not affiliated with or endorsed by Plex. Plex is a trademark of Plex, Inc.
  </div>

</div>

<script>
async function refreshPlayback() {
  const res = await fetch('/guest/playback');
  const data = await res.json();
  const titleEl = document.getElementById('now-playing-title');
  const artistEl = document.getElementById('now-playing-artist');
  const emptyEl = document.getElementById('now-playing-empty');
  const controls = document.getElementById('controls');

  if (data.now_playing) {
    titleEl.textContent = (data.is_playing ? '▶ ' : '⏸ ') + data.now_playing.title;
    artistEl.textContent = data.now_playing.artist;
    artistEl.style.display = 'block';
    emptyEl.style.display = 'none';
    controls.style.display = 'flex';
  } else {
    titleEl.textContent = 'Nothing yet';
    artistEl.style.display = 'none';
    emptyEl.style.display = 'block';
    controls.style.display = 'none';
  }
  document.getElementById('volume').value = data.volume;
}

async function refreshQueue() {
  const res = await fetch('/guest/queue');
  const data = await res.json();
  const el = document.getElementById('queue');
  el.innerHTML = '';
  if (data.queue.length === 0) {
    el.innerHTML = '<div class="empty-hint">Queue is empty</div>';
    return;
  }
  data.queue.forEach((t, i) => {
    const div = document.createElement('div');
    div.className = 'queue-row';
    div.innerHTML = '<span><span class="queue-idx">' + (i + 1) + '</span>' +
      t.title + ' — ' + t.artist + '</span>';
    el.appendChild(div);
  });
}

function renderResults(results) {
  const el = document.getElementById('search-results');
  el.innerHTML = '';
  if (results.length === 0) {
    el.innerHTML = '<div class="empty-hint">No matches — try another search</div>';
    return;
  }
  for (const t of results) {
    const div = document.createElement('div');
    div.className = 'result-row';
    div.innerHTML = '<div class="result-info"><div class="t">' + t.title +
      '</div><div class="a">' + t.artist + ' · ' + t.album + '</div></div>';
    const btn = document.createElement('button');
    btn.className = 'add-btn';
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
}

function showSpinner() {
  document.getElementById('search-results').innerHTML =
    '<div class="spinner"><div></div></div>';
}

async function doSearch(q) {
  if (!q) return;
  showSpinner();
  const res = await fetch('/guest/search?q=' + encodeURIComponent(q));
  const data = await res.json();
  renderResults(data.results);
}

async function doMood(bucket) {
  showSpinner();
  const res = await fetch('/guest/mood?bucket=' + encodeURIComponent(bucket));
  const data = await res.json();
  renderResults(data.results);
}

document.getElementById('search-btn').addEventListener('click', () => {
  doSearch(document.getElementById('search-box').value.trim());
});
document.getElementById('search-box').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') doSearch(e.target.value.trim());
});
document.querySelectorAll('.mood-pill').forEach(btn => {
  btn.addEventListener('click', () => doMood(btn.dataset.bucket));
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
</body>
</html>
"""


def register_guest_page_route(app):
    from ridermusic_sessions import require_active_session

    @app.route("/guest")
    @require_active_session
    def guest_page():
        return GUEST_PAGE
