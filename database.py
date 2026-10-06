import os
import asyncio
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


class DuplicateError(Exception):
    """Raised when an INSERT violates a UNIQUE constraint (a benign race outcome)."""
    pass


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
            try:
                result = _LocalResult(self._local.execute(sql, params))
                return result
            except Exception as e:
                # libsql wraps sqlite3.IntegrityError; detect UNIQUE violations
                msg = str(e).lower()
                if "unique" in msg or "constraint" in msg:
                    raise DuplicateError(str(e)) from e
                raise

        # Handle last_insert_rowid() without HTTP call
        if sql.strip().upper() == "SELECT LAST_INSERT_ROWID()":
            return _TursoResult([[self._last_insert_rowid]], self._last_insert_rowid)

        args = [_to_turso_arg(p) for p in params]
        payload = {
            "requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]
        }
        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        httpx.post,
                        _build_url(),
                        json=payload,
                        headers={"Authorization": f"Bearer {TURSO_TOKEN}"},
                        timeout=15,
                    )
                    resp = future.result(timeout=20)
            else:
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
                if 'duplicate column' in err_msg:
                    raise DuplicateError(err_msg)
                if 'unique' in err_msg.lower() or 'constraint' in err_msg.lower():
                    raise DuplicateError(err_msg)
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

    def rollback(self):
        if not self._use_turso and self._local:
            try:
                self._local.rollback()
            except Exception:
                pass

    def begin(self):
        """Begin transaction — only for local SQLite, no-op for Turso (auto-commit)."""
        if not self._use_turso:
            try:
                self._local.execute("BEGIN IMMEDIATE")
            except Exception:
                pass

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
            daily_swipes INTEGER DEFAULT 30,
            swipes_reset_date TEXT DEFAULT '',
            super_likes_left INTEGER DEFAULT 1,
            boosted_until TEXT DEFAULT '',
            referral_count INTEGER DEFAULT 0,
            mystery_until TEXT DEFAULT '',
            terms_accepted INTEGER DEFAULT 0,
            looking_for TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER NOT NULL,
            to_user INTEGER NOT NULL,
            is_super INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
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
            missed_notified INTEGER DEFAULT 0,
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
            date TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(match_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS vibe_scores (
            match_id INTEGER PRIMARY KEY,
            asked INTEGER DEFAULT 0,
            matched INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            sender_tg_id TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS user_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blocker_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(blocker_id, blocked_id)
        )""",
        """CREATE TABLE IF NOT EXISTS user_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            viewer_id INTEGER NOT NULL,
            viewed_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_id INTEGER,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_user_blocks_blocker ON user_blocks(blocker_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_blocks_blocked ON user_blocks(blocked_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_views_viewed ON user_views(viewed_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_log_tg ON audit_log(tg_id)",
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_tg ON users(telegram_id)",
        "CREATE INDEX IF NOT EXISTS idx_users_gender ON users(gender)",
        "CREATE INDEX IF NOT EXISTS idx_users_approved ON users(is_approved)",
        "CREATE INDEX IF NOT EXISTS idx_likes_from ON likes(from_user)",
        "CREATE INDEX IF NOT EXISTS idx_likes_to ON likes(to_user)",
        "CREATE INDEX IF NOT EXISTS idx_chat_active ON chat_sessions(is_active)",
        # Enforce uniqueness on existing tables. These use IF NOT EXISTS so they
        # are idempotent on every startup, and they actually create the constraint
        # even when the table already exists (inline UNIQUE in the DDL above is
        # ignored by CREATE TABLE IF NOT EXISTS).
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_likes_unique ON likes(from_user, to_user)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_unique ON matches(user1_id, user2_id)",
    ]

    for stmt in statements:
        try:
            conn.execute(stmt)
        except Exception as e:
            print(f"[DB] init statement skipped: {e}")

    # Ensure UNIQUE constraints exist on tables that predate this code.
    # Inline UNIQUE in the DDL above is ignored by CREATE TABLE IF NOT EXISTS
    # when the table already exists, so these idempotent indexes are what
    # actually enforce uniqueness on a live database.
    for index_stmt, dedup_sql in [
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_likes_unique ON likes(from_user, to_user)",
            "DELETE FROM likes WHERE id NOT IN ("
            "  SELECT MIN(id) FROM likes GROUP BY from_user, to_user"
            ")",
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_unique ON matches(user1_id, user2_id)",
            "DELETE FROM matches WHERE id NOT IN ("
            "  SELECT MIN(id) FROM matches GROUP BY user1_id, user2_id"
            ")",
        ),
    ]:
        try:
            conn.execute(dedup_sql)
            conn.commit()
        except Exception as e:
            print(f"[DB] dedup skipped: {e}")
        try:
            conn.execute(index_stmt)
            conn.commit()
        except Exception as e:
            print(f"[DB] index skipped: {e}")

    for alter in [
        "ALTER TABLE users ADD COLUMN mystery_until TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN terms_accepted INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN setup_msg_id INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN setup_data TEXT DEFAULT '{}'",
        "ALTER TABLE users ADD COLUMN min_age INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN max_age INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN max_distance INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN profile_views INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN looking_for TEXT DEFAULT ''",
        "ALTER TABLE chat_sessions ADD COLUMN missed_notified INTEGER DEFAULT 0",
        "ALTER TABLE vibe_answers ADD COLUMN date TEXT DEFAULT ''",
        # Premium-offer memory: how many days the admin gifted this user —
        # their referral reward equals this when their link brings 3 users.
        "ALTER TABLE users ADD COLUMN referral_reward_days INTEGER DEFAULT 0",
        # Telegram file_ids are bot-scoped: browse needs the MAIN bot's id
        # (users.photo), admin review needs the ADMIN bot's re-uploaded id.
        "ALTER TABLE users ADD COLUMN photo_admin TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(alter)
        except Exception:
            pass  # column already exists

    # Backfill: old vibe answer rows had no date — stamp them from created_at
    # so same-day comparisons work after the date column is introduced.
    try:
        conn.execute("UPDATE vibe_answers SET date=substr(created_at,1,10) WHERE date='' OR date IS NULL")
        conn.commit()
    except Exception as e:
        print(f"[DB] vibe_answers date backfill skipped: {e}")

    conn.commit()


# ── Row helpers ──────────────────────────────────────────────────────────────

USER_COLS = [
    "id", "name", "age", "gender", "interested_in",
    "bio", "city", "lat", "lng", "photo", "photos", "interests",
    "social_handle", "telegram_id", "language", "is_premium",
    "premium_until", "is_approved", "is_verified", "is_rejected",
    "is_blocked", "is_admin", "daily_swipes", "swipes_reset_date",
    "super_likes_left", "boosted_until", "referral_count", "created_at",
    "mystery_until", "terms_accepted", "setup_msg_id", "setup_data",
    "min_age", "max_age", "max_distance", "profile_views", "looking_for",
    "photo_admin",
    ]

USER_SELECT = ", ".join(USER_COLS)


def log_audit(tg_id: str, action: str, target_id: int = None, detail: str = ""):
    """Insert an audit log entry. Best-effort — never raises."""
    try:
        db = get_conn()
        db.execute(
            "INSERT INTO audit_log (tg_id, action, target_id, detail) VALUES (?,?,?,?)",
            (str(tg_id), action, target_id, detail),
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[AUDIT] log failed: {e}")


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
