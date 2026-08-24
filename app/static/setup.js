/* Settings page logic */
"use strict";

const $ = (sel) => document.querySelector(sel);

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

// Held in sessionStorage so it survives navigation but not a closed tab.
const TOKEN_KEY = "settingsToken";

async function postJSON(url, body, retried = false) {
  const headers = { "Content-Type": "application/json" };
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers["X-Settings-Token"] = token;

  const r = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (r.status === 401 && !retried) {
    const entered = window.prompt(
      "This instance requires a settings token (SETTINGS_TOKEN in .env):", "");
    if (!entered) throw new Error("Settings token required");
    sessionStorage.setItem(TOKEN_KEY, entered.trim());
    return postJSON(url, body, true);  // one retry, then surface the error
  }
  if (!r.ok) {
    if (r.status === 401) sessionStorage.removeItem(TOKEN_KEY);
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(err.detail || r.statusText);
  }
  return r.json();
}

function showResult(el, ok, message) {
  el.textContent = message;
  el.className = "action-result " + (ok ? "ok" : "err");
  // auto-clear after 15 seconds
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.textContent = ""; el.className = "action-result"; }, 15000);
}

function setBadge(el, configured, status) {
  if (configured && status && (status.last_success || status.connected)) {
    el.textContent = status.device_count !== undefined
      ? `✓ ${status.device_count} device${status.device_count === 1 ? "" : "s"}`
      : "✓ Connected";
    el.className = "status-badge good";
  } else if (configured) {
    el.textContent = status && status.last_error ? "✗ Error" : "✓ Configured";
    el.className = "status-badge " + (status && status.last_error ? "bad" : "good");
    if (status && status.last_error) el.title = status.last_error;
  } else {
    el.textContent = "Not configured";
    el.className = "status-badge muted";
  }
}

// ---------------------------------------------------------------------------
// Load current settings
// ---------------------------------------------------------------------------

async function loadSettings() {
  try {
    const s = await getJSON("/api/settings");

    // Govee
    setBadge($("#govee-badge"), s.govee.configured, s.govee.status);
    $("#govee-api-key").placeholder = s.govee.api_key_hint || "not set";

    // Qingping
    setBadge($("#qingping-badge"), s.qingping.configured, s.qingping.status);
    $("#qingping-app-key").placeholder = s.qingping.app_key_hint || "not set";
    $("#qingping-app-secret").placeholder = s.qingping.app_secret_hint || "not set";

    // Govee IoT
    setBadge($("#govee-iot-badge"), s.govee_iot.configured, s.govee_iot.status);
    if (s.govee_iot.email) $("#govee-iot-email").placeholder = s.govee_iot.email;
    if (s.govee_iot.password_hint) $("#govee-iot-password").placeholder = s.govee_iot.password_hint;

    // Energy
    $("#energy-peak-rate").value = s.energy.peak_rate;
    $("#energy-offpeak-rate").value = s.energy.offpeak_rate;
    $("#energy-peak-start").value = s.energy.peak_start_hour;
    $("#energy-peak-end").value = s.energy.peak_end_hour;

    // Polling
    $("#poll-govee").value = s.polling.govee_poll_seconds;
    $("#poll-qingping").value = s.polling.qingping_poll_seconds;
    $("#poll-iot").value = s.polling.govee_iot_poll_seconds;
    $("#poll-backfill").value = s.polling.qingping_backfill_days;

  } catch (e) {
    console.error("Failed to load settings:", e);
  }
}

// ---------------------------------------------------------------------------
// Show/hide password toggles
// ---------------------------------------------------------------------------

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".eye-btn");
  if (!btn) return;
  const input = $(`#${btn.dataset.target}`);
  if (!input) return;
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  btn.textContent = showing ? "👁" : "👁‍🗨";
});

// ---------------------------------------------------------------------------
// Govee
// ---------------------------------------------------------------------------

$("#govee-test").addEventListener("click", async () => {
  const key = $("#govee-api-key").value.trim();
  if (!key) { showResult($("#govee-result"), false, "Enter an API key first"); return; }
  const btn = $("#govee-test");
  btn.disabled = true;
  showResult($("#govee-result"), true, "Testing…");
  try {
    const r = await postJSON("/api/settings/test", { integration: "govee", api_key: key });
    showResult($("#govee-result"), r.ok, r.message);
  } catch (e) {
    showResult($("#govee-result"), false, e.message);
  } finally {
    btn.disabled = false;
  }
});

$("#govee-save").addEventListener("click", async () => {
  const key = $("#govee-api-key").value.trim();
  if (!key) { showResult($("#govee-result"), false, "Enter an API key first"); return; }
  try {
    const r = await postJSON("/api/settings/save", { updates: { GOVEE_API_KEY: key } });
    showResult($("#govee-result"), true,
      r.restart_required ? "Saved — restart to apply" : "Saved");
    if (r.restart_required) $("#restart-banner").hidden = false;
    loadSettings();
  } catch (e) {
    showResult($("#govee-result"), false, e.message);
  }
});

// ---------------------------------------------------------------------------
// Qingping
// ---------------------------------------------------------------------------

