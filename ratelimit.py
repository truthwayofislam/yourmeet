"""Simple in-memory rate limiter.

Uses a sliding-window counter per key. Suitable for low-traffic apps; for
production-scale you'd want a Redis-backed store. Keys are scoped per
tg_id (or per IP) and per action.
"""

import time
import threading

# key -> list of timestamps (seconds)
_store: dict[str, list[float]] = {}
_lock = threading.Lock()

# Default limits: (max_actions, window_seconds)
DEFAULTS = {
    "like": (30, 60),
    "report": (5, 60),
    "match": (30, 60),
    "profile_view": (60, 60),
}


def allow(key: str, limit: int = 30, window: int = 60) -> bool:
    """Return True if the action is allowed, False if rate-limited."""
    now = time.time()
    with _lock:
        # Periodically prune stale keys — without this the store grows
        # unbounded (one entry per user/IP) and leaks memory on long uptimes.
        if len(_store) > 4096:
            stale = [k for k, v in _store.items() if not v or now - v[-1] > 3600]
            for k in stale:
                del _store[k]
        stamps = _store.get(key, [])
        # Drop timestamps outside the window
        stamps = [t for t in stamps if now - t < window]
        if len(stamps) >= limit:
            _store[key] = stamps
            return False
        stamps.append(now)
        _store[key] = stamps
        return True


def remaining(key: str, limit: int = 30, window: int = 60) -> int:
    now = time.time()
    with _lock:
        stamps = [t for t in _store.get(key, []) if now - t < window]
        return max(0, limit - len(stamps))


def reset(key: str):
    with _lock:
        _store.pop(key, None)