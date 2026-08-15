import time
import random
import os
import sqlite3
from flask import request, jsonify, g

import config as _config
from config import MAX_QUEUE_ADDS_PER_SESSION, MUSIC_LIB
from ridermusic_sessions import get_db, require_active_session, log_action
from ridermusic_player import get_plex
from ridermusic_playback import get_playback_state, advance_to_next

# Soft dependency on MusicMind for Plex. If MUSICMIND_DB_PATH isn't set
# in config.py (or the file it points to doesn't exist), everything
# below just falls back to the existing live-Plex behavior -- RiderMusic
# must work identically whether or not MusicMind is installed.
MUSICMIND_DB_PATH = getattr(_config, "MUSICMIND_DB_PATH", None)


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

# Some buckets need a keyword to cast a wide net (e.g. "country" has to
# catch "country rock", "country pop", etc.) but that net also catches
# genuinely different genres that just happen to have "country" as a
# modifier rather than the core style -- "country blues" is blues,
# "country jazz" is jazz. These are excluded per-bucket even though
# their tag title contains the bucket's keyword. Verified against real
# library data (see diagnose_country*.py) before excluding anything --
# each of these legitimately carries the tag, it's just not what a
# rider expects from a "Country" tap.
MOOD_BUCKET_EXCLUDED_TAGS = {
    "country": {
        "country blues",
        "country jazz",
        "country fusion",
        "country reggae",
        "psychedelic country",
        "country influences",
        "country-inflected",
        "country-influenced",
        "country rap",
        "country-influenced pop",
        "country-infused house",
        "dance-pop country",
    },
}


# --- Short-lived, shared result cache ----------------------------------
#
# Search and mood results are paged for infinite scroll. Rather than
# re-querying Plex (and, for mood buckets, re-shuffling) on every scroll
# tick -- or every new rider's first tap -- the full candidate set is
# built once and cached, shared across ALL sessions (not per-rider):
# bucket/search composition depends on the library, not on who's asking,
# so there's no reason to pay the rebuild cost more than once per TTL
# window. This matters most for mood buckets: a broad bucket like "rock"
# can mean 60+ sequential Plex queries to build (~25s on this library) --
# measured directly, see timing_check.py -- so a per-session cache would
# have made every new guest eat that cost individually.
#
# NOTE: this is a plain in-process dict. It assumes a single gunicorn
# worker. If RiderMusic is ever run with multiple workers, a request
# can land on a different worker than the one that built the cache and
# miss it -- it would silently rebuild rather than error, just losing
# the sharing benefit across workers.
_RESULT_CACHE = {}
_CACHE_TTL = 600          # seconds -- search results
_MOOD_CACHE_TTL = 3600    # seconds -- mood pools are expensive to rebuild
                          # (one Plex query per matching genre tag) and
                          # don't depend on who's asking, so they're cached
                          # much longer and shared across every session.


def _cache_set(key, results, ttl=_CACHE_TTL):
    _RESULT_CACHE[key] = (time.time(), results, ttl)
    _cache_evict_stale()


def _cache_get(key):
    entry = _RESULT_CACHE.get(key)
    if not entry:
        return None
    ts, results, ttl = entry
    if time.time() - ts > ttl:
        _RESULT_CACHE.pop(key, None)
        return None
    return results


def _cache_evict_stale():
    now = time.time()
    stale = [k for k, (ts, _, ttl) in _RESULT_CACHE.items() if now - ts > ttl]
    for k in stale:
        _RESULT_CACHE.pop(k, None)


def _paged_response(all_results, offset, limit):
    page = all_results[offset:offset + limit]
    has_more = (offset + limit) < len(all_results)
    return jsonify({
        "results": page,
        "has_more": has_more,
        "next_offset": offset + len(page),
    })


def _build_search_results(section, plex, q):
    """Same three-layer literal-then-fuzzy search as before, just with
    wider candidate limits so there's enough to page through."""
    q_lower = q.lower()
    results = []
    seen_keys = set()

    # Layer 1: literal artist name match -> that artist's own tracks.
    all_artists = section.searchArtists()
    matching_artists = [a for a in all_artists if q_lower in a.title.lower()]
    for artist in matching_artists[:8]:
        for t in artist.tracks()[:20]:
            if t.ratingKey not in seen_keys:
                seen_keys.add(t.ratingKey)
                results.append(_track_to_dict(t))

    # Layer 2: literal track title match.
    if len(results) < 75:
        title_matches = section.searchTracks(title__icontains=q, limit=75)
        for t in title_matches:
            if t.ratingKey not in seen_keys:
                seen_keys.add(t.ratingKey)
                results.append(_track_to_dict(t))

    # Layer 3: fuzzy hub search fallback, only when nothing literal matched.
    if not results:
        hub_results = plex.search(q, mediatype="track", limit=50)
        for t in hub_results:
            if t.ratingKey not in seen_keys:
                seen_keys.add(t.ratingKey)
                results.append(_track_to_dict(t))

    return results


