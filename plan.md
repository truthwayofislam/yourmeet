# YourMeet — Complete Redesign Plan

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
- **Translation:** OpenRouter API — Google Gemma 4 (free)
- **Map:** Leaflet.js (free, OpenStreetMap)
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
- Bot messages are limited (~25 strings) so manual is practical
- Language stored in DB per user

### Web App UI
- **Base language: English**
- On first open, detect language from `Telegram.WebApp.initDataUnsafe.user.language_code`
- If language is `en` → no translation needed
- If other language → call `/api/translate` endpoint
- Backend calls **OpenRouter API (Gemma 4)** to translate all UI strings at once
- Translated strings cached in `localStorage` as `ym_strings_<lang>`
- On next visit → load from cache, no API call
- Cache cleared only if app version changes

### Translation Endpoint
```
POST /api/translate
Body: { "lang": "ru", "strings": { "key": "English text", ... } }
Response: { "key": "Translated text", ... }
```
- API key stored in Render env as `OPENROUTER_API_KEY`
- Model: `google/gemma-3-27b-it` via OpenRouter
- Key never exposed to frontend

---

## User Flow

### Phase 1 — Bot Entry
```
User opens bot → /start
    ↓
Language selection (inline buttons, auto-detect from Telegram)
    ↓
Welcome message in selected language
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
Step 7:  Interests/Hobbies (tags: Music, Travel, Fitness, etc.)
Step 8:  Phone Number (required)
Step 9:  City (required)
Step 10: Social Handle (Instagram/Telegram — required)
    ↓
Profile submitted → pending approval
    ↓
Bot sends: "Profile under review" message in user's language
```

### Phase 3 — Admin Approval
```
Admin bot receives profile card with photo
    ↓
Approve / Approve+Verify / Reject (can re-register) / Ban (permanent)
    ↓
User gets notification in their language
```

### Phase 4 — Swipe System (Web App Only)
```
Before approval: 10 free swipes/day
After approval:  30 free swipes/day
Premium:         Unlimited swipes
    ↓
Like ❤️ / Nope 👎 / Super Like ⭐ (1/day free, unlimited premium)
    ↓
Match → both notified via bot in their language
    ↓
Contact via social handle (Instagram/Telegram)
    ↓
Premium: see contact directly
Free: upgrade prompt
```

### Phase 5 — Map Discovery (Web App)
```
User opens Map tab → World map loads (Leaflet.js)
    ↓
User taps "Find My Match" button
    ↓
Backend returns random approved user (based on interested_in filter)
    ↓
Map animates — flyTo() that user's city location
    ↓
Profile card slides up — name, age, city, bio, interests, social handle
    ↓
Like ❤️ / Skip 👎
    ↓
Skip → map zooms back out → find next match
Like → if mutual → Match! → 1 min chat opens via Telegram bot
```

### Phase 6 — Telegram Bot Chat (on Match)
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
→ Session delete, koi data store nahi
```

---

## Bot Commands (Multilingual)

| Command | Description |
|---------|-------------|
| `/start` | Language select → welcome → open app |
| `/profile` | View your profile |
| `/matches` | See your matches count |
| `/stats` | Your activity stats |
| `/premium` | Buy premium (Stars) |
| `/share` | Referral link |
| `/help` | Help message |
| `/about` | About app & developer |
| `/delete` | Delete account |
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
- **Approve** → `is_approved=1`, notify user in their language
- **Approve+Verify** → same + verified badge ⭐
- **Reject** (can re-register) → `is_rejected=1`, notify
- **Ban** (permanent) → `is_blocked=1`, cannot re-register

---

## Web App Pages

### 1. Profile Setup (Multi-step wizard)
- Progress bar at top
- One step at a time
- Back button on each step
- Auto-save to localStorage on each step
- Translated UI via Gemma 4

### 2. Home / Swipe Feed
- Card-based swipe UI
- Photo carousel (multiple photos per card)
- Name, Age, City, Bio, Interests shown
- Like ❤️ / Nope 👎 / Super ⭐ buttons
- Touch/swipe gesture support
- Daily swipe counter shown
- Boost indicator if active

### 3. Map Discovery Page
- Full world map (Leaflet.js + OpenStreetMap)
- Clean map — no dots, no markers
- "Find My Match" button
- Click → backend picks random matching user
- Map flyTo() animation to that user's city
- Profile card slides up from bottom
- Name, Age, City, Bio, Interests, Social handle shown
- Like ❤️ / Skip 👎 buttons
- Match → 1 min Telegram bot chat opens

### 4. Matches Page
- List of matched profiles
- Social handle shown (premium) or locked (free)
- Match date shown
- Unmatch option

### 4. Profile Page
- View & edit all fields
- Photo management (add/remove)
- Premium badge if premium
- Verified badge ⭐ if verified
- Stats: likes given, received, matches

### 5. Premium Page
- Plan comparison table
- Telegram Stars payment
- Benefits list

### 6. Pending Page
- Shown before approval
- Progress steps: submitted → reviewing → approved
- 10 free swipes available during pending

---

## Database Schema

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    phone TEXT UNIQUE,
    age INTEGER,
    gender TEXT,
    interested_in TEXT DEFAULT 'both',
    bio TEXT DEFAULT '',
    city TEXT DEFAULT '',
    lat REAL DEFAULT 0,
    lng REAL DEFAULT 0,
    photo TEXT DEFAULT '',
    photos TEXT DEFAULT '',        -- JSON array of file_ids
    interests TEXT DEFAULT '',     -- JSON array of tags
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
    created_at TEXT
);

CREATE TABLE likes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user INTEGER,
    to_user INTEGER,
    is_super INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER,
    user2_id INTEGER,
    matched_at TEXT
);

CREATE TABLE skips (
    user_id INTEGER,
    skipped_id INTEGER,
    PRIMARY KEY (user_id, skipped_id)
);

CREATE TABLE referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id INTEGER,
    referred_id INTEGER,
    created_at TEXT
);

CREATE TABLE reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER,
    reported_id INTEGER,
    reason TEXT,
    created_at TEXT
);

CREATE TABLE chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER,
    user2_id INTEGER,
    user1_tg_id TEXT,
    user2_tg_id TEXT,
    expires_at TEXT,          -- 1 min for free, NULL for premium
    is_active INTEGER DEFAULT 1,
    created_at TEXT
);
```

