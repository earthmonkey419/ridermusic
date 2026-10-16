/*
 * MLGapless v1 -- two-element gapless handoff for the MusicLounge apps.
 *
 * CANONICAL COPY: MusicLoungePlayer/static/js/gapless.js. The Jukebox
 * and RiderMusic carry byte-identical copies. Edit it in Player first,
 * copy it out, then confirm with: sha256sum <each copy>
 *
 * How it works: a second, idle <audio> element preloads whatever the
 * host says plays NEXT. A hair before the active track ends it fires
 * the host's own "ended" handler early; the host calls start(ref), and
 * start() hands off to the already-buffered element instead of loading
 * a new src. If anything isn't ready, start() falls back to plain
 * set-src-and-play, so the worst case is the pre-gapless behavior.
 * Foreground only -- this does NOT fix iOS background advancement.
 *
 * Host contract:
 *   const g = MLGapless.create({
 *     audio:   <audio> element the page already has (initial active),
 *     urlFor:  ref => "/stream/" + ref,            // optional
 *     nextRef: () => ref | null,                   // what plays next, WITHOUT mutating state
 *     onSwap:  el => { audioEl = el; },            // host re-points its own variable
 *     onBlocked: err => {...},                     // optional: engine's play() was refused (autoplay policy)
 *   });
 *   g.on("ended", handler)   // use instead of audioEl.addEventListener
 *   g.start(ref)             // use instead of audioEl.src = ...; audioEl.play()
 *   g.refresh()              // optional: queue changed, re-aim the preload now
 * Debug: localStorage.setItem("mlGaplessDebug", "1") and reload.
 */
(function (root) {
  "use strict";
  var VERSION = 3;
  var SILENT_WAV = "data:audio/wav;base64,UklGRiUAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQEAAACA";

  function create(opts) {
    var urlFor = opts.urlFor || function (ref) { return "/stream/" + ref; };
    var nextRef = opts.nextRef || function () { return null; };
    var onSwap = opts.onSwap || function () {};
    var onBlocked = opts.onBlocked || function () {};
    var leadMs = opts.leadMs != null ? opts.leadMs : 60;            // hand off this long before the end; tune by ear
    var preloadAfterS = opts.preloadAfterS != null ? opts.preloadAfterS : 2;
    var debug = !!opts.debug;
    try { if (localStorage.getItem("mlGaplessDebug") === "1") debug = true; } catch (e) {}

    var active = opts.audio;
    var idle = new Audio();
    idle.preload = "auto";
    var preloadedRef = null;
    var idleUnlocked = false, idleUnlocking = false;
    var earlyFired = false;
    var gen = 0;   // bumped by every start(); lets stale async callbacks recognise they were superseded

    function same(a, b) { return a != null && b != null && String(a) === String(b); }
    function log() { if (debug) console.log.apply(console, ["[gapless]"].concat([].slice.call(arguments))); }

    function on(type, handler) {
      // Same listener on both elements; only the ACTIVE one's events count.
      [active, idle].forEach(function (el) {
        el.addEventListener(type, function (e) { if (el === active) handler(e); });
      });
    }

    function refresh() {
      try {
        if (idleUnlocking || active.currentTime < preloadAfterS) return;
        var ref = nextRef();
        if (ref == null || same(preloadedRef, ref)) return;
        idle.src = urlFor(ref);
        idle.load();
        preloadedRef = ref;
        log("preloading", ref);
      } catch (e) { console.error("MLGapless: preload failed (non-fatal):", e); }
    }

    function unlockIdle() {
      // iOS: play 1 sample of silence on the idle element inside a real
      // user gesture, so it may be started later from a timer.
      if (idleUnlocked || idleUnlocking || preloadedRef !== null) return;
      idleUnlocking = true;
      var el = idle;
      function finish(ok) {
        try { el.pause(); el.removeAttribute("src"); el.load(); } catch (e) {}
        idleUnlocking = false;
        if (ok) idleUnlocked = true;
      }
      try {
        el.src = SILENT_WAV;
        var p = el.play();
        if (p && p.then) p.then(function () { finish(true); }, function () { finish(false); });
        else finish(true);
      } catch (e) { finish(false); }
    }
    ["touchend", "click", "keydown"].forEach(function (ev) { document.addEventListener(ev, unlockIdle, true); });

    function quiet() {
      // Invariant: only the ACTIVE element may be sounding. Anything else that is
      // playing is a stray (a race, a late promise) -- silence it.
      if (!idleUnlocking && !idle.paused) { log("stray audio on the idle element; pausing it"); idle.pause(); }
    }

    function start(ref) {
      var url = urlFor(ref);
      var myGen = ++gen;
      earlyFired = false;
      log("start", ref, "gen", myGen, same(preloadedRef, ref) && idle.readyState >= 3 ? "(swap)" : "(load)");
      if (same(preloadedRef, ref) && idle.readyState >= 3) {
        var incoming = idle, outgoing = active;
        log("handoff; outgoing remaining ms:", Math.round(((outgoing.duration || 0) - outgoing.currentTime) * 1000));
        incoming.volume = outgoing.volume;
        var p = incoming.play();          // start the new one FIRST...
        active = incoming; idle = outgoing; preloadedRef = null;
        outgoing.pause();                 // ...then stop the old one
        onSwap(active);
        if (p && p.catch) p.catch(function () {
          if (myGen !== gen) return;   // superseded by a newer start(): ITS src/role changes caused this rejection
          // Couldn't start (e.g. never unlocked): undo the swap, load normally.
          log("incoming play() rejected; falling back");
          active = outgoing; idle = incoming;
          incoming.pause();
          onSwap(active);
          active.src = url;
          active.play().catch(function (e) { if (myGen === gen) onBlocked(e); });
        });
        quiet();
        return;
      }
      quiet();
      active.src = url;
      active.play().catch(function (e) { if (myGen === gen) onBlocked(e); });
    }

    var tick = 0;
    setInterval(function () {
      try {
        if (++tick % 25 === 0) { refresh(); quiet(); }    // ~every 500ms
        if (earlyFired || preloadedRef === null || active.paused || active.seeking) return;
        var d = active.duration;
        if (!isFinite(d) || d < 3) return;
        if ((d - active.currentTime) * 1000 > leadMs) return;
        if (!same(nextRef(), preloadedRef) || idle.readyState < 3) return;
        earlyFired = true;                                // once per track
        // Reuse the host's normal end-of-track path by firing it a few ms early.
        active.dispatchEvent(new Event("ended"));
      } catch (e) { /* never let this break playback */ }
    }, 20);

    return { on: on, start: start, refresh: refresh, active: function () { return active; }, version: VERSION };
  }

  root.MLGapless = { VERSION: VERSION, create: create };
})(window);
