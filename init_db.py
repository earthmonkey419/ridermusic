import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.executescript("""
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    started_at     REAL,
    expires_at     REAL,
    ended_by_admin INTEGER DEFAULT 0,
    device_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    action_type TEXT,
    detail      TEXT,
    ts          REAL
);

CREATE TABLE IF NOT EXISTS config (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
""")
conn.commit()
conn.close()
print("ridermusic.db initialized")
