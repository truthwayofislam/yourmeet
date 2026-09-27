import os
import httpx

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")


def _build_url():
    url = TURSO_URL.rstrip("/")
    url = url.replace("libsql://", "https://").replace("http://", "https://")
    if not url.endswith("/v2/pipeline"):
        url = url + "/v2/pipeline"
    return url


def _to_turso_arg(val):
    if val is None:
        return {"type": "null"}
    if isinstance(val, bool):
        return {"type": "integer", "value": str(int(val))}
    if isinstance(val, int):
        return {"type": "integer", "value": str(val)}
    if isinstance(val, float):
        return {"type": "float", "value": val}
    return {"type": "text", "value": str(val)}


def _from_turso_val(val):
    if val is None:
        return None
    if isinstance(val, dict):
        t = val.get("type", "text")
        v = val.get("value")
        if t == "null":
            return None
        if t == "integer":
            return int(v) if v is not None else 0
        if t == "float":
            return float(v) if v is not None else 0.0
        return v
    return val


class _TursoResult:
    def __init__(self, rows, last_insert_rowid=0):
        self._rows = rows
        self.lastrowid = last_insert_rowid

    def fetchone(self):
        return tuple(self._rows[0]) if self._rows else None

    def fetchall(self):
        return [tuple(r) for r in self._rows]


class _LocalResult:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = getattr(cursor, "lastrowid", 0)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class _ConnWrapper:
    """
    For Turso: uses plain synchronous httpx.post — no libsql, no streams.
    For local: uses libsql SQLite file.
    """

    def __init__(self):
        self._use_turso = bool(TURSO_URL and TURSO_TOKEN)
        self._last_insert_rowid = 0
        if not self._use_turso:
            import libsql_experimental as libsql
            self._local = libsql.connect("yourmeet.db")
        else:
            self._local = None

    def execute(self, sql, params=()):
        if not self._use_turso:
            result = _LocalResult(self._local.execute(sql, params))
            return result

        # Handle last_insert_rowid() without HTTP call
        if sql.strip().upper() == "SELECT LAST_INSERT_ROWID()":
            return _TursoResult([[self._last_insert_rowid]], self._last_insert_rowid)

        args = [_to_turso_arg(p) for p in params]
        payload = {
            "requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]
        }
        try:
            resp = httpx.post(
                _build_url(),
                json=payload,
                headers={"Authorization": f"Bearer {TURSO_TOKEN}"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            result = data["results"][0]
            if result.get("type") == "error":
                err_detail = result.get('error', {})
                err_msg = err_detail.get('message', '') if isinstance(err_detail, dict) else str(err_detail)
                if 'duplicate column' not in err_msg:
                    print(f"[DB] Turso query error: {err_detail}")
                raise ValueError("Database error. Please try again.")
            res = result.get("response", {}).get("result", {})
            cols = [c["name"] for c in res.get("cols", [])]
            raw_rows = res.get("rows", [])
            rows = [[_from_turso_val(v) for v in row] for row in raw_rows]
            rowid = res.get("last_insert_rowid")
            if rowid:
                self._last_insert_rowid = int(rowid)
            return _TursoResult(rows, self._last_insert_rowid)
        except httpx.HTTPError as e:
            try:
                err_body = resp.text[:500]
            except Exception:
                err_body = 'unknown'
            print(f"[DB] Turso error: {e} | body: {err_body}")
            raise ValueError("Database error. Please try again.")

    def commit(self):
        # Turso HTTP API is auto-commit — no-op
        if not self._use_turso and self._local:
            self._local.commit()

    def close(self):
        if not self._use_turso and self._local:
            try:
                self._local.close()
            except Exception:
                pass


def get_conn():
    return _ConnWrapper()


def get_db():
    """FastAPI dependency — fresh connection per request."""
    conn = _ConnWrapper()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    conn = get_conn()

    statements = [
        """CREATE TABLE IF NOT EXISTS users (
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
            mystery_until TEXT DEFAULT '',
            terms_accepted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL,
            to_user INTEGER NOT NULL,
            is_super INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(from_user, to_user)
        )""",
        """CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            matched_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS skips (
            user_id INTEGER NOT NULL,
            skipped_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, skipped_id)
        )""",
        """CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            reported_id INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            user1_tg_id TEXT NOT NULL,
            user2_tg_id TEXT NOT NULL,
            is_premium_chat INTEGER DEFAULT 0,
            expires_at TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS vibe_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            date TEXT NOT NULL UNIQUE
        )""",
        """CREATE TABLE IF NOT EXISTS vibe_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(match_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            sender_tg_id TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_tg ON users(telegram_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_gender ON users(gender)",
        "CREATE INDEX IF NOT EXISTS idx_users_approved ON users(is_approved)",
        "CREATE INDEX IF NOT EXISTS idx_likes_from ON likes(from_user)",
        "CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_user)",
        "CREATE INDEX IF NOT EXISTS idx_chat_active ON chat_sessions(is_active)",
    ]

    for stmt in statements:
        try:
            conn.execute(stmt)
        except Exception as e:
            print(f"[DB] init statement skipped: {e}")

    for alter in [
        "ALTER TABLE users ADD COLUMN mystery_until TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN terms_accepted INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN setup_msg_id INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN setup_data TEXT DEFAULT '{}'",
    ]:
        try:
            conn.execute(alter)
        except Exception:
            pass  # column already exists

    conn.commit()


# ── Row helpers ──────────────────────────────────────────────────────────────

USER_COLS = [
    "id", "name", "phone", "age", "gender", "interested_in",
    "bio", "city", "lat", "lng", "photo", "photos", "interests",
    "social_handle", "telegram_id", "language", "is_premium",
    "premium_until", "is_approved", "is_verified", "is_rejected",
    "is_blocked", "is_admin", "daily_swipes", "swipes_reset_date",
    "super_likes_left", "boosted_until", "referral_count", "created_at",
    "mystery_until", "terms_accepted",
]

USER_SELECT = ", ".join(USER_COLS)


def row_to_user(row):
    if not row:
        return None
    return User(dict(zip(USER_COLS, row)))


class User:
    def __init__(self, d: dict):
        self._data = d
        for k, v in d.items():
            if k not in ("is_premium", "is_approved", "is_verified", "is_blocked", "is_rejected", "is_admin"):
                self.__dict__[k] = v

    @property
    def is_premium(self):
        return bool(self._data.get("is_premium", 0))

    @property
    def is_approved(self):
        return bool(self._data.get("is_approved", 0))

    @property
    def is_verified(self):
        return bool(self._data.get("is_verified", 0))

    @property
    def is_blocked(self):
        return bool(self._data.get("is_blocked", 0))

    @property
    def is_rejected(self):
        return bool(self._data.get("is_rejected", 0))

    @property
    def is_admin(self):
        return bool(self._data.get("is_admin", 0))
