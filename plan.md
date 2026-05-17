# YourMeet — Complete Redesign Plan

## Overview
Full redesign of YourMeet dating app with multilingual support, improved UX, and streamlined bot + web app flow.

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
Step 1: Name
Step 2: Age (18-60)
Step 3: Gender (Male / Female)
Step 4: Interested In (Male / Female / Both)
Step 5: Photos (min 1, max 6)
Step 6: Bio (min 10 chars)
Step 7: Interests/Hobbies (tags: Music, Travel, Fitness, etc.)
Step 8: Phone Number (required, intl-tel-input)
Step 9: Email (optional)
Step 10: City (optional) contry required
    ↓
Profile submitted → pending approval
    ↓
Bot sends: "Profile under review" message
```

### Phase 3 — Admin Approval
```
Admin bot receives profile card with photo
    ↓
Approve / Approve+Verify / Block
    ↓
User gets notification in their language
```

### Phase 4 — Swipe System (Web App Only)
```
Before approval: 10 free swipes/day
After approval:  30 free swipes/day
Premium:         Unlimited swipes
    ↓
Like / Nope / Super Like (1/day free, unlimited premium)
    ↓
Match → both notified via bot in their language
    ↓
Contact via social handle (Instagram/Telegram)
    ↓
Premium: see contact directly
Free: upgrade prompt
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

**Removed from bot:**
- `/swipe` — moved to web app only
- `/setup` — replaced by TMA profile setup
- `/edit` — replaced by TMA profile edit
- `/boost` — kept (premium only)

---

## Web App (TMA) Pages

### 1. Profile Setup (Multi-step wizard)
- Progress bar at top
- Step by step — one thing at a time
- Back button on each step
- Auto-save on each step

### 2. Home / Swipe Feed
- Card-based swipe UI
- Photo carousel (multiple photos)
- Name, Age, City, Bio, Interests shown
- Like ❤️ / Nope 👎 / Super ⭐ buttons
- Daily swipe counter shown
- Boost indicator if active

### 3. Matches Page
- Grid of matched profiles
- Social handle shown (premium) or locked (free)
- Match date shown
- Unmatch option

### 4. Profile Page
- View & edit all fields
- Photo management (add/remove/reorder)
- Premium badge if premium
- Verified badge if verified
- Stats (likes given, received, matches)

### 5. Premium Page
- Plan comparison
- Stars payment
- Benefits list

### 6. Pending Page
- Shown before approval
- Steps: submitted → reviewing → approved
- 10 swipes available during pending

---

## Swipe Limits

| Status | Daily Swipes | Super Likes |
|--------|-------------|-------------|
| Pending (before approval) | 10 | 1/day |
| Approved (free) | 30 | 1/day |
| Premium | Unlimited | Unlimited |

---

## Multilingual System

### Bot
- Language stored in DB (`language` column)
- `/language` command to change
- Auto-detect from `user.language_code` on `/start`
- All bot messages in `strings.py` dict

### Web App
- Language passed via URL param or localStorage
- All UI strings in `i18n.js`
- RTL support for Arabic

### strings.py structure
```python
STRINGS = {
    "en": {
        "welcome": "Hey {name}! 👋 Welcome to YourMeet 💕",
        "language_select": "🌍 Select your language:",
        "profile_approved": "🎉 Your profile has been approved!",
        ...
    },
    "es": {
        "welcome": "¡Hola {name}! 👋 Bienvenido a YourMeet 💕",
        ...
    },
    ...
}
```

---

## Database Changes

### New columns needed
```sql
ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en';
ALTER TABLE users ADD COLUMN interested_in TEXT DEFAULT 'both';
ALTER TABLE users ADD COLUMN interests TEXT DEFAULT '';
ALTER TABLE users ADD COLUMN email TEXT;
ALTER TABLE users ADD COLUMN photos TEXT DEFAULT '';  -- JSON array of file_ids
ALTER TABLE users ADD COLUMN setup_step INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN pre_approval_swipes INTEGER DEFAULT 10;
```

### Fix existing issues
- Remove `SELECT *` everywhere → use explicit column names
- Remove all `row[index]` access → use named queries
- Add proper indexes on `telegram_id`, `gender`, `is_approved`

---

## Admin Bot Improvements

### Commands
| Command | Description |
|---------|-------------|
| `/pending` | Show pending profiles (with photo) |
| `/stats` | Full app stats |
| `/broadcast msg` | Send to all users |
| `/remind` | Remind incomplete profiles |
| `/remind_blocked` | Notify rejected users |
| `/approve_seed` | Approve seed profiles |
| `/remove_fake` | Delete all fake profiles |
| `/find <name>` | Search user by name |
| `/user <id>` | View user details |

### Approval flow
- Approve → `is_approved=1`, notify user in their language
- Approve+Verify → same + verified badge
- Block (reject, can re-register) → `is_rejected=1`, notify
- Ban (permanent) → `is_blocked=1`, cannot re-register

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

### Plans
- 1 Month — 150 ⭐
- 3 Months — 350 ⭐

---

## Referral System
- Share link → friend joins → count++
- Every 3 friends = +10 swipes bonus
- Referral tracked in `referrals` table

---

## UI Design Guidelines

### Colors
- Primary: `#E91E8C` (pink/magenta)
- Secondary: `#9C27B0` (purple)
- Background: Telegram theme vars
- Cards: glassmorphism style

### Components
- Profile cards with photo carousel
- Smooth swipe animations (CSS transform)
- Bottom navigation bar (Home, Matches, Profile)
- Toast notifications
- Loading skeletons

### Mobile First
- Designed for Telegram WebApp (mobile)
- Touch-friendly buttons (min 44px)
- Swipe gestures on cards
- No horizontal scroll

---

## Implementation Order

### Phase 1 — Foundation
1. `strings.py` — all language strings
2. DB migrations — new columns
3. Fix all `SELECT *` → explicit columns
4. Fix all `row[index]` → named queries

### Phase 2 — Bot Redesign
1. `/start` with language selection
2. `/language` command
3. All commands multilingual
4. Remove swipe from bot
5. TMA link generation

### Phase 3 — Web App Redesign
1. Multi-step profile setup wizard
2. Improved swipe UI with animations
3. Multiple photos support
4. Interests/tags system
5. i18n for web app

### Phase 4 — Admin Improvements
1. `/find` and `/user` commands
2. Language shown in pending card
3. Permanent ban vs reject distinction

### Phase 5 — Polish
1. Performance optimization
2. Error handling everywhere
3. Rate limiting
4. Testing all flows

---

## Developer Info
- **App:** YourMeet
- **Developer:** @who_is_the-black_hat
- **Stack:** FastAPI + Python Telegram Bot + Turso (libsql) + Telegram WebApp
- **Hosting:** Render
- **Payments:** Telegram Stars (XTR)
