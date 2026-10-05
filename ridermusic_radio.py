"""RiderMusic radio auto-fill.

Keeps a ride from going silent: whenever fewer than RADIO_LOOKAHEAD tracks
are waiting in the queue, a few tracks similar to what the *riders* picked
are appended with source='radio'.

Rules this module lives by:
  * Rider adds always play before radio tracks (queue order is
    "guest first, then radio, each oldest-first"), so radio never makes
    a rider wait behind it.
  * Radio rows never count toward MAX_QUEUE_ADDS_PER_SESSION.
  * Seeds are the riders' own most recent picks, not previous radio
    tracks, so a long ride can't drift away from what was asked for.
  * Nothing in a ride repeats: every track already in the session's
    queue (played, waiting, or removed) is excluded.
  * Similarity comes from MusicMind (tags + optional audio features) when
    MUSICMIND_DB_PATH is set, otherwise Plex sonic analysis. If neither
    yields anything, radio quietly does nothing.
  * Filling runs in a background thread so Next/Skip never waits on it.
  * Any failure here must never break playback.

config.py knobs (both optional):
  RADIO_AUTOFILL  = True   # master switch
  RADIO_LOOKAHEAD = 2      # keep this many tracks waiting
"""
import os
import random
import sqlite3
import threading
import time
from collections import Counter

import config as _config

RUN_SYNC = False          # tests flip this; production always uses a thread
SEED_COUNT = 3            # how many recent rider picks seed a fill
MAX_PER_ARTIST = 2
JITTER = 10.0

_inflight = set()
_inflight_lock = threading.Lock()


def enabled():
    return bool(getattr(_config, "RADIO_AUTOFILL", True))


def lookahead():
    try:
        return max(1, int(getattr(_config, "RADIO_LOOKAHEAD", 2)))
    except (TypeError, ValueError):
        return 2


