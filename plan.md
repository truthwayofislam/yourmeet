# YourMeet — Complete Plan (Updated)

## Overview
Full redesign of YourMeet dating app with multilingual support, improved UX, and streamlined bot + web app flow.

---

## Tech Stack
- **Backend:** FastAPI (Python)
- **Bot:** Python Telegram Bot (PTB)
- **Database:** Turso (libsql)
- **Frontend:** Telegram Mini App (TMA)
- **Hosting:** Render
- **Payments:** Telegram Stars (XTR)
- **Translation:** OpenRouter API — Google Gemma 4 31B (`google/gemma-4-31b-it:free`)
- **Vibe Check Questions:** OpenRouter API — same model
- **Map:** Leaflet.js + CartoDB Dark Matter tiles (free, no key)
- **Map Animation:** leaflet-ant-path (animated arrow)
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

## Translation Strategy

### Bot Messages
- Manually written in `strings.py` for all 14 languages
- All strings including `btn_upgrade` present in all 14 languages
- Language stored in DB per user

### Web App UI
- Base language: English
- On first open, detect language from `Telegram.WebApp.initDataUnsafe.user.language_code`
- If `en` → no translation needed
- If other → call `/api/translate` endpoint
- Backend calls OpenRouter Gemma 4 31B to translate all UI strings at once
- Translated strings cached in `localStorage` as `ym_strings_<lang>_v1.0`
- On next visit → load from cache, no API call

### Translation Endpoint
```
POST /api/translate
Body: { "lang": "ru", "strings": { "key": "English text", ... } }
Response: { "key": "Translated text", ... }
```
- Model: `google/gemma-4-31b-it:free` via OpenRouter
- Key never exposed to frontend

---

## User Flow

### Phase 1 — Bot Entry
```
User opens bot → /start
    ↓
Welcome message (auto-detect language from Telegram)
    ↓
"Open YourMeet" button → opens TMA (web app)
```

### Phase 2 — TMA Profile Setup (Multi-step)
```
Step 1:  Name
Step 2:  Age (18-60)
Step 3:  Gender (Male / Female)
Step 4:  Interested In (Male / Female / Both)
Step 5:  Photos (min 1, max 6)
Step 6:  Bio (min 10 chars)
Step 7:  Interests/Hobbies (tags)
Step 8:  Phone Number (country code + number)
Step 9:  City (required, geocoded to lat/lng)
Step 10: Social Handle (Instagram/Telegram — required)
    ↓
Profile submitted → pending approval
    ↓
Bot sends: "Profile under review" in user's language
```

### Phase 3 — Admin Approval
```
Admin bot receives profile card with photo
    ↓
Approve / Approve+Verify / Reject (can re-register) / Ban (permanent)
    ↓
User gets notification in their language
```

### Phase 4 — Swipe System
```
Before approval: 10 free swipes/day
After approval:  30 free swipes/day
Premium:         Unlimited swipes
    ↓
Like ❤️ / Nope 👎 / Super Like ⭐ (1/day free, unlimited premium)
    ↓
Swipe limit checked BEFORE animation (no false swipes)
    ↓
Cards khatam → /api/feed se naye cards load (infinite scroll)
    ↓
Match → both notified via bot
    ↓
Vibe Check question sent to both via bot inline buttons
    ↓
Premium: see contact directly | Free: upgrade prompt
```

### Phase 5 — Map Discovery
```
User opens Map tab → CartoDB Dark Matter map loads
    ↓
User taps "Find My Match"
    ↓
Map zooms out to world view
    ↓
White dot appears at user's location (browser geolocation)
    ↓
Animated pink dashed arrow draws from user → match city
    ↓
Map fits both points in view
    ↓
Pink pulse marker appears at destination
    ↓
FlyTo destination (zoom 11)
    ↓
Profile card slides up — name, age, city, bio, interests
    ↓
Like ❤️ / Skip 👎 (swipe limit applies)
    ↓
Skip → map resets → find next
Like → if mutual → Match! + Vibe Check sent
```

### Phase 6 — Vibe Check (on Match) 🎯
```
Match ho → bot dono ko notify kare
    ↓
Bot AI-generated question bheje (daily, Gemma 4 31B se)
Inline buttons: Option A | Option B
    ↓
Dono answer karein
    ↓
Same answer → "✨ Vibe Match! Great minds think alike!"
Alag answer → "🎭 Opposites Attract!"
    ↓
Result dono ko bot pe milta hai
```

### Phase 7 — Telegram Bot Chat (on Match)
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
| `/start` | Welcome → open app |
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

---

## Admin Bot Commands

| Command | Description |
|---------|-------------|
| `/pending` | Show pending profiles (with photo) |
| `/stats` | Full app stats |
| `/broadcast msg` | Send to all users |
| `/remind` | Remind incomplete profile users |
| `/remind_blocked` | Notify rejected users |
| `/find <name>` | Search user by name |
| `/user <id>` | View user details |

### Approval Flow
- **Approve** → `is_approved=1`, notify user, swipes reset to 30
- **Approve+Verify** → same + verified badge ⭐
- **Reject** (can re-register) → `is_rejected=1`, notify
- **Ban** (permanent) → `is_blocked=1`, cannot re-register

---

## Web App Pages

### 1. Profile Setup (Multi-step wizard)
- Progress bar at top
- One step at a time, back button on each
- Auto-save to localStorage on each step
- Country code selector (auto-detect from Telegram language)
- Translated UI via Gemma 4 31B

### 2. Home / Swipe Feed
- Card-based swipe UI with drag gestures
- Photo carousel (tap left/right to cycle)
- Mystery Mode cards show 👻 ghost animation instead of photo
- Like ❤️ / Nope 👎 / Super ⭐ buttons
- Swipe limit checked before animation
- Daily swipe counter shown
- Infinite scroll — new cards load automatically
- Bio translate button (per card)

