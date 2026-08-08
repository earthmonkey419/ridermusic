# RiderMusic

QR-based guest DJ system for rideshare passengers — from the makers of
[MusicMind for Plex](https://musicmind.vp-fun.com/).

A rideshare driver running Plex places a QR code in the car. Passengers
scan it, search the driver's music library, and queue songs — no app
install, no Plex account, no login. The driver's own phone plays the
music through the car stereo; the passenger's phone is just a remote.

**Status: early, working build.** The core loop (guest search/queue,
driver playback, admin session control) runs end to end and has been
tested on real hardware, including a working Docker image. Not yet
tested in a real moving car. Standalone product: does not require
MusicMind to run.

## How it works

```
Guest phone (portal)  ->  RiderMusic backend  ->  Driver's phone (dashboard)  ->  car stereo
                              |
                              v
                        Plex Media Server
```

- **Guest portal:** search, browse mood buckets (Chill, Dance, R&B,
  Disco, 80s, Country — matched against the real genre tags in your
  library, not fixed keywords), add to queue, play/pause/skip, volume
  (clamped to a driver-set ceiling)
- **Driver dashboard:** one page — now-playing audio player, session
  status, End Session button, and recent activity log all together.
  Streams audio directly from Plex, no Plexamp involved
- **In-app guide:** a `?` link on the dashboard explains QR setup,
  how sessions work, and where config lives
- **Sessions:** passive by default — first scan starts a session,
  it auto-expires, driver only needs to act to end one early

## Requirements

- A Plex Media Server with a music library
- A way to expose the backend to the internet (Cloudflare Tunnel, a
  reverse proxy, etc.) — both the driver's phone and guest phones need
  to reach it from outside your home network while on the road
- Docker (recommended), or Python 3.12+ for a manual install

## Setup

### Docker (recommended)

```bash
git clone https://github.com/earthmonkey419/ridermusic.git
cd ridermusic

cp config.example.py config.py
# edit config.py: PLEX_URL, PLEX_TOKEN, MUSIC_LIB, ADMIN_PASSWORD
# set COOKIE_SECURE = True (requires HTTPS — see below)

docker build -t ridermusic .
docker run -d --name ridermusic \
  -p 6869:6869 \
  -v $(pwd)/config.py:/app/config.py:ro \
  ridermusic
```

`config.py` holds your Plex token and admin password — it's bind-mounted
at container start, never baked into the image, so it never ends up in
a Docker layer or gets pushed anywhere by accident.

### Manual (Python)

```bash
git clone https://github.com/earthmonkey419/ridermusic.git
cd ridermusic
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# edit config.py as above

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
3. As the driver: log in at `/admin/login`, then stay on
   `/admin/dashboard` — that's both your session control panel and
   the audio player, pair it to your car stereo (Bluetooth or aux)
4. Tap the `?` on the dashboard anytime for a quick in-app guide
5. Use **End Session** on the dashboard to cut a ride short if needed

## Known limitations (current)

- **Search quality** depends on what's actually tagged in your Plex
  library. RiderMusic layers literal artist/title/genre matches on top
  of Plex's fuzzy hub search, and mood buckets match real genre tags —
  but a library with thin or missing genre data will get thin results,
  same as any search built on top of it.
- **Single shared admin password** — no per-user accounts.
- **Driver dashboard reliability while backgrounded** (phone running
  CarPlay, screen off, etc.) hasn't been tested in a real moving car yet.
- **Installer/packaging story** for a driver who isn't the original
  developer is still minimal — this README is the current documentation.

## License

MIT — see [LICENSE](LICENSE).
