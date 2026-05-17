import libsql_experimental as libsql
import os

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")

_conn = None


def get_conn():
    global _conn
    if _conn is None:
        if TURSO_URL and TURSO_TOKEN:
            _conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
        else:
            _conn = libsql.connect("yourmeet.db")
    return _ConnWrapper(_conn)


class _ConnWrapper:
    """Wraps connection — auto-reconnect on failure, close() is no-op."""

    def __init__(self, conn):
        self._c = conn

    def _sync(self):
        try:
            self._c.sync()
        except Exception:
            self._reconnect()

    def _reconnect(self):
        global _conn
        if TURSO_URL and TURSO_TOKEN:
            _conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
        else:
            _conn = libsql.connect("yourmeet.db")
        self._c = _conn

    def execute(self, sql, params=()):
        self._sync()
        try:
            return self._c.execute(sql, params)
        except Exception:
            self._reconnect()
            return self._c.execute(sql, params)

    def executescript(self, sql):
        self._sync()
        return self._c.executescript(sql)

    def commit(self):
        return self._c.commit()

    def close(self):
        pass  # keep connection alive


def get_db():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE,
            age INTEGER,
            gender TEXT,
            interested_in TEXT DEFAULT 'both',
            bio TEXT DEFAULT '',
            city TEXT DEFAULT '',
            lat REAL DEFAULT 0,
            lng REAL DEFAULT 0,
            photo TEXT DEFAULT '',
            photos TEXT DEFAULT '[]',
            interests TEXT DEFAULT '[]',
            social_handle TEXT DEFAULT '',
            telegram_id TEXT UNIQUE,
            language TEXT DEFAULT 'en',
            is_premium INTEGER DEFAULT 0,
            premium_until TEXT DEFAULT '',
            is_approved INTEGER DEFAULT 0,
            is_verified INTEGER DEFAULT 0,
            is_rejected INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            daily_swipes INTEGER DEFAULT 10,
            swipes_reset_date TEXT DEFAULT '',
            super_likes_left INTEGER DEFAULT 1,
            boosted_until TEXT DEFAULT '',
            referral_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL,
            to_user INTEGER NOT NULL,
            is_super INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            matched_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS skips (
            user_id INTEGER NOT NULL,
            skipped_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, skipped_id)
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            reported_id INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            user1_tg_id TEXT NOT NULL,
            user2_tg_id TEXT NOT NULL,
            is_premium_chat INTEGER DEFAULT 0,
            expires_at TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_users_tg ON users(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_users_gender ON users(gender);
        CREATE INDEX IF NOT EXISTS idx_users_approved ON users(is_approved);
        CREATE INDEX IF NOT EXISTS idx_likes_from ON likes(from_user);
        CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_user);
        CREATE INDEX IF NOT EXISTS idx_chat_active ON chat_sessions(is_active);
    """)
    conn.commit()


# ── Row helpers ──────────────────────────────────────────────────────────────

USER_COLS = [
    "id", "name", "phone", "age", "gender", "interested_in",
    "bio", "city", "lat", "lng", "photo", "photos", "interests",
    "social_handle", "telegram_id", "language", "is_premium",
    "premium_until", "is_approved", "is_verified", "is_rejected",
    "is_blocked", "is_admin", "daily_swipes", "swipes_reset_date",
    "super_likes_left", "boosted_until", "referral_count", "created_at",
]

USER_SELECT = ", ".join(f"u.{c}" if False else c for c in USER_COLS)


def row_to_user(row):
    if not row:
        return None
    return User(dict(zip(USER_COLS, row)))


class User:
    def __init__(self, d: dict):
        self.__dict__.update(d)

    @property
    def is_premium(self):
        return bool(self.__dict__.get("is_premium", 0))

    @property
    def is_approved(self):
        return bool(self.__dict__.get("is_approved", 0))

    @property
    def is_verified(self):
        return bool(self.__dict__.get("is_verified", 0))

    @property
    def is_blocked(self):
        return bool(self.__dict__.get("is_blocked", 0))

    @property
    def is_rejected(self):
        return bool(self.__dict__.get("is_rejected", 0))

    @property
    def is_admin(self):
        return bool(self.__dict__.get("is_admin", 0))
