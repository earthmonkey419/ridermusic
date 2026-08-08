# RiderMusic

QR-based guest DJ system for rideshare passengers — from the makers of
[MusicMind for Plex](https://musicmind.vp-fun.com/).

A rideshare driver running Plex places a QR code in the car. Passengers
scan it, search the driver's music library, and queue songs — no app
install, no Plex account, no login. The driver's own phone plays the
music through the car stereo; the passenger's phone is just a remote.

**Status: early, working build.** The core loop (guest search/queue,
driver playback, admin session control) runs end to end and has been
tested on real hardware. Not yet packaged for easy install by other
drivers — that's in progress. Standalone product: does not require
MusicMind to run.

## How it works

```
Guest phone (portal)  ->  RiderMusic backend  ->  Driver's phone (player)  ->  car stereo
                              |
                              v
                        Plex Media Server
```

- **Guest portal:** search, browse, add to queue, play/pause/skip,
  volume (clamped to a driver-set ceiling)
- **Driver player:** a browser page on the driver's own phone, streams
  audio directly from Plex, no Plexamp involved
- **Admin dashboard:** live session status, End Session button,
  recent activity log
- **Sessions:** passive by default — first scan starts a session,
  it auto-expires, driver only needs to act to end one early

## Requirements

- Python 3.12+
- A Plex Media Server with a music library
- A way to expose the backend to the internet (Cloudflare Tunnel, a
  reverse proxy, etc.) — both the driver's phone and guest phones need
  to reach it from outside your home network while on the road
- Recommended: PM2 or similar process manager

## Setup

```bash
git clone https://github.com/earthmonkey419/ridermusic.git
cd ridermusic
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# edit config.py: PLEX_URL, PLEX_TOKEN, MUSIC_LIB, ADMIN_PASSWORD
# set COOKIE_SECURE = True (requires HTTPS — see below)

python init_db.py
python app.py
```

App runs on port `6869` by default.

**Before real use:** `COOKIE_SECURE` must be `True`, which requires
the app to be served over HTTPS. `False` is for local development
only — cookies won't persist in a real browser over plain HTTP.

**Getting a real Plex token:** see
[Plex's own guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

## Using it

1. Expose the app to the internet via your tunnel/reverse proxy of choice
2. Print a QR code pointing at `https://your-domain/join` and place it
   in the car
3. As the driver: log in at `/admin/login`, then open `/player` on
   your phone once a ride session is active — that page is what
   should be paired to your car stereo (Bluetooth or aux)
4. Use `/admin/dashboard` to monitor sessions and end one early if needed

## Known limitations (current)

- **Search quality** depends on Plex's own hub search — relevance-based,
  not literal. RiderMusic layers a literal artist/title match on top
  before falling back to fuzzy search, but it's not MusicMind-grade
  mood/genre intelligence.
- **No Docker image yet** — manual Python setup only, for now.
- **Single shared admin password** — no per-user accounts.
- **Driver player reliability while backgrounded** (phone running
  CarPlay, screen off, etc.) hasn't been tested in a real moving car yet.
- **Installer/packaging story** for a driver who isn't the original
  developer is still minimal — this README is the current documentation.

## License

MIT — see [LICENSE](LICENSE).
