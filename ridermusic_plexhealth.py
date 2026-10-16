"""Plex connection health: turns a mysterious 'track_not_found' into a
clear, specific message when Plex rejects our token or can't be reached.

check() does one cheap authenticated request and caches the verdict:
  ok | unauthorized (HTTP 401) | unreachable | error (other HTTP status)
The driver dashboard shows a banner for anything but ok; guest-facing
routes use is_down() to say 'music unavailable' instead of blaming the
song.
"""
import threading
import time

import requests

import config as _config

_lock = threading.Lock()
_state = {"state": "unknown", "detail": "", "checked_at": 0.0}


def _set(state, detail=""):
    with _lock:
        changed = _state["state"] != state
        _state.update(state=state, detail=detail, checked_at=time.time())
    if changed:
        print(f"[plex-health] {state}" + (f": {detail}" if detail else ""), flush=True)


def check(max_age=30):
    """Return {'state', 'detail'}; re-probes Plex if the cached verdict is
    older than max_age seconds."""
    with _lock:
        fresh = (time.time() - _state["checked_at"]) < max_age
        snap = dict(_state)
    if fresh:
        return {"state": snap["state"], "detail": snap["detail"]}
    try:
        r = requests.get(
            _config.PLEX_URL.rstrip("/") + "/library/sections",
            headers={"X-Plex-Token": _config.PLEX_TOKEN},
            timeout=3,
        )
        if r.status_code == 200:
            _set("ok")
        elif r.status_code == 401:
            _set("unauthorized", "Plex rejected the access token (HTTP 401)")
        else:
            _set("error", f"Plex answered HTTP {r.status_code}")
    except Exception as e:
        _set("unreachable", f"Can't reach Plex ({type(e).__name__})")
    with _lock:
        return {"state": _state["state"], "detail": _state["detail"]}


def is_down(max_age=5):
    """True if Plex is currently rejecting us or unreachable. Used on
    failure paths only, so the extra probe is rare and short-lived."""
    return check(max_age)["state"] in ("unauthorized", "unreachable", "error")


def register_plex_error_handlers(app):
    """Uncaught Plex auth/connection errors anywhere (search, browse...)
    become a clean 503 JSON instead of a 500 page."""
    from flask import jsonify
    from plexapi.exceptions import Unauthorized

    def _unavailable(exc):
        check(max_age=5)
        return jsonify({"error": "music_unavailable"}), 503

    app.register_error_handler(Unauthorized, _unavailable)
    app.register_error_handler(requests.exceptions.ConnectionError, _unavailable)
    app.register_error_handler(requests.exceptions.Timeout, _unavailable)