def _connect():
    conn = sqlite3.connect(_config.DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    """Add queue.source if this database predates radio. Safe to call on
    every start (and from several gunicorn workers at once)."""
    conn = _connect()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(queue)")}
        if "source" not in cols:
            try:
                conn.execute("ALTER TABLE queue ADD COLUMN source TEXT DEFAULT 'guest'")
                conn.commit()
            except sqlite3.OperationalError:
                pass   # another worker got there first
    finally:
        conn.close()


# --- similarity sources --------------------------------------------------

def _mm_available():
    path = getattr(_config, "MUSICMIND_DB_PATH", None)
    return bool(path) and os.path.exists(path)


def _mm_similar(seeds, exclude, limit):
    """MusicMind tag (+ BPM/key when analysed) similarity. Returns a list
    of track dicts ([] = nothing similar), or None if MusicMind is
    unavailable or the query failed."""
    if not _mm_available():
        return None
    seeds = [str(k) for k in seeds if k is not None]
    if not seeds:
        return []
    exclude = {str(k) for k in exclude} | set(seeds)
    try:
        conn = sqlite3.connect(f"file:{_config.MUSICMIND_DB_PATH}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            ph = ",".join("?" * len(seeds))
            tag_rows = conn.execute(
                f"SELECT tag, COUNT(*) AS n FROM track_tags "
                f"WHERE rating_key IN ({ph}) GROUP BY tag ORDER BY n DESC LIMIT 40",
                seeds,
            ).fetchall()
            if not tag_rows:
                return []
            tag_weight = {r["tag"]: r["n"] / len(seeds) for r in tag_rows}

            has_feat = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_audio_features'"
            ).fetchone() is not None
            track_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
            artist_expr = "COALESCE(t.real_artist, t.artist)" if "real_artist" in track_cols else "t.artist"

            seed_bpm = seed_key = seed_scale = None
            if has_feat:
                feats = conn.execute(
                    f"SELECT bpm, key, scale FROM track_audio_features WHERE rating_key IN ({ph})",
                    seeds,
                ).fetchall()
                bpms = sorted(f["bpm"] for f in feats if f["bpm"] is not None)
                if bpms:
                    seed_bpm = bpms[len(bpms) // 2]
                keys = Counter((f["key"], f["scale"]) for f in feats if f["key"] is not None)
                if keys:
                    (seed_key, seed_scale), _ = keys.most_common(1)[0]

            tags = list(tag_weight)
            tph = ",".join("?" * len(tags))
            if has_feat:
                feat_cols = ", taf.bpm AS bpm, taf.key AS fkey, taf.scale AS scale"
                feat_join = "LEFT JOIN track_audio_features taf ON taf.rating_key = t.rating_key"
            else:
                feat_cols = ", NULL AS bpm, NULL AS fkey, NULL AS scale"
                feat_join = ""
            rows = conn.execute(f"""
                SELECT t.rating_key, t.title, {artist_expr} AS artist, t.album,
                       t.duration_ms,
                       GROUP_CONCAT(tt.tag, char(31)) AS shared{feat_cols}
                FROM tracks t
                JOIN track_tags tt ON tt.rating_key = t.rating_key
                {feat_join}
                WHERE tt.tag IN ({tph})
                GROUP BY t.rating_key
            """, tags).fetchall()
        finally:
            conn.close()
    except Exception:
        return None

    scored = []
    for r in rows:
        if str(r["rating_key"]) in exclude:
            continue
        score = 10 * sum(tag_weight.get(t, 0) for t in r["shared"].split(chr(31)))
        if seed_bpm is not None and r["bpm"] is not None:
            score += max(0, 5 - abs(seed_bpm - r["bpm"]) / 2)
        if seed_key is not None and r["fkey"] == seed_key and r["scale"] == seed_scale:
            score += 3
        scored.append((score + random.uniform(0, JITTER), r))
    scored.sort(key=lambda x: -x[0])

    out, per_artist = [], {}
    for _, r in scored:
        try:
            rk = int(r["rating_key"])
        except (TypeError, ValueError):
            continue
        a = r["artist"]
        if per_artist.get(a, 0) >= MAX_PER_ARTIST:
            continue
        per_artist[a] = per_artist.get(a, 0) + 1
        out.append({"rating_key": rk, "title": r["title"], "artist": a,
                    "duration_ms": r["duration_ms"] or 0})
        if len(out) >= limit:
            break
    return out


def _sonic_similar(seeds, exclude, limit):
    """Plex sonic analysis (needs Plex Pass + analysed library). [] on any
    problem -- radio just stays quiet."""
    try:
        from ridermusic_player import get_plex
        plex = get_plex()
        seed = plex.fetchItem(int(random.choice(list(seeds))))
        ex = {str(k) for k in exclude} | {str(s) for s in seeds}
        found = list(seed.sonicallySimilar(limit=limit * 4))
        random.shuffle(found)   # near neighbours, shuffled: close but not repetitive
        out, per_artist = [], {}
        for t in found:
            if str(t.ratingKey) in ex or getattr(t, "type", "track") != "track":
                continue
            a = getattr(t, "originalTitle", None) or getattr(t, "grandparentTitle", None) or ""
            if per_artist.get(a, 0) >= MAX_PER_ARTIST:
                continue
            per_artist[a] = per_artist.get(a, 0) + 1
            out.append({"rating_key": int(t.ratingKey), "title": t.title, "artist": a,
                        "duration_ms": getattr(t, "duration", 0) or 0})
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _candidates(seeds, exclude, limit):
    found = _mm_similar(seeds, exclude, limit)
    if found:
        return found
    # MusicMind absent, failed, or has nothing for these seeds.
    return _sonic_similar(seeds, exclude, limit)


# --- planning + filling --------------------------------------------------

def _plan(conn, session_id):
    """(need, seeds, exclude) for this session right now."""
    sess = conn.execute(
        "SELECT 1 FROM sessions WHERE session_id = ? AND ended_by_admin = 0 AND expires_at > ?",
        (session_id, time.time()),
    ).fetchone()
    if not sess:
        return 0, [], set()
    st = conn.execute(
        "SELECT current_queue_id FROM playback_state WHERE session_id = ?", (session_id,)
    ).fetchone()
    cur = st["current_queue_id"] if st else None
    if not cur:
        return 0, [], set()

    upcoming = conn.execute(
        "SELECT COUNT(*) AS c FROM queue WHERE session_id = ? AND played = 0 AND id != ?",
        (session_id, cur),
    ).fetchone()["c"]
    need = lookahead() - upcoming

    seeds = [r["rating_key"] for r in conn.execute(
        "SELECT rating_key FROM queue WHERE session_id = ? "
        "AND COALESCE(source, 'guest') != 'radio' AND played != 2 "
        "ORDER BY added_at DESC LIMIT ?", (session_id, SEED_COUNT))]
    if not seeds:
        row = conn.execute("SELECT rating_key FROM queue WHERE id = ?", (cur,)).fetchone()
        seeds = [row["rating_key"]] if row else []

    exclude = {str(r["rating_key"]) for r in conn.execute(
        "SELECT rating_key FROM queue WHERE session_id = ?", (session_id,))}
    return need, seeds, exclude


def _fill(session_id):
    conn = _connect()
    try:
        need, seeds, exclude = _plan(conn, session_id)
        if need <= 0 or not seeds:
            return 0
        cands = _candidates(seeds, exclude, need + 3)   # headroom for races
        if not cands:
            return 0

        # Another worker may have filled in the meantime: re-plan under a
        # write lock, then insert only what's still needed and not present.
        conn.execute("BEGIN IMMEDIATE")
        need, _, exclude = _plan(conn, session_id)
        added, now = 0, time.time()
        for c in cands:
            if added >= need:
                break
            if str(c["rating_key"]) in exclude:
                continue
            conn.execute(
                "INSERT INTO queue (session_id, rating_key, title, artist, duration_ms, "
                "added_at, played, source) VALUES (?, ?, ?, ?, ?, ?, 0, 'radio')",
                (session_id, c["rating_key"], c["title"], c["artist"],
                 c["duration_ms"], now + added * 0.001),
            )
            exclude.add(str(c["rating_key"]))
            added += 1
        if added:
            conn.execute(
                "INSERT INTO session_actions (session_id, action_type, detail, ts) "
                "VALUES (?, 'radio_fill', ?, ?)",
                (session_id, f"{added} track{'s' if added != 1 else ''}", time.time()),
            )
        conn.commit()
        return added
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        conn.close()


def _worker(session_id):
    try:
        _fill(session_id)
    finally:
        with _inflight_lock:
            _inflight.discard(session_id)


def schedule_top_up(session_id):
    """Ask for a top-up. Returns immediately (unless RUN_SYNC)."""
    if not enabled() or not session_id:
        return
    with _inflight_lock:
        if session_id in _inflight:
            return
        _inflight.add(session_id)
    if RUN_SYNC:
        _worker(session_id)
        return
    t = threading.Thread(target=_worker, args=(session_id,), daemon=True)
    t.start()
