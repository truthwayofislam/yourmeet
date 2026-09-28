# YourMeet — Complete Plan (Updated)

## Overview
Full redesign of YourMeet dating app with multilingual support and streamlined bot flow.

---

## Tech Stack
- **Backend:** FastAPI (Python)
- **Bot:** Python Telegram Bot (PTB)
- **Database:** Turso (libsql)
- **Hosting:** Render
- **Payments:** Telegram Stars (XTR)
- **Vibe Check Questions:** Groq API — `llama-3.3-70b-versatile`
- **Chat:** Telegram Bot forwarding (no extra service needed)

---

## Supported Languages
| Code | Language |
|------|----------|
| `en` | English |
| `es` | Spanish |
| `ru` | Russian |
| `ko` | Korean |
| `zh` | Chinese (Simplified) |
| `id` | Indonesian |
| `ar` | Arabic |
| `pt` | Portuguese |
| `fr` | French |
| `de` | German |
| `tr` | Turkish |
| `it` | Italian |
| `ja` | Japanese |
| `hi` | Hindi |

---

## Bot Messages
- Manually written in `strings.py` for all 14 languages
- All strings including `btn_upgrade` present in all 14 languages
- Language stored in DB per user

---

## User Flow

### Phase 1 — Bot Entry
```
User opens bot → /start
    ↓
Welcome message (auto-detect language from Telegram)
    ↓
Bot shows inline keyboard to start browsing
```

### Phase 2 — Admin Approval
```
Admin bot receives profile card with photo
    ↓
Approve / Approve+Verify / Reject (can re-register) / Ban (permanent)
    ↓
User gets notification in their language
```

### Phase 3 — Swipe System
```
Before approval: 10 free swipes/day
After approval:  30 free swipes/day
Premium:         Unlimited swipes
    ↓
Like ❤️ / Nope 👎 / Super Like ⭐ (1/day free, unlimited premium)
    ↓
Swipe limit checked BEFORE animation (no false swipes)
    ↓
No more cards → "Check back later" message
    ↓
Match → both notified via bot
    ↓
Vibe Check question sent to both via bot inline buttons
    ↓
Premium: see contact directly | Free: upgrade prompt
```

### Phase 4 — Vibe Check (on Match) 🎯
```
Match ho → bot dono ko notify kare
    ↓
Bot AI-generated question bheje (daily, Groq `llama-3.3-70b-versatile` se)
Inline buttons: Option A | Option B
    ↓
Dono answer karein
    ↓
Same answer → "✨ Vibe Match! Great minds think alike!"
Alag answer → "🎭 Opposites Attract!"
    ↓
Result dono ko bot pe milta hai
```

### Phase 5 — Telegram Bot Chat (on Match)
```
Match ho → bot dono users ko notify kare
    ↓
Bot ek chat session create kare (1 minute timer)
    ↓
User A bot ko message kare → bot forward kare User B ko
User B bot ko message kare → bot forward kare User A ko
    ↓
Free user: 1 minute chat
Premium user: unlimited chat
    ↓
Timer khatam → bot dono ko notify kare "Chat ended"
```

---

## Bot Commands (Multilingual)

| Command | Description |
|---------|-------------|
| `/start` | Welcome → start browsing |
| `/profile` | View your profile |
| `/matches` | See your matches count |
| `/stats` | Your activity stats |
| `/premium` | Buy premium (Stars) |
| `/share` | Referral link |
| `/help` | Help message |
| `/about` | About app & developer |
| `/delete` | Delete account (confirm step) |
| `/confirmdelete` | Permanently delete account |
| `/language` | Change language |
| `/boost` | Boost profile (Premium only) |
| `/block <id>` | Block a user (removes match) |
| `/filters [min] [max] [km]` | Set age/distance filters |
| `/editprofile` | Edit profile (preserves approval) |

---

## Admin Bot Commands

| Command | Description |
|---------|-------------|
| `/pending` | Show pending profiles (with photo) |
| `/pendingall` | Show ALL users with status |
| `/stats` | Full app stats |
| `/broadcast msg` | Send to all users |
| `/remind` | Remind incomplete profile users |
| `/remind_blocked` | Notify rejected users |
| `/find <name>` | Search user by name |
| `/user <id>` | View user details |
| `/users` | List all users |
| `/cleanup` | Find incomplete users |
| `/confirmcleanup` | Delete incomplete users |
| `/deleteuser <id>` | Delete a user |
| `/fixuser <id>` | Reset a user to pending |
| `/auditlog [limit]` | Show recent admin actions |

### Approval Flow
- **Approve** → `is_approved=1`, notify user, swipes reset to 30
- **Approve+Verify** → same + verified badge ⭐
- **Reject** (can re-register) → `is_rejected=1`, notify
- **Ban** (permanent) → `is_blocked=1`, cannot re-register

---

## Database Schema

```sql
CREATE TABLE users (
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
    daily_swipes INTEGER DEFAULT 10,
    swipes_reset_date TEXT DEFAULT '',
    super_likes_left INTEGER DEFAULT 1,
    boosted_until TEXT DEFAULT '',
    referral_count INTEGER DEFAULT 0,
    mystery_until TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user INTEGER NOT NULL,
    to_user INTEGER NOT NULL,
    is_super INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER NOT NULL,
    user2_id INTEGER NOT NULL,
    matched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE skips (
    user_id INTEGER NOT NULL,
    skipped_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, skipped_id)
);

CREATE TABLE referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER NOT NULL,
    referred_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    reported_id INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE chat_sessions (
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

CREATE TABLE vibe_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    date TEXT NOT NULL UNIQUE
);

CREATE TABLE vibe_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(match_id, user_id)
);
```