def _musicmind_search(q, limit=500):
    """Fast path: search MusicMind's local tracks table directly
    (title/artist LIKE) instead of live Plex calls. The live-Plex
    literal-artist layer alone costs one section.searchArtists() call
    plus one additional Plex round-trip PER matching artist just to
    fetch their tracks -- this replaces all of that with two local SQL
    queries, at whatever result count actually matches (not capped to
    8 artists like the live path had to be to stay fast).

    Same ordering as the live three-layer search: artist matches first,
    then title matches not already included. No fuzzy layer here --
    that's Plex's hub search specifically, MusicMind has no equivalent,
    so the dispatcher below falls back to live Plex fuzzy search only
    if this returns literally nothing.
    """
    q_like = f"%{q.lower()}%"
    conn = _musicmind_conn()
    try:
        conn.row_factory = sqlite3.Row
        artist_rows = conn.execute(
            "SELECT rating_key, title, artist, album, duration_ms "
            "FROM tracks WHERE LOWER(artist) LIKE ?",
            (q_like,),
        ).fetchall()
        title_rows = conn.execute(
            "SELECT rating_key, title, artist, album, duration_ms "
            "FROM tracks WHERE LOWER(title) LIKE ?",
            (q_like,),
        ).fetchall()
    finally:
        conn.close()

    def _row_to_dict(r):
        try:
            rating_key = int(r["rating_key"])
        except (TypeError, ValueError):
            return None
        return {
            "rating_key": rating_key,
            "title": r["title"],
            "artist": r["artist"],
            "album": r["album"],
            "duration_ms": r["duration_ms"],
        }

    results = []
    seen = set()
    for r in artist_rows:
        d = _row_to_dict(r)
        if d and d["rating_key"] not in seen:
            seen.add(d["rating_key"])
            results.append(d)
    for r in title_rows:
        d = _row_to_dict(r)
        if d and d["rating_key"] not in seen:
            seen.add(d["rating_key"])
            results.append(d)

    return results[:limit]


def _build_search_pool(section, plex, q):
    """Resolve search results. Prefers MusicMind's local data when
    available (fast, no per-artist Plex round-trips); falls back to
    the live three-layer Plex search otherwise -- including on ANY
    failure of the MusicMind path, or if it genuinely finds nothing
    (MusicMind has no fuzzy-search equivalent to Plex's hub search,
    so a real fuzzy-only match still needs the live fallback)."""
    if _musicmind_available():
        try:
            results = _musicmind_search(q)
            if results:
                return results
        except Exception:
            pass  # fall through to live Plex below
    return _build_search_results(section, plex, q)


