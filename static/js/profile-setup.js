(() => {
  const loginPanel = document.getElementById("login-panel");
  const loginError = document.getElementById("login-error");
  const unavailableMessage = document.getElementById("telegram-unavailable");
  const widget = document.getElementById("telegram-widget");
  const form = document.getElementById("profile-form");
  const formError = document.getElementById("form-error");
  const accountStatus = document.getElementById("account-status");
  const result = document.getElementById("submission-result");
  const steps = Array.from(document.querySelectorAll(".form-step"));
  const stepTitles = ["About you", "Faith and family", "Your introduction"];
  const genderInput = form.elements.gender;
  const interestedInInput = form.elements.interested_in;
  const matchPreference = document.getElementById("match-preference");
  const photoInput = form.elements.photo;
  const submitButton = document.getElementById("submit-profile");
  let stepIndex = 0;
  let hasPhoto = false;

  function showStep(index) {
    stepIndex = index;
    steps.forEach((step, i) => {
      step.hidden = i !== index;
    });
    document.getElementById("step-label").textContent = `Step ${index + 1} of ${steps.length}`;
    document.getElementById("step-title").textContent = stepTitles[index];
    document.getElementById("progress-value").style.width = `${((index + 1) / steps.length) * 100}%`;
  }

  function setField(name, value) {
    if (value !== undefined && value !== null) {
      form.elements[name].value = value;
    }
  }

  function updateMatchPreference() {
    if (genderInput.value === "male") {
      interestedInInput.value = "female";
      matchPreference.textContent = "You will be shown profiles of women seeking marriage.";
    } else if (genderInput.value === "female") {
      interestedInInput.value = "male";
      matchPreference.textContent = "You will be shown profiles of men seeking marriage.";
    } else {
      interestedInInput.value = "";
      matchPreference.textContent = "Choose your gender to see how matches are selected.";
    }
  }

  function applyAccount(account) {
    const profile = account.profile;
    const status = account.status;
    setField("name", profile.name);
    setField("age", profile.age);
    setField("gender", profile.gender);
    setField("city", profile.city);
    setField("bio", profile.bio);
    setField("marital_intent", profile.marital_intent);
    setField("deeni_level", profile.deeni_level);
    setField("sect", profile.sect);
    setField("family_values", profile.family_values);
    form.elements.guardian_consented.checked = profile.guardian_consented;
    updateMatchPreference();
    form.elements.terms_accepted.checked = status.terms_accepted;
    hasPhoto = profile.has_photo;
    photoInput.required = !hasPhoto;
    document.getElementById("photo-help").textContent = hasPhoto
      ? "A photo is already saved on your profile. Upload another one only if you want to replace it. Maximum 5 MB."
      : "JPEG, PNG, or WebP; maximum 5 MB. A photo is required before submission.";

    accountStatus.hidden = false;
    if (status.approved) {
      accountStatus.textContent = "Your profile is approved. Updates will be saved to this same account.";
    } else if (status.rejected) {
      accountStatus.textContent = "Your previous submission needs changes. Submit an updated profile for another review.";
    } else if (status.submitted) {
      accountStatus.textContent = "Your saved profile is awaiting review. You can update it here.";
    } else {
      accountStatus.hidden = true;
    }

    loginPanel.hidden = true;
    form.hidden = false;
  }

  async function loadAccount() {
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (response.status === 401) {
      return false;
    }
    if (!response.ok) {
      throw new Error("We could not load your Sakinah account. Please try again.");
    }
    applyAccount(await response.json());
    return true;
  }

  window.onTelegramAuth = async (telegramUser) => {
    loginError.hidden = true;
    try {
      const response = await fetch("/api/auth/telegram", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(telegramUser),
      });
      if (!response.ok) {
        const body = await response.json();
        const messages = {
          account_unavailable: "This account cannot sign in. Please contact Sakinah support.",
          telegram_login_unavailable: "Telegram sign-in is not configured on this site yet.",
          invalid_telegram_auth: "Telegram could not verify your sign-in. Please sign in again.",
        };
        throw new Error(messages[body.error] || "Sign-in failed. Please try again.");
      }
      await loadAccount();
    } catch (error) {
      loginError.textContent = error.message || "Sign-in failed. Please try again.";
      loginError.hidden = false;
    }
  };

  const botUsername = widget.dataset.botUsername;
  if (botUsername) {
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", botUsername);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-userpic", "false");
    script.setAttribute("data-request-access", "write");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    widget.appendChild(script);
  } else {
    unavailableMessage.hidden = false;
  }

  document.querySelectorAll("[data-next]").forEach((button) => {
    button.addEventListener("click", () => {
      const visibleInputs = Array.from(steps[stepIndex].querySelectorAll("input, select, textarea"));
      if (!visibleInputs.every((input) => input.reportValidity())) {
        return;
      }
      showStep(Math.min(stepIndex + 1, steps.length - 1));
    });
  });

  document.querySelectorAll("[data-back]").forEach((button) => {
    button.addEventListener("click", () => showStep(Math.max(stepIndex - 1, 0)));
  });
  genderInput.addEventListener("change", updateMatchPreference);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.hidden = true;
    submitButton.disabled = true;
    submitButton.textContent = "Submitting…";

    try {
      const response = await fetch("/api/profile/setup", {
        method: "POST",
        credentials: "same-origin",
        body: new FormData(form),
      });
      const body = await response.json();
      if (response.status === 401) {
        throw new Error("Your sign-in has expired. Reload this page and sign in with Telegram again.");
      }
      if (!response.ok) {
        const fieldErrors = body.fields ? Object.values(body.fields).join(" ") : "";
        const messages = {
          photo_storage_unavailable: "Photo storage is not configured yet. Please contact Sakinah support before submitting.",
          photo_upload_failed: "Telegram could not store your photo. Check the image and try again.",
          rate_limited: "Please wait a few minutes before submitting again.",
        };
        throw new Error(fieldErrors || messages[body.error] || "We could not save your profile. Please try again.");
      }

      steps.forEach((step) => { step.hidden = true; });
      document.querySelector(".form-progress").hidden = true;
      result.hidden = false;
      result.innerHTML = body.status === "approved"
        ? "<h2>Your profile is updated</h2><p>Your changes have been saved to your existing Sakinah account.</p>"
        : "<h2>Your profile is with our review team</h2><p>We will notify you on Telegram when the review is complete.</p>";
      if (!body.location_resolved) {
        const note = document.createElement("p");
        note.className = "form-message";
        note.textContent = "We could not confirm this location. Your profile was saved, but distance-based suggestions may be less precise.";
        note.style.marginTop = "16px";
        result.appendChild(note);
      }
      if (body.status === "pending_review" && !body.review_notified) {
        const note = document.createElement("p");
        note.className = "form-message form-message-error";
        note.textContent = "Your profile was saved, but the review team could not be notified automatically. Please contact Sakinah support.";
        note.style.marginTop = "16px";
        result.appendChild(note);
      }
    } catch (error) {
      formError.textContent = error.message || "We could not save your profile. Please try again.";
      formError.hidden = false;
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "Submit for review";
    }
  });

  loadAccount().catch((error) => {
    loginError.textContent = error.message;
    loginError.hidden = false;
  });
  showStep(0);
})();