---

## Swipe Limits

| Status | Daily Swipes | Super Likes |
|--------|-------------|-------------|
| Pending (before approval) | 10 | 1/day |
| Approved (free) | 30 | 1/day |
| Premium | Unlimited | Unlimited |

- Profile edit → swipes reset to 10 (pending state)

---

## Premium Features

| Feature | Free | Premium |
|---------|------|---------|
| Daily swipes | 30 (after approval) | Unlimited |
| Super likes | 1/day | Unlimited |
| See who liked you | ❌ | ✅ |
| Social handle on match | ❌ | ✅ |
| Profile boost | ❌ | ✅ (30 min) |
| Priority in feed | ❌ | ✅ |
| Bot chat on match | 1 minute | Unlimited |
| Mystery Mode 👻 | ❌ | ✅ (24h) |
| Chat History 💬 | ❌ | ✅ |
| Vibe Check 🎯 | ✅ | ✅ |
| Who Liked You | ❌ | ✅ |
| Profile Views | ✅ | ✅ |
| Block User | ✅ | ✅ |
| Chat Message Delete | ✅ | ✅ |

### Plans
- 1 Month — 150 ⭐
- 3 Months — 350 ⭐

### Premium Auto-Expiry
- Scheduler runs every 1 hour
- When `premium_until` < now → `is_premium=0`, `super_likes_left=1` reset

---

## User Features

### Block
- `/block <id>` — removes the match and hides the blocked user from your feed
- Blocked users can no longer see your profile or message you

### Filters
- `/filters [min_age] [max_age] [max_distance_km]`
- Applied server-side to the swipe feed (age range + distance via lat/lng)
- View current filters with no args

### Edit Profile
- `/editprofile` — re-enters setup to edit name, age, gender, city, bio, social, photo
- **Preserves approval status** — editing doesn't force re-review
- New submissions (via `/start`) still reset to pending

### Who Liked You (Premium)
- Shows everyone who liked or super-liked you, excluding blocked users
- Free users get an upgrade prompt

### Profile Views
- Tracked per user (`profile_views` column)
- Visible on `/profile/{id}` endpoint

### Chat Message Delete
- `/chat/message/{id}/delete` — delete a message you sent
- Replaces the text with "[message deleted]" in history

### Data Export
- `/api/export` — returns all of your data (profile, likes, matches, reports, blocks)
- GDPR-style data portability

---

## Rate Limiting
- `ratelimit.py` — sliding-window limiter per tg_id per action
- Like: 30/min | Report: 5/min | Profile view: 60/min | Unmatch: 30/min | Delete msg: 30/min
- Admin bot commands: 10/min per admin

---

## Audit Logging
- `audit_log` table tracks all admin actions (approve, reject, ban, delete, fix, cleanup)
- `/auditlog [limit]` command to review recent actions

---

## Photo Storage
- `storage.py` — uploads photos to a private Telegram storage chat
- Returns a `file_id` that the bot can later send to anyone
- Requires `TELEGRAM_STORAGE_CHAT_ID` env var

---

## Vibe Check Feature 🎯

- Triggers automatically on every match
- Daily question generated by Groq `llama-3.3-70b-versatile`
- Question cached in `vibe_questions` table (one per day)
- Bot sends question to both users with inline A/B buttons
- Both answers stored in `vibe_answers` table
- When both answer → result sent to both:
  - Same → "✨ Vibe Match!"
  - Different → "🎭 Opposites Attract!"
- Conversation starter built-in

---

## Mystery Mode Feature 👻

- Premium only
- Toggle via `/api/mystery/toggle`
- Active for 24 hours, auto-expires
- While active: photo hidden in feed, ghost animation shown
- Name, age, city, bio, interests still visible
- Match → photo revealed normally

---

## Schedulers (every minute/hour)

- **Every 1 min:** cleanup expired chat sessions, notify both users
- **Every 1 hour:** expire premium subscriptions

---

## Referral System
- Share link → friend joins → count++
- Every 3 friends joined = +10 swipes bonus
- Tracked in `referrals` table

---

## Environment Variables (Render)

```
TURSO_DATABASE_URL=
TURSO_DATABASE_KEY=
TELEGRAM_BOTS_KEY=           # Main bot token
ADMIN_BOT_TOKEN=             # Admin bot token
ADMIN_TG_ID=                 # Admin Telegram user ID
BOT_USERNAME=                # e.g. Yoursmeetbot
APP_URL=                     # e.g. https://yourmeet.onrender.com
SECRET_KEY=                  # JWT secret
GROQ_API_KEY=                # For vibe check questions (llama-3.3-70b-versatile)
TELEGRAM_STORAGE_CHAT_ID=    # Chat ID for photo storage
```

---

## Developer Info
- **App:** YourMeet
- **Developer:** @who_is_the-black_hat
- **Stack:** FastAPI + Python Telegram Bot + Turso
- **Hosting:** Render
- **Payments:** Telegram Stars (XTR)
- **AI:** Groq — `llama-3.3-70b-versatile`
- **Chat:** Telegram Bot forwarding (free, no extra service)
