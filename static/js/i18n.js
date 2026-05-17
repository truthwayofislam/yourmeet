const I18N_VERSION = "1.0";

const DEFAULT_STRINGS = {
  // Setup
  setup_title: "Create Your Profile",
  step_name: "What's your name?",
  step_age: "How old are you?",
  step_gender: "Your gender",
  step_interested: "Interested in",
  step_photos: "Add your photos",
  step_bio: "About you",
  step_interests: "Your interests",
  step_phone: "Your phone number",
  step_city: "Your city",
  step_social: "Your social handle",
  btn_next: "Next",
  btn_back: "Back",
  btn_submit: "Submit Profile",
  male: "Male",
  female: "Female",
  both: "Both",
  // Home / Swipe
  swipes_left: "swipes left",
  no_more_profiles: "No more profiles right now. Check back later!",
  its_a_match: "It's a Match! 💕",
  match_msg: "You and {name} liked each other!",
  btn_keep_swiping: "Keep Swiping",
  btn_view_matches: "View Matches",
  limit_reached: "Daily swipe limit reached. Come back tomorrow or upgrade to Premium!",
  // Map
  map_title: "Discover on Map",
  btn_find_match: "Find My Match",
  btn_like: "Like ❤️",
  btn_skip: "Skip 👎",
  finding: "Finding someone for you...",
  no_users_map: "No users found nearby. Try again later!",
  // Matches
  matches_title: "Your Matches",
  no_matches: "No matches yet. Keep swiping!",
  matched_on: "Matched on",
  btn_unmatch: "Unmatch",
  contact_locked: "🔒 Upgrade to Premium to see contact",
  // Profile
  profile_title: "Your Profile",
  btn_edit: "Edit Profile",
  btn_save: "Save Changes",
  btn_boost: "Boost Profile 🚀",
  likes_given: "Likes Given",
  likes_received: "Likes Received",
  total_matches: "Total Matches",
  verified: "Verified ⭐",
  premium_badge: "Premium 👑",
  // Premium
  premium_title: "YourMeet Premium",
  plan_monthly: "1 Month",
  plan_quarterly: "3 Months",
  btn_buy: "Buy Now",
  // Pending
  pending_title: "Profile Under Review",
  pending_msg: "Your profile is being reviewed. You can still swipe while you wait!",
  step_submitted: "Submitted",
  step_reviewing: "Reviewing",
  step_approved: "Approved",
  // Common
  loading: "Loading...",
  error_generic: "Something went wrong. Please try again.",
  btn_upgrade: "Upgrade to Premium 👑",
  logout: "Logout",
  report: "Report",
  // Nav
  nav_home: "Home",
  nav_map: "Map",
  nav_matches: "Matches",
  nav_profile: "Profile",
  nav_premium: "Premium",
};

let _strings = { ...DEFAULT_STRINGS };

async function initI18n() {
  const tg = window.Telegram?.WebApp;
  const lang = tg?.initDataUnsafe?.user?.language_code?.slice(0, 2) || "en";

  if (lang === "en") return;

  const cacheKey = `ym_strings_${lang}_v${I18N_VERSION}`;
  const cached = localStorage.getItem(cacheKey);
  if (cached) {
    try {
      _strings = { ...DEFAULT_STRINGS, ...JSON.parse(cached) };
      applyStrings();
      return;
    } catch {}
  }

  try {
    const resp = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang, strings: DEFAULT_STRINGS }),
    });
    if (resp.ok) {
      const translated = await resp.json();
      _strings = { ...DEFAULT_STRINGS, ...translated };
      localStorage.setItem(cacheKey, JSON.stringify(translated));
      applyStrings();
    }
  } catch (e) {
    console.warn("[i18n] translation failed, using English", e);
  }
}

function applyStrings() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (_strings[key]) el.textContent = _strings[key];
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (_strings[key]) el.placeholder = _strings[key];
  });
}

function t(key, vars = {}) {
  let str = _strings[key] || DEFAULT_STRINGS[key] || key;
  Object.entries(vars).forEach(([k, v]) => {
    str = str.replaceAll(`{${k}}`, v);
  });
  return str;
}

export { initI18n, applyStrings, t };
