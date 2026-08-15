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

  /* --- The printable card itself --- */
  .sign-card {
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
  #sign-headline {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    font-size: 1.6em;
    color: #1a1a1a;
    margin: 0 0 1em 0;
    line-height: 1.2;
  }
  .sign-card img {
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

  @media print {
    html, body {
      background: #ffffff !important;
      margin: 0;
      padding: 0;
    }
    .wrap > *:not(.sign-card) {
      display: none !important;
    }
    .wrap {
      padding: 0;
      max-width: none;
    }
    .sign-card {
      box-shadow: none;
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
         oninput="document.getElementById('sign-headline').textContent = this.value">

  <div class="print-tip">
    <strong>Before printing:</strong> in your browser's print dialog, make
    sure "Background graphics" is turned on, or the card will print
    without its colors.
  </div>

  <button id="print-btn" onclick="window.print()">Print / Save as PDF</button>

  <div class="sign-card">
    <div class="sign-logo">Rider<span>Music</span> Jukebox</div>
    <div id="sign-headline">Be the DJ for your ride</div>
    <img src="/admin/sign/qr.png" alt="QR code to join">
    <div class="sign-sub">Scan to choose the music</div>
    <div class="sign-website">ridermusic.vp-fun.com</div>
  </div>

  """ + FOOTER_HTML + """
</div>
</body>
</html>
"""
