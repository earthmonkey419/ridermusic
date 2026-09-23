import os
import requests
from urllib.parse import quote
from flask import request, Response, abort, send_file
from plexapi.server import PlexServer

from config import PLEX_URL, PLEX_TOKEN
from ridermusic_admin import require_admin_auth

_plex = None

def get_plex():
    global _plex
    if _plex is None:
        _plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    return _plex


_FALLBACK_ART = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "android-chrome-512x512.png")


def _fallback_art():
    resp = send_file(_FALLBACK_ART, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


def register_player_routes(app):

    @app.route("/player/art/<int:rating_key>")
    def player_art(rating_key):
        # No login check on purpose: iOS's lock-screen artwork loader
        # can't be relied on to send cookies. Instead, only tracks in
        # the active ride's queue are served, so this can't be used to
        # browse the library -- and the Plex token never leaves here.
        from ridermusic_sessions import get_db, get_active_session
        db = get_db()
        active = get_active_session(db)
        if not active:
            return _fallback_art()
        in_queue = db.execute(
            "SELECT 1 FROM queue WHERE session_id = ? AND CAST(rating_key AS TEXT) = ? LIMIT 1",
            (active["session_id"], str(rating_key))
        ).fetchone()
        if not in_queue:
            return _fallback_art()

        try:
            track = get_plex().fetchItem(rating_key)
            thumb = track.parentThumb or track.thumb or track.grandparentThumb
        except Exception:
            thumb = None
        if not thumb:
            return _fallback_art()

        # Plex's photo transcoder: square 512px JPEG, the size/format
        # MusicLounge's working lock-screen art uses.
        url = (f"{PLEX_URL}/photo/:/transcode?width=512&height=512&minSize=1"
               f"&upscale=1&url={quote(thumb, safe='')}&X-Plex-Token={PLEX_TOKEN}")
        try:
            upstream = requests.get(url, timeout=6)
        except Exception:
            return _fallback_art()
        if upstream.status_code != 200 or not upstream.content:
            return _fallback_art()

        resp = Response(upstream.content,
                        content_type=upstream.headers.get("Content-Type", "image/jpeg"))
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.route("/player/stream/<int:rating_key>")
    @require_admin_auth
    def player_stream(rating_key):
        plex = get_plex()
        try:
            track = plex.fetchItem(rating_key)
        except Exception:
            abort(404)

        part = track.media[0].parts[0]
        real_url = f"{PLEX_URL}{part.key}?X-Plex-Token={PLEX_TOKEN}"

        headers = {}
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]

        plex_resp = requests.get(real_url, headers=headers, stream=True)

        def generate():
            for chunk in plex_resp.iter_content(chunk_size=65536):
                yield chunk

        passthrough_headers = {}
        for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            if h in plex_resp.headers:
                passthrough_headers[h] = plex_resp.headers[h]

        return Response(
            generate(),
            status=plex_resp.status_code,
            headers=passthrough_headers
        )