def _genre_bucket_pool(section, bucket_key, pool_size=200):
    """Pulls a pool of popular tracks (sorted by ratingCount, Plex's own
    documented popularity metric) across every real genre tag matching
    this bucket's keywords, then shuffles the whole pool once. Paging
    slices through the shuffled pool, so scrolling gives a varied,
    non-repeating sequence instead of re-sampling (and reshuffling) on
    every page like the old single-shot version did."""
    bucket_key = bucket_key.lower()
    keywords = MOOD_BUCKETS.get(bucket_key, [bucket_key])
    excluded = MOOD_BUCKET_EXCLUDED_TAGS.get(bucket_key, set())
    all_genres = section.listFilterChoices("genre", libtype="track")
    matching_tags = [
        g.title for g in all_genres
        if any(kw in g.title.lower() for kw in keywords)
        and g.title.lower() not in excluded
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

    random.shuffle(pool)
    return [_track_to_dict(t) for t in pool]


def _musicmind_available():
    """True only if MUSICMIND_DB_PATH is configured AND the file
    actually exists on disk right now. Checked fresh on every call
    (cheap) rather than cached, so a MusicMind install/removal takes
    effect without a RiderMusic restart."""
    return bool(MUSICMIND_DB_PATH) and os.path.exists(MUSICMIND_DB_PATH)


def _musicmind_conn():
    """Read-only connection -- RiderMusic must never write to
    MusicMind's database, and must never risk lock contention with
    MusicMind's own process writing to it concurrently."""
    return sqlite3.connect(f"file:{MUSICMIND_DB_PATH}?mode=ro", uri=True)


def _mood_pool_from_musicmind(bucket_key, pool_size=500):
    """Fast path: resolve a mood bucket from MusicMind's own tagging
    data (track_tags + tracks tables) instead of live per-tag Plex
    queries. Measured at ~0.07s for the whole library locally, versus
    5-26s doing the equivalent live against Plex (see timing_check.py)
    -- because it's one local SQL pass instead of dozens of sequential
    Plex API round-trips. MusicMind's tags also cover far more of the
    library (98.5% in a recent export) than Plex's genre field (67%).

    Tradeoff, accepted deliberately: this reflects MusicMind's most
    recent analysis pass, not the live Plex library. If a track was
    removed from Plex since then, adding it to the queue fails
    gracefully (guest_queue_add already returns "track_not_found") --
    rare in practice, not worth reconciling live for.
    """
    keywords = MOOD_BUCKETS.get(bucket_key, [bucket_key])
    excluded = MOOD_BUCKET_EXCLUDED_TAGS.get(bucket_key, set())

    conn = _musicmind_conn()
    try:
        conn.row_factory = sqlite3.Row
        like_clauses = " OR ".join(["LOWER(tag) LIKE ?"] * len(keywords))
        params = [f"%{kw.lower()}%" for kw in keywords]
        tag_rows = conn.execute(
            f"SELECT DISTINCT rating_key, tag FROM track_tags WHERE {like_clauses}",
            params,
        ).fetchall()

        matched_keys = []
        seen = set()
        for row in tag_rows:
            if row["tag"].strip().lower() in excluded:
                continue
            if row["rating_key"] not in seen:
                seen.add(row["rating_key"])
                matched_keys.append(row["rating_key"])

        if not matched_keys:
            return []

        placeholders = ",".join("?" * len(matched_keys))
        track_rows = conn.execute(
            "SELECT rating_key, title, artist, album, duration_ms "
            f"FROM tracks WHERE rating_key IN ({placeholders})",
            matched_keys,
        ).fetchall()
    finally:
        conn.close()

    pool = []
    for r in track_rows:
        try:
            rating_key = int(r["rating_key"])
        except (TypeError, ValueError):
            continue
        pool.append({
            "rating_key": rating_key,
            "title": r["title"],
            "artist": r["artist"],
            "album": r["album"],
            "duration_ms": r["duration_ms"],
        })

    random.shuffle(pool)
    return pool[:pool_size]


def _build_mood_pool(section, bucket_key):
    """Resolve a mood bucket's track pool. Prefers MusicMind's local
    tagging data when available (fast, more complete); falls back to
    live Plex genre queries otherwise -- including on ANY failure of
    the MusicMind path (missing table, locked db, schema change), so
    a MusicMind hiccup can never break a guest's mood pill tap."""
    if _musicmind_available():
        try:
            pool = _mood_pool_from_musicmind(bucket_key)
            if pool:
                return pool
        except Exception:
            pass  # fall through to live Plex below
    return _genre_bucket_pool(section, bucket_key)


def register_guest_routes(app):

    @app.route("/guest/search")
    @require_active_session
    def guest_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"results": [], "has_more": False, "next_offset": 0})

        offset = max(0, request.args.get("offset", 0, type=int) or 0)
        limit = min(50, max(1, request.args.get("limit", 20, type=int) or 20))

        cache_key = ("search", q.lower())

        all_results = _cache_get(cache_key)
        if all_results is None:
            plex = get_plex()
            section = plex.library.section(MUSIC_LIB)
            all_results = _build_search_pool(section, plex, q)
            _cache_set(cache_key, all_results)

        return _paged_response(all_results, offset, limit)

    @app.route("/guest/mood")
    @require_active_session
    def guest_mood():
        bucket = request.args.get("bucket", "").strip().lower()
        if bucket not in MOOD_BUCKETS:
            return jsonify({"results": [], "has_more": False, "next_offset": 0})

        offset = max(0, request.args.get("offset", 0, type=int) or 0)
        limit = min(50, max(1, request.args.get("limit", 20, type=int) or 20))

        cache_key = ("mood", bucket)

        all_results = _cache_get(cache_key)
        if all_results is None:
            plex = get_plex()
            section = plex.library.section(MUSIC_LIB)
            all_results = _build_mood_pool(section, bucket)
            _cache_set(cache_key, all_results, ttl=_MOOD_CACHE_TTL)

        return _paged_response(all_results, offset, limit)

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
<title>RiderMusic Jukebox for Plex</title>
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
  .end-hint { text-align: center; font-size: 0.85em; }

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
  #loading-more div {
    width: 20px;
    height: 20px;
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

  #back-to-top {
    position: fixed;
    bottom: 1.25em;
    right: 1.25em;
    width: 3em;
    height: 3em;
    border-radius: 50%;
    border: none;
    background: var(--cta);
    color: #1a1a1a;
    font-size: 1.3em;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 10px rgba(0,0,0,0.35);
    z-index: 100;
  }
  #back-to-top:active { background: var(--accent); }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <span class="logo">Rider<span>Music</span> Jukebox for Plex</span>
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

  <div id="search-results"></div>

  <div id="footer">
    © 2026 <a href="https://verbenaprojects.com">Verbena Projects LLC</a> ·
    <a href="https://vp-fun.com">vp-fun.com</a> ·
    From the makers of <a href="https://musicmind.vp-fun.com/">MusicMind for Plex</a> ·
    Not affiliated with or endorsed by Plex. Plex is a trademark of Plex, Inc.
  </div>

