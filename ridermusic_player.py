import requests
from flask import request, Response, abort
from plexapi.server import PlexServer

from config import PLEX_URL, PLEX_TOKEN
from ridermusic_admin import require_admin_auth

_plex = None

def get_plex():
    global _plex
    if _plex is None:
        _plex = PlexServer(PLEX_URL, PLEX_TOKEN)
    return _plex


def register_player_routes(app):

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