### 3. Map Discovery Page
- CartoDB Dark Matter tiles (cinematic dark map)
- Top/bottom gradient overlays for depth
- "Find My Match" pill button with pink glow
- Click → animated sequence:
  1. Zoom out to world
  2. White dot at user location
  3. Pink animated dashed arrow to match
  4. fitBounds to show both
  5. Pulse marker at destination
  6. FlyTo zoom in
  7. Profile card slides up
- Glassmorphism profile card with pink border
- Like ❤️ / Skip 👎 (swipe limit applies)

### 4. Matches Page
- List of matched profiles
- Social handle shown (premium) or locked (free)
- Match date shown
- Unmatch option
- Start chat button

### 5. Profile Page
- View & edit all fields
- Stats: likes given, received, matches
- Premium badge, Verified badge, Approved/Pending badge
- Edit Profile button
- 🚀 Boost button (premium)
- 👻 Mystery Mode toggle button (premium) — glows pink when active
- "Who Liked You" section (premium) — avatar grid with super like indicator
- "See Who Liked You" locked card (free) → upgrade prompt
- Delete Account button

### 6. Premium Page
- Comparison table (Free vs Premium) — all features listed
- "What you get" feature list with icons
- Plan cards: 1 Month (150 ⭐) / 3 Months (350 ⭐)
- Active premium badge with expiry date

### 7. Pending Page
- Progress steps: Submitted → Reviewing → Approved
- Swipe preview cards while waiting
- Profile summary

---

## Database Schema

```sql
CREATE TABLE users (
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
- Map likes use same swipe limit as feed

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
| Map discovery | ✅ | ✅ |
| Vibe Check 🎯 | ✅ | ✅ |

### Plans
- 1 Month — 150 ⭐
- 3 Months — 350 ⭐

### Premium Auto-Expiry
- Scheduler runs every 1 hour
- When `premium_until` < now → `is_premium=0`, `super_likes_left=1` reset

---

## Vibe Check Feature 🎯

- Triggers automatically on every match
- Daily question generated by Gemma 4 31B via OpenRouter
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
- Toggle from profile page (👻 button)
- Active for 24 hours, auto-expires
- While active: photo hidden in feed, ghost animation shown
- Name, age, city, bio, interests still visible
- Match → photo revealed normally
- Button glows pink when active

---

## Security

- **Telegram initData verification** — `verify_init_data()` uses `WebAppData` HMAC-SHA256
- If initData present and invalid → 403 returned
- If initData missing (dev mode) → allowed
- JWT tokens for session auth (cookie-based)
- Account delete clears all related data (likes, matches, skips, referrals, reports)

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

## UI Design

### Colors
- Primary: `#E91E8C` (pink/magenta)
- Secondary: `#9C27B0` (purple)
- Background: `#0f0f1a`
- Card: `#1a1a2e`
- Surface: `#16213e`

### Design Style
- Glassmorphism cards
- Smooth swipe animations (CSS transform)
- Bottom navigation bar (Home, Map, Matches, Profile, Premium)
- Toast notifications
- Loading skeletons + spinners
- Mobile first — designed for Telegram WebApp
- Touch-friendly buttons (min 44px)
- No horizontal scroll
- RTL support for Arabic

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
OPENROUTER_API_KEY=          # For Gemma 4 31B (translation + vibe questions)
TELEGRAM_STORAGE_CHAT_ID=    # Chat ID for photo storage
```

---

## File Structure

```
/
├── main.py               # FastAPI app, lifespan, webhooks, schedulers
├── database.py           # DB connection, init, helpers
├── strings.py            # Bot multilingual strings (14 langs, all keys)
├── storage.py            # Photo upload to Telegram
├── bot.py                # Main user bot (all commands + vibe callback)
├── admin_bot.py          # Admin bot (all commands)
├── templating.py         # Jinja2 setup with custom filters
├── requirements.txt
├── render.yaml
├── routers/
│   ├── auth.py           # TMA auth via initData verification + account delete
│   ├── profiles.py       # Swipe, like, match, profile, feed, likes received
│   ├── setup.py          # Multi-step profile setup + geocoding
│   ├── map.py            # Map discovery + /api/map/like
│   ├── chat.py           # Chat session create/end/forward/cleanup
│   ├── payment.py        # Telegram Stars
│   ├── translate.py      # /api/translate — Gemma 4 31B
│   └── vibe.py           # Vibe Check + Mystery Mode
├── templates/
│   ├── base.html         # Base layout, TMA init, i18n init, auth
│   ├── setup.html        # Multi-step wizard (10 steps)
│   ├── index.html        # Swipe feed (infinite scroll, mystery support)
│   ├── map.html          # Dark map + animated arrow discovery
│   ├── matches.html      # Matches list
│   ├── profile.html      # Profile view/edit + mystery toggle + who liked you
│   ├── pending.html      # Pending approval page
│   └── premium.html      # Premium plans (all features listed)
└── static/
    ├── css/
    └── js/
        └── i18n.js       # Translation loader + cache
```

---

## Developer Info
- **App:** YourMeet
- **Developer:** @who_is_the-black_hat
- **Stack:** FastAPI + Python Telegram Bot + Turso + Telegram WebApp
- **Hosting:** Render
- **Payments:** Telegram Stars (XTR)
- **Translation + AI:** OpenRouter — Google Gemma 4 31B (free)
- **Map:** Leaflet.js + CartoDB Dark Matter + leaflet-ant-path
- **Chat:** Telegram Bot forwarding (free, no extra service)