</div>

<button id="back-to-top" aria-label="Back to top">↑</button>

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

// --- Infinite-scroll search/mood results -------------------------------

const RESULTS_PAGE_SIZE = 20;

let currentQueryType = null;   // 'search' or 'mood'
let currentQueryValue = null;
let currentOffset = 0;
let isLoadingResults = false;
let hasMoreResults = false;
let resultsObserver = null;

function addTrackButton(t) {
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
  return div;
}

function appendResults(results, isFirstPage) {
  const el = document.getElementById('search-results');
  if (isFirstPage && results.length === 0) {
    el.innerHTML = '<div class="empty-hint">No matches — try another search</div>';
    return;
  }
  for (const t of results) {
    el.appendChild(addTrackButton(t));
  }
}

function showSpinner() {
  document.getElementById('search-results').innerHTML =
    '<div class="spinner"><div></div></div>';
}

function showLoadingMore() {
  removeLoadingMore();
  const el = document.getElementById('search-results');
  const div = document.createElement('div');
  div.id = 'loading-more';
  div.className = 'spinner';
  div.innerHTML = '<div></div>';
  el.appendChild(div);
}

function removeLoadingMore() {
  const existing = document.getElementById('loading-more');
  if (existing) existing.remove();
}

function removeSentinel() {
  const existing = document.getElementById('results-sentinel');
  if (existing) existing.remove();
  if (resultsObserver) {
    resultsObserver.disconnect();
    resultsObserver = null;
  }
}

function ensureSentinel() {
  removeSentinel();
  if (!hasMoreResults) return;

  const el = document.getElementById('search-results');
  const sentinel = document.createElement('div');
  sentinel.id = 'results-sentinel';
  sentinel.style.height = '1px';
  el.appendChild(sentinel);

  resultsObserver = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && hasMoreResults && !isLoadingResults) {
      loadResults(false);
    }
  }, { rootMargin: '200px' });
  resultsObserver.observe(sentinel);
}

function maybeShowEndHint(results) {
  if (hasMoreResults || results.length === 0) return;
  const el = document.getElementById('search-results');
  if (el.querySelector('.end-hint')) return;
  const doneEl = document.createElement('div');
  doneEl.className = 'empty-hint end-hint';
  doneEl.textContent = "— That's everything —";
  el.appendChild(doneEl);
}

async function loadResults(isFirstPage) {
  if (isLoadingResults) return;
  if (!isFirstPage && !hasMoreResults) return;
  isLoadingResults = true;

  if (isFirstPage) {
    currentOffset = 0;
    removeSentinel();
    showSpinner();
  } else {
    showLoadingMore();
  }

  const params = new URLSearchParams({
    offset: String(currentOffset),
    limit: String(RESULTS_PAGE_SIZE),
  });
  let url;
  if (currentQueryType === 'search') {
    params.set('q', currentQueryValue);
    url = '/guest/search?' + params.toString();
  } else {
    params.set('bucket', currentQueryValue);
    url = '/guest/mood?' + params.toString();
  }

  let data;
  try {
    const res = await fetch(url);
    data = await res.json();
  } catch (e) {
    data = { results: [], has_more: false, next_offset: currentOffset };
  }

  if (isFirstPage) {
    document.getElementById('search-results').innerHTML = '';
  } else {
    removeLoadingMore();
  }

  appendResults(data.results, isFirstPage);
  hasMoreResults = !!data.has_more;
  currentOffset = typeof data.next_offset === 'number'
    ? data.next_offset
    : currentOffset + data.results.length;
  isLoadingResults = false;

  maybeShowEndHint(data.results);
  ensureSentinel();
}

function doSearch(q) {
  if (!q) return;
  currentQueryType = 'search';
  currentQueryValue = q;
  loadResults(true);
}

function doMood(bucket) {
  currentQueryType = 'mood';
  currentQueryValue = bucket;
  loadResults(true);
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

document.getElementById('back-to-top').addEventListener('click', () => {
  window.scrollTo({ top: 0, behavior: 'smooth' });
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
