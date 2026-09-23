"""RiderMusic v2: real album art for the lock screen / CarPlay.
  - New /player/art/<rating_key>: 512px JPEG of the track's album art,
    proxied from Plex (token stays server-side). Only serves tracks in
    the active ride's queue; falls back to the RiderMusic icon.
  - Artwork served cacheable (exempt from app-wide no-store).
  - Metadata re-applied on 'playing', after iOS has committed the new
    track, instead of only before play() as now.
Apply on top of patch_mediasession_race_pwa + patch_mediasession_artwork.
Run from the ridermusic repo root, on the v2 branch."""

import pathlib
import sys

def patch(path, old, new):
    p = pathlib.Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        print(f"FAIL {path}: expected 1 match, found {count} — old_str below")
        print(repr(old[:200]))
        sys.exit(1)
    p.write_text(text.replace(old, new))
    print(f"OK   {path}: patched")

# 1. Art route
patch("ridermusic_player.py",
"""import requests
from flask import request, Response, abort
""",
"""import os
import requests
from urllib.parse import quote
from flask import request, Response, abort, send_file
""")

patch("ridermusic_player.py",
"""def register_player_routes(app):
""",
"""_FALLBACK_ART = os.path.join(os.path.dirname(os.path.abspath(__file__)),
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
""")

# 2. Exempt /player/art/ from app-wide no-store (after_request would
#    otherwise overwrite the route's own header)
patch("app.py",
"""    if request.path.startswith("/static/") and request.path.rsplit(".", 1)[-1].lower() in ("png", "jpg", "jpeg", "ico"):""",
"""    if request.path.startswith("/player/art/") or (
        request.path.startswith("/static/") and request.path.rsplit(".", 1)[-1].lower() in ("png", "jpg", "jpeg", "ico")
    ):""")

# 3. Dashboard: album art URL + re-apply metadata once playing
patch("ridermusic_admin.py",
"""function setMediaMetadata(np) {
  if (!hasMediaSession || !np) return;
  try {""",
"""let lastMediaNp = null;

function setMediaMetadata(np) {
  if (!hasMediaSession || !np) return;
  lastMediaNp = np;
  try {""")

patch("ridermusic_admin.py",
"""      artwork: [
        { src: location.origin + '/static/android-chrome-512x512.png', sizes: '512x512', type: 'image/png' },
        { src: location.origin + '/static/apple-touch-icon.png', sizes: '180x180', type: 'image/png' },
      ],""",
"""      artwork: [
        { src: location.origin + '/player/art/' + np.rating_key, sizes: '512x512', type: 'image/jpeg' },
      ],""")

patch("ridermusic_admin.py",
"""  audio.addEventListener('play', () => { navigator.mediaSession.playbackState = 'playing'; });""",
"""  audio.addEventListener('play', () => { navigator.mediaSession.playbackState = 'playing'; });
  // iOS can reset Now Playing info when a new src loads; setting it
  // again once audio is actually playing makes it stick.
  audio.addEventListener('playing', () => { if (lastMediaNp) setMediaMetadata(lastMediaNp); });""")

print("\nAll patches applied. Next: pm2 restart ridermusic")