### Indexes
```
CREATE INDEX idx_users_telegram_id ON users(telegram_id);
CREATE INDEX idx_users_gender ON users(gender);
CREATE INDEX idx_users_is_approved ON users(is_approved);
CREATE INDEX idx_likes_from ON likes(from_user);
CREATE INDEX idx_likes_to ON likes(to_user);
CREATE INDEX idx_chat_sessions_active ON chat_sessions(is_active);
```

---

## Swipe Limits

| Status | Daily Swipes | Super Likes |
|--------|-------------|-------------|
| Pending (before approval) | 10 | 1/day |
| Approved (free) | 30 | 1/day |
| Premium | Unlimited | Unlimited |

---

## Premium Features

| Feature | Free | Premium |
|---------|------|---------|
| Daily swipes | 30 (after approval) | Unlimited |
| Super likes | 1/day | Unlimited |
| See who liked you | ❌ | ✅ |
| Contact details on match | ❌ | ✅ |
| Profile boost | ❌ | ✅ (30 min) |
| Priority in feed | ❌ | ✅ |
| Bot chat on match | 1 minute | Unlimited |
| Map discovery | ✅ | ✅ |

### Plans
- 1 Month — 150 ⭐
- 3 Months — 350 ⭐

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
- Bottom navigation bar (Home, Matches, Profile, Premium)
- Toast notifications
- Loading skeletons
- Mobile first — designed for Telegram WebApp
- Touch-friendly buttons (min 44px)
- No horizontal scroll
- RTL support for Arabic

---

## Environment Variables (Render)

```
TURSO_DATABASE_URL=
TURSO_DATABASE_KEY=
TELEGRAM_BOTS_KEY=        # Main bot token
ADMIN_BOT_TOKEN=          # Admin bot token
ADMIN_TG_ID=              # Admin Telegram user ID
BOT_USERNAME=             # e.g. Yoursmeetbot
APP_URL=                  # e.g. https://yourmeet.onrender.com
SECRET_KEY=               # JWT secret
OPENROUTER_API_KEY=       # For Gemma 4 translation
```

---

## File Structure

```
/
├── main.py               # FastAPI app, lifespan, webhooks
├── database.py           # DB connection, init, helpers
├── strings.py            # Bot multilingual strings (14 langs)
├── storage.py            # Photo upload to Telegram
├── bot.py                # Main user bot
├── admin_bot.py          # Admin bot
├── requirements.txt
├── render.yaml
├── routers/
│   ├── auth.py           # TMA auth via initData
│   ├── profiles.py       # Swipe, like, match, profile
│   ├── setup.py          # Multi-step profile setup
│   ├── map.py            # Map discovery — random match + geocode
│   ├── chat.py           # Chat session create/end
│   ├── payment.py        # Telegram Stars
│   └── translate.py      # /api/translate endpoint
├── templates/
│   ├── base.html         # Base layout, TMA init, i18n init
│   ├── setup.html        # Multi-step wizard
│   ├── index.html        # Swipe feed
│   ├── map.html          # World map discovery
│   ├── matches.html      # Matches list
│   ├── profile.html      # Profile view/edit
│   ├── pending.html      # Pending approval page
│   └── premium.html      # Premium plans
└── static/
    ├── css/
    └── js/
        └── i18n.js       # Translation loader + cache
```

---

## Implementation Order

### Phase 1 — Foundation
1. `requirements.txt`
2. `database.py` — clean schema, no migrations needed (fresh DB)
3. `strings.py` — all 14 language bot strings
4. `storage.py` — photo upload
5. `main.py` — FastAPI app skeleton

### Phase 2 — Translation
1. `routers/translate.py` — OpenRouter Gemma 4 endpoint
2. `static/js/i18n.js` — detect lang, fetch, cache, apply

### Phase 3 — Auth & Setup
1. `routers/auth.py` — TMA auth via initData
2. `templates/setup.html` — 10-step wizard
3. `routers/setup.py` — submit endpoint

### Phase 4 — Web App Pages
1. `templates/index.html` — swipe feed
2. `templates/map.html` — world map discovery
3. `templates/pending.html` — pending page
4. `templates/matches.html` — matches
5. `templates/profile.html` — profile edit
6. `templates/premium.html` — premium page
7. `routers/profiles.py` — swipe/like/match API routes
8. `routers/map.py` — random match + geocode API
9. `routers/chat.py` — chat session API

### Phase 5 — Bots
1. `bot.py` — user bot (all commands)
2. `admin_bot.py` — admin bot (all commands)

### Phase 6 — Polish
1. Error handling everywhere
2. Rate limiting
3. Loading states in UI
4. Test all flows end to end

---

## Developer Info
- **App:** YourMeet
- **Developer:** @who_is_the-black_hat
- **Stack:** FastAPI + Python Telegram Bot + Turso + Telegram WebApp
- **Hosting:** Render
- **Payments:** Telegram Stars (XTR)
- **Translation:** OpenRouter — Google Gemma 4 (free)
- **Map:** Leaflet.js + OpenStreetMap (free)
- **Chat:** Telegram Bot forwarding (free, no extra service)
