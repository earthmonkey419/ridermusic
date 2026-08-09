import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.executescript("""
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    started_at     REAL,
    expires_at     REAL,
    ended_by_admin INTEGER DEFAULT 0,
    device_count   INTEGER DEFAULT 0,
    rejoin_code    TEXT
);

CREATE TABLE IF NOT EXISTS session_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    action_type TEXT,
    detail      TEXT,
    ts          REAL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token       TEXT PRIMARY KEY,
    created_at  REAL,
    expires_at  REAL
);

CREATE TABLE IF NOT EXISTS queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    rating_key  INTEGER,
    title       TEXT,
    artist      TEXT,
    duration_ms INTEGER,
    added_at    REAL,
    played      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS playback_state (
    session_id        TEXT PRIMARY KEY,
    current_queue_id  INTEGER,
    is_playing        INTEGER DEFAULT 0,
    position_ms       INTEGER DEFAULT 0,
    volume            INTEGER DEFAULT 50,
    last_skip_at      REAL,
    updated_at        REAL
);

CREATE TABLE IF NOT EXISTS config (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

INSERT OR IGNORE INTO config (key, value) VALUES ('volume_ceiling', '80');
INSERT OR IGNORE INTO config (key, value) VALUES ('skip_rate_limit_seconds', '20');
""")
conn.commit()
conn.close()
print("ridermusic.db initialized")

conn2 = sqlite3.connect(DB_PATH)
try:
    conn2.execute("ALTER TABLE sessions ADD COLUMN rejoin_code TEXT")
    conn2.commit()
    print("added rejoin_code column")
except sqlite3.OperationalError as e:
    print(f"rejoin_code column already exists or other issue: {e}")
conn2.close()