$("#qingping-test").addEventListener("click", async () => {
  const key = $("#qingping-app-key").value.trim();
  const secret = $("#qingping-app-secret").value.trim();
  if (!key || !secret) {
    showResult($("#qingping-result"), false, "Enter both App Key and App Secret");
    return;
  }
  const btn = $("#qingping-test");
  btn.disabled = true;
  showResult($("#qingping-result"), true, "Testing…");
  try {
    const r = await postJSON("/api/settings/test", {
      integration: "qingping", app_key: key, app_secret: secret,
    });
    showResult($("#qingping-result"), r.ok, r.message);
  } catch (e) {
    showResult($("#qingping-result"), false, e.message);
  } finally {
    btn.disabled = false;
  }
});

$("#qingping-save").addEventListener("click", async () => {
  const key = $("#qingping-app-key").value.trim();
  const secret = $("#qingping-app-secret").value.trim();
  if (!key || !secret) {
    showResult($("#qingping-result"), false, "Enter both App Key and App Secret");
    return;
  }
  try {
    const r = await postJSON("/api/settings/save", {
      updates: { QINGPING_APP_KEY: key, QINGPING_APP_SECRET: secret },
    });
    showResult($("#qingping-result"), true,
      r.restart_required ? "Saved — restart to apply" : "Saved");
    if (r.restart_required) $("#restart-banner").hidden = false;
    loadSettings();
  } catch (e) {
    showResult($("#qingping-result"), false, e.message);
  }
});

// ---------------------------------------------------------------------------
// Govee IoT (2FA flow)
// ---------------------------------------------------------------------------

$("#govee-iot-save").addEventListener("click", async () => {
  const email = $("#govee-iot-email").value.trim();
  const pass = $("#govee-iot-password").value.trim();
  if (!email || !pass) {
    showResult($("#govee-iot-result"), false, "Enter both email and password");
    return;
  }
  const btn = $("#govee-iot-save");
  btn.disabled = true;
  try {
    // First save credentials
    await postJSON("/api/settings/save", {
      updates: { GOVEE_EMAIL: email, GOVEE_PASSWORD: pass },
    });
    // Then trigger the 2FA code email by calling verify with empty code
    showResult($("#govee-iot-result"), true, "Saved — sending verification email…");
    const r = await postJSON("/api/settings/govee-iot/verify", { code: "" });
    if (r.ok) {
      showResult($("#govee-iot-result"), true, r.message);
    } else if (r.two_factor) {
      showResult($("#govee-iot-result"), true,
        "✓ Credentials saved. Check your email for a verification code.");
    } else {
      showResult($("#govee-iot-result"), false, r.message);
    }
    $("#restart-banner").hidden = false;
    loadSettings();
  } catch (e) {
    showResult($("#govee-iot-result"), false, e.message);
  } finally {
    btn.disabled = false;
  }
});

$("#govee-iot-verify").addEventListener("click", async () => {
  const code = $("#govee-iot-code").value.trim();
  if (!code) {
    showResult($("#govee-iot-verify-result"), false, "Enter the code from your email");
    return;
  }
  const btn = $("#govee-iot-verify");
  btn.disabled = true;
  showResult($("#govee-iot-verify-result"), true, "Verifying…");
  try {
    const r = await postJSON("/api/settings/govee-iot/verify", { code });
    showResult($("#govee-iot-verify-result"), r.ok, r.message);
    if (r.ok) {
      showResult($("#govee-iot-result"), true,
        "✓ Verified — restart to connect the IoT channel");
      $("#restart-banner").hidden = false;
    }
    loadSettings();
  } catch (e) {
    showResult($("#govee-iot-verify-result"), false, e.message);
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Energy cost
// ---------------------------------------------------------------------------

$("#energy-save").addEventListener("click", async () => {
  try {
    const r = await postJSON("/api/settings/save", {
      updates: {
        PEAK_RATE_PER_KWH: $("#energy-peak-rate").value,
        OFFPEAK_RATE_PER_KWH: $("#energy-offpeak-rate").value,
        PEAK_START_HOUR: $("#energy-peak-start").value,
        PEAK_END_HOUR: $("#energy-peak-end").value,
      },
    });
    showResult($("#energy-result"), true,
      r.restart_required ? "Saved — restart to apply" : "Saved");
    if (r.restart_required) $("#restart-banner").hidden = false;
  } catch (e) {
    showResult($("#energy-result"), false, e.message);
  }
});

// ---------------------------------------------------------------------------
// Polling intervals
// ---------------------------------------------------------------------------

$("#polling-save").addEventListener("click", async () => {
  try {
    const r = await postJSON("/api/settings/save", {
      updates: {
        GOVEE_POLL_SECONDS: $("#poll-govee").value,
        QINGPING_POLL_SECONDS: $("#poll-qingping").value,
        GOVEE_IOT_POLL_SECONDS: $("#poll-iot").value,
        QINGPING_BACKFILL_DAYS: $("#poll-backfill").value,
      },
    });
    showResult($("#polling-result"), true,
      r.restart_required ? "Saved — restart to apply" : "Saved");
    if (r.restart_required) $("#restart-banner").hidden = false;
  } catch (e) {
    showResult($("#polling-result"), false, e.message);
  }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadSettings();
