import io
import qrcode
from flask import request, Response

from ridermusic_admin import require_admin_auth, BASE_STYLE, FOOTER_HTML


def register_sign_routes(app):

    @app.route("/admin/sign/qr.png")
    @require_admin_auth
    def sign_qr():
        # request.host_url reflects whatever domain the driver is
        # actually using to reach this instance (their Cloudflare
        # tunnel domain, etc.) -- so the QR is always correct for
        # this specific install with zero manual configuration.
        join_url = request.host_url.rstrip("/") + "/join"
        img = qrcode.make(join_url, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="image/png")

    @app.route("/admin/sign")
    @require_admin_auth
    def sign_page():
        return SIGN_PAGE


SIGN_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RiderMusic Jukebox for Plex — Print a Sign</title>
<link rel="icon" type="image/jpeg" href="/static/logo.jpg">

<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
""" + BASE_STYLE + """
<style>
  a.back { display: inline-block; margin-bottom: 1em; color: var(--accent); text-decoration: none; }

  #headline-input {
    width: 100%;
    padding: 0.7em 0.9em;
    border-radius: 10px;
    border: none;
    background: var(--panel);
    color: var(--text);
    font-size: 1em;
    font-family: 'Inter', sans-serif;
    margin-bottom: 1.5em;
  }

  .layout-toggle {
    display: flex;
    gap: 0.5em;
    margin-bottom: 1.2em;
  }
  .toggle-btn {
    flex: 1;
    padding: 0.7em;
    border-radius: 10px;
    border: 1px solid var(--accent);
    background: transparent;
    color: var(--text);
    font-family: 'Inter', sans-serif;
    font-size: 0.9em;
    font-weight: 600;
    cursor: pointer;
  }
  .toggle-btn.active { background: var(--accent); }

  .print-tip {
    background: var(--panel);
    border-radius: 10px;
    padding: 0.8em 1em;
    font-size: 0.85em;
    color: var(--text-muted);
    margin-bottom: 1.5em;
    line-height: 1.5;
  }

  #print-btn {
    width: 100%;
    padding: 0.9em;
    font-size: 1.05em;
    font-weight: 700;
    font-family: 'Sora', sans-serif;
    background: var(--cta);
    color: #1a1a1a;
    border: none;
    border-radius: 10px;
    cursor: pointer;
    margin-bottom: 2em;
  }

  /* --- The printable card(s) --- */
  .sign-card { display: none; }
  .sign-card.portrait.active { display: block; }
  .sign-card.banner.active { display: flex; }

  .sign-card.portrait {
    background: #ffffff;
    color: #1a1a1a;
    border-radius: 18px;
    padding: 2em 1.5em;
    text-align: center;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
  }
  .sign-logo {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    font-size: 1em;
    color: #325e6a;
    margin-bottom: 0.8em;
  }
  .sign-logo span { color: #ff9a00; }
  .sign-headline-text {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    color: #1a1a1a;
    line-height: 1.2;
  }
  .sign-card.portrait #sign-headline-portrait {
    font-size: 1.6em;
    margin: 0 0 1em 0;
  }
  .sign-card.portrait img {
    width: 100%;
    max-width: 130px;
    height: auto;
    display: block;
    margin: 0 auto 1em auto;
  }
  .sign-sub {
    font-size: 0.95em;
    color: #44a1a4;
    font-weight: 600;
  }
  .sign-website {
    font-size: 0.75em;
    color: #999;
    margin-top: 0.8em;
  }

  /* --- Banner layout: fixed 2in-tall row, QR + text side by side --- */
  .sign-card.banner {
    background: #ffffff;
    color: #1a1a1a;
    border-radius: 12px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    align-items: center;
    gap: 0.35in;
    padding: 0.15in 0.3in;
    width: fit-content;
  }
  .sign-card.banner img {
    height: 2in;
    width: 2in;
    display: block;
    flex-shrink: 0;
  }
  .banner-text {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 0.12in;
  }
  .sign-card.banner .sign-logo {
    font-size: 1.1em;
    margin-bottom: 0;
  }
  .sign-card.banner #sign-headline-banner {
    font-size: 1.5em;
    white-space: nowrap;
  }
  .sign-card.banner .sign-sub {
    font-size: 1em;
  }

  @media print {
    html, body {
      background: #ffffff !important;
      margin: 0;
      padding: 0;
    }
    .wrap > *:not(.sign-card.active) {
      display: none !important;
    }
    .wrap {
      padding: 0;
      max-width: none;
    }
    .sign-card {
      box-shadow: none !important;
      margin: 0 auto;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
  }
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="/admin/dashboard">&larr; Back to dashboard</a>
  <div class="logo">Rider<span class="accent">Music</span> Jukebox for Plex — Print a Sign</div>

  <input type="text" id="headline-input" value="Be the DJ for your ride"
         oninput="document.querySelectorAll('.sign-headline-text').forEach(el => el.textContent = this.value)">

  <div class="layout-toggle">
    <button class="toggle-btn active" data-layout="portrait" onclick="setSignLayout('portrait')">Portrait</button>
    <button class="toggle-btn" data-layout="banner" onclick="setSignLayout('banner')">Banner (2in tall)</button>
  </div>

  <div class="print-tip">
    <strong>Before printing:</strong> in your browser's print dialog, make
    sure "Background graphics" is turned on, or the card will print
    without its colors. For the banner layout, also set scale to
    "Actual size" / 100% (not "Fit to page") so the QR code prints at
    exactly 2 inches.
  </div>

  <button id="print-btn" onclick="window.print()">Print / Save as PDF</button>

  <div class="sign-card portrait active">
    <div class="sign-logo">Rider<span>Music</span> Jukebox</div>
    <div id="sign-headline-portrait" class="sign-headline-text">Be the DJ for your ride</div>
    <img src="/admin/sign/qr.png" alt="QR code to join">
    <div class="sign-sub">Scan to choose the music</div>
    <div class="sign-website">ridermusic.vp-fun.com</div>
  </div>

  <div class="sign-card banner">
    <img src="/admin/sign/qr.png" alt="QR code to join">
    <div class="banner-text">
      <div class="sign-logo">Rider<span>Music</span> Jukebox</div>
      <div id="sign-headline-banner" class="sign-headline-text">Be the DJ for your ride</div>
      <div class="sign-sub">Scan to choose the music</div>
    </div>
  </div>

  """ + FOOTER_HTML + """
</div>
<script>
function setSignLayout(layout) {
  document.querySelectorAll('.toggle-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.layout === layout);
  });
  document.querySelectorAll('.sign-card').forEach(c => {
    c.classList.toggle('active', c.classList.contains(layout));
  });
}
</script>
</body>
</html>
"""
