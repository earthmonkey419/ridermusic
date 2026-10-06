#!/usr/bin/env python3
"""Create a dedicated Plex token for RiderMusic.

Why: a token copied from Plex Web belongs to that browser session, and
dies the moment you sign that browser out. This registers RiderMusic as
its own device ("RiderMusic" under plex.tv -> Authorized Devices), so
only removing *that* entry can revoke it.

Usage (from the RiderMusic folder, venv active):
    python3 make_plex_token.py            # prints the token
    python3 make_plex_token.py --write    # also updates PLEX_TOKEN in config.py
                                          # (config.py.bak keeps the old one)
Then restart RiderMusic.
"""
import argparse
import os
import re
import shutil
import sys
import uuid

APP_NAME = "RiderMusic"
CONFIG_PATH = "config.py"
_TOKEN_LINE = re.compile(r'^(PLEX_TOKEN\s*=\s*)(["\']).*?\2', re.M)


def write_token(config_path, token):
    """Replace PLEX_TOKEN in config.py, keeping a .bak. Returns True/False."""
    with open(config_path, encoding="utf-8") as f:
        src = f.read()
    if not _TOKEN_LINE.search(src):
        return False
    shutil.copyfile(config_path, config_path + ".bak")
    new_src = _TOKEN_LINE.sub(lambda m: f'{m.group(1)}"{token}"', src, count=1)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(new_src)
    return True


def verify(token):
    """Try the token against the PLEX_URL in config.py. (ok, message)."""
    try:
        sys.path.insert(0, os.getcwd())
        import config
        import requests
        r = requests.get(config.PLEX_URL.rstrip("/") + "/library/sections",
                         headers={"X-Plex-Token": token}, timeout=8)
        if r.status_code == 200:
            return True, f"Plex at {config.PLEX_URL} accepted the new token."
        return False, f"Plex at {config.PLEX_URL} answered HTTP {r.status_code}."
    except Exception as e:
        return None, f"Couldn't verify against Plex ({type(e).__name__}); that's okay."


def main():
    ap = argparse.ArgumentParser(description="Create a dedicated Plex token for RiderMusic.")
    ap.add_argument("--write", action="store_true",
                    help="update PLEX_TOKEN in config.py (backs up to config.py.bak)")
    ap.add_argument("--timeout", type=int, default=300,
                    help="seconds to wait for you to approve in the browser")
    args = ap.parse_args()

    try:
        import plexapi
        from plexapi.myplex import MyPlexPinLogin
    except ImportError:
        print("plexapi isn't installed here. Activate the venv: source .venv/bin/activate")
        return 1

    headers = dict(plexapi.BASE_HEADERS)
    headers.update({
        "X-Plex-Product": APP_NAME,
        "X-Plex-Device-Name": APP_NAME,
        "X-Plex-Client-Identifier": f"ridermusic-{uuid.uuid4()}",
    })
    login = MyPlexPinLogin(headers=headers, oauth=True)
    print("\nOpen this link in any browser, sign in to Plex, and approve:\n")
    print("  " + login.oauthUrl() + "\n")
    print(f"Waiting up to {args.timeout}s...")
    login.run(timeout=args.timeout)
    login.waitForLogin()
    token = login.token
    if not token:
        print("\nNo approval received (expired or cancelled). Run it again.")
        return 1

    ok, msg = verify(token)
    print("\n" + msg)
    if ok is False:
        print("Not saving a token Plex just rejected.")
        return 1

    if args.write:
        if write_token(CONFIG_PATH, token):
            print(f"Saved to {CONFIG_PATH} (previous file kept as {CONFIG_PATH}.bak).")
        else:
            print(f"Couldn't find a PLEX_TOKEN line in {CONFIG_PATH}. Set it yourself:")
            print(f'  PLEX_TOKEN = "{token}"')
    else:
        print("\nPut this in config.py:\n")
        print(f'  PLEX_TOKEN = "{token}"')
    print("\nThen restart RiderMusic. In Plex it appears as a device named "
          f'"{APP_NAME}" -- don\'t remove that entry.')
    return 0


if __name__ == "__main__":
    sys.exit(main())
