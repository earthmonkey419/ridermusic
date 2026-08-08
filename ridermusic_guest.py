import time
from flask import request, jsonify, g

from config import MAX_QUEUE_ADDS_PER_SESSION
from ridermusic_sessions import get_db, require_active_session, log_action
from ridermusic_player import get_plex


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
