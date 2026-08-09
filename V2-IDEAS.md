# RiderMusic v2 Ideas

Not scoped, not scheduled — a running note of things worth considering
for a future version, captured so they don't get lost between sessions.

## Additional music sources beyond Plex

**Spotify — the most promising of the three explored.** Spotify's
Player API can control playback (play/pause/skip/queue) on a device
already running Spotify, and does so through Spotify's own cloud, not
local network discovery — meaning it likely avoids the exact dead end
that killed the original Plexamp-based v1 architecture. The real
constraint: as of Feb 2026, Spotify tightened third-party API access
significantly; commercial/production access now requires approval,
and free "Development Mode" caps out at five authorized users.

RiderMusic's existing BYOK architecture (each driver brings their own
Plex token) may sidestep this: if each driver registers their own
small Spotify developer app and authorizes only their own account,
that's one user per app — comfortably under the five-user cap that's
currently killing broader third-party Spotify integrations.

Important limitation regardless: Spotify's terms don't allow
proxying/streaming raw audio through a third-party player the way
RiderMusic does with Plex's direct file API. A Spotify mode would
need the driver's actual Spotify app open and playing on their phone,
with RiderMusic sending remote commands — not the same "stream
through our own `<audio>` element" architecture used for Plex.

**Not tested — only researched.** Before building anything here, the
Spotify Connect API needs the same real-hardware verification
discipline used for the Plexamp research: confirm it actually works
as documented against a real account before designing around it.

**YouTube — probably a pass.** No officially sanctioned way to get
playable audio for third-party use. The common unofficial approach
(yt-dlp and similar) works by reverse-engineering signatures Google
actively fights, and using it violates YouTube's terms — a real risk
for a product with your name on it, not just a personal script.

**Local iTunes/Music.app libraries — plausible, but probably doesn't
need iTunes at all.** DRM-free files (anything ripped from CD or
purchased outright rather than streamed via an active subscription)
are just regular audio files on disk — architecturally similar to
Plex's FLAC files. DRM-protected tracks from an active Apple Music
subscription are not extractable, same category of dead end as
Spotify's streams.

Synology's own iTunes Server package is officially end-of-life as of
DSM 7.2 (2023) — not installable on current DSM. A Docker alternative
(`forked-daapd`) exists and is actively maintained, but DAAP (the
protocol both use) exists specifically so iTunes/Music.app can browse
a remote library as a *client* — RiderMusic isn't an iTunes client,
it's building its own guest UI. If the goal is just "play these
DRM-free files," RiderMusic could likely read them directly (e.g. via
`mutagen`, already used elsewhere in the MusicMind/RiderMusic
ecosystem) without any DAAP layer at all. DAAP would only matter if
the goal is specifically to interoperate with an existing iTunes/Music
setup as its own separate use case, not just to play the files.

## Open question to resolve before any of this gets scoped

Which of these is actually wanted:
1. A genuinely multi-source RiderMusic (Plex + Spotify + local files,
   guest picks whichever), or
2. A per-install choice (a given driver's instance is Plex-only, or
   Spotify-only, or files-only, configured once), or
3. Just Plex, permanently, with these noted for completeness

The answer changes how much of this is worth building at all.
