/*!
 * Bellas XV Booking Widget — embeddable IIFE
 * Three-step booking flow + attribution + abandon telemetry.
 * No build step, no dependencies. Self-contained.
 */
(function (window, document) {
  'use strict';

  if (window.BellasBookingWidget && window.BellasBookingWidget._initialized) {
    return;
  }

  // ---------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------

  var NS = 'bxv';
  var VISITOR_COOKIE = 'bxv_vid';
  var ATTRIBUTION_LS_KEY = 'bxv_attribution';
  // Boutique Experience handoff keys, written by widgets/bellas-fit-prep-tool.js
  // when the customer completes their profile before booking. Both are cleared
  // on successful submission. The summary is the textual fallback for browsers
  // where the server profile create failed; the profile id is the canonical
  // server-side link.
  var SUMMARY_LS_KEY = 'bxv_fit_prep_summary';
  var PROFILE_ID_LS_KEY = 'bxv_boutique_profile_id';
  // Post-booking handoff key, written after a booking succeeds so a customer
  // who opens the standalone sizing calculator from the same browser session
  // can still attach their answers to the appointment they just booked.
  var POST_BOOKING_URL_LS_KEY = 'bxv_boutique_experience_url';
  var VISITOR_COOKIE_DAYS = 365;
  var WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = [
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December',
  ];

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------

  var config = {
    containerId: 'bellas-booking-widget',
    apiBaseUrl: '',
  };

  var state = {
    container: null,
    root: null,
    theme: null,
    copy: null,
    flow: null,
    visitorId: null,
    sessionId: null,
    eventId: null,
    startedAt: 0,
    interactionCount: 0,
    journey: [],
    step: 'loading', // loading | step1 | step2 | step3 | success | error
    selectedDate: null, // 'YYYY-MM-DD'
    selectedSlot: null, // { start, end, duration_minutes, remaining }
    selectedDuration: null,
    visibleMonth: null, // Date pinned to first of visible month, in shop tz
    availability: {},   // map 'YYYY-MM-DD' -> [slots]
    availabilityLoading: false,
    submitting: false,
    submitted: false,
    formData: {
      parent_first_name: '',
      parent_last_name: '',
      celebrant_first_name: '',
      event_date: '',
      party_size: '',
      phone: '',
      email: '',
      note: '',
      marketing_consent: false,
      company_website: '', // honeypot
    },
    // Server-side Boutique Experience profile id from the calculator-first
    // path. When set, included in the appointment submission so the lead
    // binds to the prep answers. Cleared on a successful booking.
    boutiqueExperienceProfileId: null,
    // True if `formData.note` was prefilled from the Fit Prep handoff,
    // so step 3 can render an in-widget notice instead of relying on
    // page-level glue. Switches to false the moment the customer edits
    // or removes the note.
    notePrefilled: false,
    confirmation: null,
    abandonSent: false,
  };

  // ---------------------------------------------------------------------
  // Identity + attribution
  // ---------------------------------------------------------------------

  function uuid() {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    // RFC4122 v4 fallback
    var d = Date.now();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (d + Math.random() * 16) % 16 | 0;
      d = Math.floor(d / 16);
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function readCookie(name) {
    var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function writeCookie(name, value, days) {
    var d = new Date();
    d.setTime(d.getTime() + days * 86400000);
    var parts = [
      name + '=' + encodeURIComponent(value),
      'expires=' + d.toUTCString(),
      'path=/',
      'SameSite=Lax',
    ];
    if (window.location.protocol === 'https:') parts.push('Secure');
    document.cookie = parts.join('; ');
  }

  function ensureVisitorId() {
    var existing = readCookie(VISITOR_COOKIE);
    if (existing) return existing;
    var id = uuid();
    writeCookie(VISITOR_COOKIE, id, VISITOR_COOKIE_DAYS);
    return id;
  }

  function readStoredSummary() {
    try { return window.localStorage.getItem(SUMMARY_LS_KEY) || ''; }
    catch (e) { return ''; }
  }

  function readStoredProfileId() {
    try {
      var raw = window.localStorage.getItem(PROFILE_ID_LS_KEY);
      var n = parseInt(raw, 10);
      return isFinite(n) && n > 0 ? n : null;
    } catch (e) { return null; }
  }

  function clearBoutiqueExperienceHandoff() {
    try { window.localStorage.removeItem(SUMMARY_LS_KEY); } catch (e) { /* ignore */ }
    try { window.localStorage.removeItem(PROFILE_ID_LS_KEY); } catch (e) { /* ignore */ }
  }

  function savePostBookingBoutiqueExperienceUrl(url) {
    if (!url) return;
    try { window.localStorage.setItem(POST_BOOKING_URL_LS_KEY, String(url)); }
    catch (e) { /* ignore */ }
  }

  function clearPostBookingBoutiqueExperienceUrl() {
    try { window.localStorage.removeItem(POST_BOOKING_URL_LS_KEY); }
    catch (e) { /* ignore */ }
  }

  var ATTR_KEYS = [
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'utm_id',
    'fbclid', 'gclid', 'msclkid',
  ];

  function captureAttribution() {
    var url = new URL(window.location.href);
    var fresh = {};
    ATTR_KEYS.forEach(function (k) {
      var v = url.searchParams.get(k);
      if (v) fresh[k] = v;
    });
    fresh.page_url = window.location.href;
    fresh.referrer_url = document.referrer || null;

    // Persist fresh values, don't clobber prior touches that aren't being
    // refreshed in this URL. localStorage carries the cross-session view.
    var stored = {};
    try {
      var raw = window.localStorage.getItem(ATTRIBUTION_LS_KEY);
      if (raw) stored = JSON.parse(raw);
    } catch (e) { /* ignore */ }

    var merged = Object.assign({}, stored, fresh);
    try {
      window.localStorage.setItem(ATTRIBUTION_LS_KEY, JSON.stringify(merged));
    } catch (e) { /* ignore */ }

    merged.fbp = readCookie('_fbp');
    merged.fbc = readCookie('_fbc');
    return merged;
  }

  function captureDevice() {
    var screenStr = '';
    try { screenStr = window.screen.width + 'x' + window.screen.height; } catch (e) { /* ignore */ }
    var viewportStr = '';
    try { viewportStr = window.innerWidth + 'x' + window.innerHeight; } catch (e) { /* ignore */ }
    var tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { /* ignore */ }
    var deviceType = window.innerWidth < 720 ? 'mobile' : 'desktop';
    return {
      device_type: deviceType,
      user_agent: navigator.userAgent,
      screen: screenStr,
      viewport: viewportStr,
      browser_language: navigator.language || '',
      platform: navigator.platform || '',
      browser_timezone: tz,
    };
  }

  function captureBehavior() {
    return {
      time_on_widget_ms: Date.now() - state.startedAt,
      interaction_count: state.interactionCount,
      steps_completed: stepsCompleted(),
      user_journey: state.journey.slice(),
    };
  }

  function stepsCompleted() {
    var n = 0;
    if (state.selectedSlot) n = 1;
    if (
      state.formData.parent_first_name &&
      state.formData.parent_last_name &&
      state.formData.celebrant_first_name &&
      state.formData.party_size
    ) n = 2;
    if (state.formData.phone && state.formData.email) n = 3;
    return n;
  }

  // ---------------------------------------------------------------------
  // API client + telemetry
  // ---------------------------------------------------------------------

  function apiUrl(path) {
    return (config.apiBaseUrl || '').replace(/\/+$/, '') + path;
  }

  function apiGet(path) {
    return window.fetch(apiUrl(path), { credentials: 'omit' }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function apiPost(path, body) {
    return window.fetch(apiUrl(path), {
      method: 'POST',
      credentials: 'omit',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok, status: r.status, data: data };
      }).catch(function () {
        return { ok: r.ok, status: r.status, data: null };
      });
    });
  }

  function track(eventName, payload) {
    state.journey.push({ at: Date.now() - state.startedAt, name: eventName });
    var body = {
      event_name: eventName,
      visitor_id: state.visitorId,
      session_id: state.sessionId,
      event_id: eventName === 'submit_succeeded' ? state.eventId : null,
      step: state.step,
      page_url: window.location.href,
      referrer_url: document.referrer || null,
      payload: payload || {},
    };
    // Fire and forget. Don't await; don't surface errors to user.
    try {
      apiPost('/api/booking/events', body).catch(function () { /* ignore */ });
    } catch (e) { /* ignore */ }
  }

  function sendAbandon() {
    if (state.abandonSent || state.submitted) return;
    state.abandonSent = true;
    var body = {
      event_id: 'abandon-' + state.eventId,
      visitor_id: state.visitorId,
      session_id: state.sessionId,
      step: state.step,
      page_url: window.location.href,
      referrer_url: document.referrer || null,
      partial: {
        selectedDate: state.selectedDate,
        selectedSlot: state.selectedSlot ? state.selectedSlot.start : null,
        parent_first_name: state.formData.parent_first_name || null,
        parent_last_name: state.formData.parent_last_name || null,
        celebrant_first_name: state.formData.celebrant_first_name || null,
        event_date: state.formData.event_date || null,
        party_size: state.formData.party_size || null,
        has_phone: !!state.formData.phone,
        has_email: !!state.formData.email,
      },
      attribution: captureAttribution(),
      device: captureDevice(),
      behavior: captureBehavior(),
    };
    var url = apiUrl('/api/booking/abandon');
    var blob = new Blob([JSON.stringify(body)], { type: 'application/json' });
    if (navigator.sendBeacon && navigator.sendBeacon(url, blob)) return;
    // Fallback if sendBeacon unavailable.
    try {
      window.fetch(url, {
        method: 'POST',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------
  // Date helpers (shop timezone is whatever the API tells us; rendering
  // uses the local date strings the API returns)
  // ---------------------------------------------------------------------

  function pad2(n) { return n < 10 ? '0' + n : '' + n; }

  function ymd(date) {
    return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
  }

  function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }

  function endOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth() + 1, 0);
  }

  function addDays(date, n) {
    var d = new Date(date);
    d.setDate(d.getDate() + n);
    return d;
  }

  function addMonths(date, n) {
    return new Date(date.getFullYear(), date.getMonth() + n, 1);
  }

  function formatTime(iso) {
    var d = new Date(iso);
    var hours = d.getHours();
    var mins = d.getMinutes();
    var ampm = hours >= 12 ? 'PM' : 'AM';
    hours = hours % 12 || 12;
    return hours + ':' + pad2(mins) + ' ' + ampm;
  }

  // ---------------------------------------------------------------------
  // Availability fetching
  // ---------------------------------------------------------------------

  function availableDurations() {
    // null = nothing loaded yet; treat all flow durations as enabled.
    var keys = Object.keys(state.availability);
    if (!keys.length) return null;
    var set = new Set();
    keys.forEach(function (date) {
      (state.availability[date] || []).forEach(function (slot) {
        set.add(slot.duration_minutes);
      });
    });
    return set;
  }

  function loadAvailability(monthDate) {
    state.availabilityLoading = true;
    var first = startOfMonth(monthDate);
    var last = endOfMonth(monthDate);
    // Pad ±7 days so adjacent months in the visible grid have data too.
    var from = ymd(addDays(first, -7));
    var to = ymd(addDays(last, 7));
    return apiGet('/api/booking/availability?from=' + from + '&to=' + to)
      .then(function (resp) {
        resp.days.forEach(function (day) {
          state.availability[day.date] = day.slots;
        });
        state.availabilityLoading = false;
        render();
      })
      .catch(function (err) {
        state.availabilityLoading = false;
        console.error('[bxv] availability fetch failed', err);
        render();
      });
  }

  // ---------------------------------------------------------------------
  // Theme + copy fetch
  // ---------------------------------------------------------------------

  function loadTheme() {
    return apiGet('/api/booking/theme').then(function (resp) {
      state.theme = resp.theme || {};
      state.copy = resp.copy_text || {};
      state.flow = resp.flow || {};
      var durations = (state.flow.duration_options_minutes || [45]);
      state.selectedDuration = state.flow.default_duration_minutes || durations[0] || 45;
    });
  }

  // ---------------------------------------------------------------------
  // Submission
  // ---------------------------------------------------------------------

  function submitBooking() {
    if (state.submitting) return;
    track('submit_attempted', {});
    state.submitting = true;
    render();

    var body = {
      slot_start: state.selectedSlot.start,
      slot_duration_minutes: state.selectedSlot.duration_minutes,
      parent_first_name: state.formData.parent_first_name.trim(),
      parent_last_name: state.formData.parent_last_name.trim(),
      celebrant_first_name: state.formData.celebrant_first_name.trim(),
      event_date: state.formData.event_date || null,
      party_size: state.formData.party_size,
      phone: state.formData.phone.trim(),
      email: state.formData.email.trim(),
      note: state.formData.note.trim() || null,
      marketing_consent: !!state.formData.marketing_consent,
      event_id: state.eventId,
      visitor_id: state.visitorId,
      session_id: state.sessionId,
      boutique_experience_profile_id: state.boutiqueExperienceProfileId,
      company_website: state.formData.company_website || '',
      attribution: captureAttribution(),
      device: captureDevice(),
      behavior: captureBehavior(),
    };

    apiPost('/api/booking/appointments', body).then(function (res) {
      state.submitting = false;
      if (res.ok && res.data && res.data.confirmation_code) {
        state.submitted = true;
        state.confirmation = res.data;
        state.step = 'success';
        track('submit_succeeded', { confirmation_code: res.data.confirmation_code });
        // The Boutique Experience handoff (note + profile id) has been
        // consumed. Clear both keys so a future visit doesn't resurface a
        // stale notice or attach the wrong profile id.
        clearBoutiqueExperienceHandoff();
        if (res.data.boutique_experience_attached) {
          clearPostBookingBoutiqueExperienceUrl();
        } else if (res.data.boutique_experience_url) {
          savePostBookingBoutiqueExperienceUrl(res.data.boutique_experience_url);
        } else {
          clearPostBookingBoutiqueExperienceUrl();
        }
      } else {
        state.step = 'step3';
        track('submit_failed', { status: res.status, detail: res.data && res.data.detail });
        renderSubmitError(res);
      }
      render();
    }).catch(function (err) {
      state.submitting = false;
      state.step = 'step3';
      track('submit_failed', { error: String(err) });
      renderSubmitError({ status: 0, data: { detail: 'Network error. Please try again.' } });
      render();
    });
  }

  function renderSubmitError(res) {
    var detail = (res && res.data && res.data.detail) || 'Something went wrong. Please try again.';
    var box = state.root.querySelector('.' + NS + '-submit-error');
    if (box) {
      box.textContent = String(detail);
      box.style.display = 'block';
    }
  }

  // ---------------------------------------------------------------------
  // Step transitions
  // ---------------------------------------------------------------------

  function goToStep(step) {
    state.step = step;
    track(step === 'step2' ? 'step_2_viewed' : step === 'step3' ? 'step_3_viewed' : step, {});
    render();
    // Scroll widget into view on small screens.
    try { state.container.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'className') node.className = attrs[k];
        else if (k === 'html') node.innerHTML = attrs[k];
        else if (k.indexOf('on') === 0) node.addEventListener(k.slice(2), attrs[k]);
        else if (k === 'style') node.style.cssText = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c == null || c === false) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  function injectStyles() {
    var t = state.theme || {};
    var css = `
.${NS}-root, .${NS}-root * {
  all: revert;
  box-sizing: border-box;
  font-family: ${t.font_body || 'Inter, system-ui, sans-serif'};
}
.${NS}-root {
  --bxv-bg: ${t.color_bg || '#FBF5EF'};
  --bxv-surface: ${t.color_surface || '#FFFFFF'};
  --bxv-accent: ${t.color_accent || '#A7616F'};
  --bxv-accent-dark: ${t.color_accent_dark || '#7E4451'};
  --bxv-text: ${t.color_text || '#2A1B1F'};
  --bxv-text-muted: ${t.color_text_muted || '#7A6A6F'};
  --bxv-radius: ${t.radius || '16px'};
  --bxv-radius-sm: 10px;
  --bxv-border: rgba(0,0,0,0.06);
  display: block;
  width: 100%;
  max-width: 920px;
  margin: 0 auto;
  background: var(--bxv-bg);
  border-radius: var(--bxv-radius);
  padding: 8px;
  color: var(--bxv-text);
  line-height: 1.4;
}
.${NS}-card {
  background: var(--bxv-surface);
  border-radius: var(--bxv-radius);
  padding: 24px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 32px;
}
@media (max-width: 720px) {
  .${NS}-card { grid-template-columns: 1fr; padding: 18px; gap: 20px; }
}
.${NS}-brand-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.${NS}-logo {
  display: block;
  max-width: 160px;
  max-height: 56px;
  width: auto;
  height: auto;
}
.${NS}-title {
  font-family: ${t.font_heading || 'Playfair Display, serif'};
  font-size: 30px; font-weight: 600; margin: 4px 0 12px 0;
}
.${NS}-subtitle { color: var(--bxv-text-muted); font-size: 15px; margin: 0 0 18px 0; }
.${NS}-meta { display: flex; flex-direction: column; gap: 6px; color: var(--bxv-text-muted); font-size: 14px; }
.${NS}-meta-row { display: flex; align-items: center; gap: 8px; }
.${NS}-meta-icon { width: 16px; height: 16px; opacity: 0.7; }
.${NS}-duration {
  background: rgba(167, 97, 111, 0.10);
  border-radius: 999px; padding: 4px;
  display: inline-flex; gap: 4px; margin: 14px 0 8px 0;
}
.${NS}-duration-btn {
  background: transparent; border: none; cursor: pointer;
  padding: 6px 14px; border-radius: 999px; font-size: 13px;
  color: var(--bxv-text-muted);
}
.${NS}-duration-btn[aria-pressed="true"] {
  background: var(--bxv-surface); color: var(--bxv-text);
  box-shadow: 0 1px 2px rgba(0,0,0,0.08);
}
.${NS}-duration-btn[disabled] {
  opacity: 0.35; cursor: not-allowed; text-decoration: line-through;
}
.${NS}-duration-btn[disabled]:hover { background: transparent; }
.${NS}-cal-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.${NS}-cal-title {
  font-family: ${t.font_heading || 'Playfair Display, serif'};
  font-size: 22px; font-weight: 600;
}
.${NS}-cal-title-year { color: var(--bxv-text-muted); margin-left: 6px; font-weight: 400; }
.${NS}-cal-nav { display: flex; gap: 4px; }
.${NS}-cal-nav-btn {
  background: transparent; border: 1px solid var(--bxv-border); cursor: pointer;
  width: 32px; height: 32px; border-radius: 8px; color: var(--bxv-text);
  display: flex; align-items: center; justify-content: center;
}
.${NS}-cal-nav-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.${NS}-cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
.${NS}-cal-dow {
  text-align: center; font-size: 11px; color: var(--bxv-text-muted);
  text-transform: capitalize; padding: 4px 0;
}
.${NS}-cal-day {
  background: transparent; border: none; aspect-ratio: 1; padding: 0;
  font-size: 14px; color: var(--bxv-text); border-radius: 10px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background-color 80ms ease;
}
.${NS}-cal-day[data-state="other"] { color: rgba(0,0,0,0.18); cursor: default; }
.${NS}-cal-day[data-state="closed"] { color: rgba(0,0,0,0.22); cursor: not-allowed; }
.${NS}-cal-day[data-state="open"] { background: rgba(167, 97, 111, 0.10); }
.${NS}-cal-day[data-state="open"]:hover { background: rgba(167, 97, 111, 0.22); }
.${NS}-cal-day[data-state="selected"] {
  background: var(--bxv-accent); color: #fff; font-weight: 600;
}
.${NS}-slots {
  margin-top: 16px; display: flex; flex-direction: column; gap: 8px;
  max-height: 260px; overflow-y: auto;
}
.${NS}-slot-btn {
  background: var(--bxv-surface); border: 1px solid var(--bxv-border);
  padding: 12px 16px; border-radius: var(--bxv-radius-sm); cursor: pointer;
  text-align: left; font-size: 15px; color: var(--bxv-text);
  display: flex; justify-content: space-between; align-items: center;
}
.${NS}-slot-btn:hover { border-color: var(--bxv-accent); }
.${NS}-slot-meta { font-size: 12px; color: var(--bxv-text-muted); }
.${NS}-empty { color: var(--bxv-text-muted); font-size: 14px; text-align: center; padding: 24px 0; }
.${NS}-form { display: flex; flex-direction: column; gap: 16px; max-width: 520px; }
.${NS}-field { display: flex; flex-direction: column; gap: 6px; }
.${NS}-label { font-size: 13px; color: var(--bxv-text-muted); }
.${NS}-field-group { display: flex; flex-direction: column; gap: 8px; }
.${NS}-group-heading { font-size: 15px; color: var(--bxv-text); font-weight: 600; }
.${NS}-hint { font-size: 12px; color: var(--bxv-text-muted); margin-top: -2px; }
.${NS}-name-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 380px) { .${NS}-name-row { grid-template-columns: 1fr; } }
.${NS}-input, .${NS}-textarea {
  background: var(--bxv-surface); border: 1px solid var(--bxv-border);
  border-radius: var(--bxv-radius-sm); padding: 12px 14px;
  font-size: 15px; color: var(--bxv-text); width: 100%;
}
.${NS}-input:focus, .${NS}-textarea:focus {
  outline: none; border-color: var(--bxv-accent);
}
.${NS}-textarea { min-height: 72px; resize: vertical; }
.${NS}-honeypot {
  position: absolute; left: -9999px; opacity: 0; pointer-events: none;
  width: 1px; height: 1px;
}
.${NS}-party-row { display: flex; gap: 8px; flex-wrap: wrap; }
.${NS}-party-btn {
  flex: 1; min-width: 100px; background: var(--bxv-surface);
  border: 1px solid var(--bxv-border); border-radius: var(--bxv-radius-sm);
  padding: 12px; cursor: pointer; font-size: 14px; color: var(--bxv-text);
}
.${NS}-party-btn[aria-pressed="true"] {
  border-color: var(--bxv-accent); background: rgba(167, 97, 111, 0.08);
  color: var(--bxv-accent-dark); font-weight: 600;
}
.${NS}-actions { display: flex; gap: 8px; justify-content: space-between; margin-top: 8px; }
.${NS}-cta {
  background: var(--bxv-accent); color: #fff; border: none; cursor: pointer;
  padding: 12px 20px; border-radius: var(--bxv-radius-sm); font-size: 15px; font-weight: 600;
}
.${NS}-cta:hover { background: var(--bxv-accent-dark); }
.${NS}-cta:disabled { opacity: 0.5; cursor: wait; }
.${NS}-back {
  background: transparent; color: var(--bxv-text-muted); border: none;
  cursor: pointer; padding: 12px 0; font-size: 14px;
}
.${NS}-back:hover { color: var(--bxv-text); }
.${NS}-progress { display: flex; gap: 6px; margin-bottom: 16px; }
.${NS}-progress-dot { height: 4px; flex: 1; background: rgba(0,0,0,0.06); border-radius: 2px; }
.${NS}-progress-dot[data-active="true"] { background: var(--bxv-accent); }
.${NS}-submit-error {
  display: none; color: #B83A3A; background: rgba(184, 58, 58, 0.08);
  padding: 10px 12px; border-radius: 8px; font-size: 14px; margin-top: 8px;
}
.${NS}-success {
  text-align: center; padding: 32px 24px; display: flex;
  flex-direction: column; gap: 12px; align-items: center;
}
.${NS}-success-icon {
  width: 56px; height: 56px; border-radius: 50%; background: var(--bxv-accent);
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 26px;
}
.${NS}-success-code {
  background: rgba(167, 97, 111, 0.10); padding: 8px 14px; border-radius: 999px;
  font-family: ui-monospace, monospace; font-size: 13px; color: var(--bxv-accent-dark);
}
.${NS}-success-slot {
  font-family: ${t.font_heading || 'Playfair Display, serif'};
  font-size: 22px; margin: 4px 0;
}
.${NS}-success-meta { color: var(--bxv-text-muted); font-size: 14px; }
.${NS}-success-be {
  margin-top: 18px; padding: 16px 18px; border-radius: var(--bxv-radius-sm);
  background: rgba(167, 97, 111, 0.06); border: 1px solid var(--bxv-border);
  text-align: left; width: 100%; max-width: 380px;
}
.${NS}-success-be.be-attached {
  background: rgba(80, 130, 90, 0.08);
  border-color: rgba(80, 130, 90, 0.25);
}
.${NS}-success-be-title {
  font-weight: 600; color: var(--bxv-text);
  margin-bottom: 4px; font-size: 14px;
}
.${NS}-success-be-body {
  color: var(--bxv-text-muted); font-size: 13px; line-height: 1.5;
}
.${NS}-success-be-link {
  display: inline-block; margin-top: 10px; color: var(--bxv-accent-dark);
  font-weight: 600; text-decoration: none; font-size: 14px;
}
.${NS}-success-be-link:hover { text-decoration: underline; }
.${NS}-prefill-notice {
  background: rgba(167, 97, 111, 0.08); color: var(--bxv-accent-dark);
  padding: 10px 12px; border-radius: var(--bxv-radius-sm);
  font-size: 13px; margin-bottom: 12px;
  display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap;
}
.${NS}-prefill-notice-text { flex: 1; min-width: 200px; line-height: 1.4; }
.${NS}-prefill-notice-remove {
  background: transparent; border: none; cursor: pointer;
  color: var(--bxv-accent-dark); font-weight: 600; font-size: 13px;
  text-decoration: underline; padding: 0;
}
.${NS}-loading { padding: 60px 0; text-align: center; color: var(--bxv-text-muted); }
`;
    var style = document.getElementById(NS + '-styles');
    if (style) style.parentNode.removeChild(style);
    style = document.createElement('style');
    style.id = NS + '-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function render() {
    if (!state.root) return;
    state.root.innerHTML = '';

    if (state.step === 'loading') {
      state.root.appendChild(el('div', { className: NS + '-loading' }, ['Loading…']));
      return;
    }
    if (state.step === 'error') {
      state.root.appendChild(el('div', { className: NS + '-loading' }, [
        "We couldn't load the booking widget. Please refresh, or call (210) 670-5845.",
      ]));
      return;
    }
    if (state.step === 'success') {
      state.root.appendChild(renderSuccess());
      return;
    }

    var card = el('div', { className: NS + '-card' });
    if (state.step === 'step1') {
      card.appendChild(renderLeftPane());
      card.appendChild(renderCalendar());
    } else {
      card.appendChild(renderLeftPane(true));
      card.appendChild(state.step === 'step2' ? renderStep2() : renderStep3());
    }
    state.root.appendChild(card);
  }

  function renderLeftPane(compact) {
    var pane = el('div', {});
    var logoUrl = state.theme.logo_url ||
      ((config.apiBaseUrl || '').replace(/\/+$/, '') + '/widgets/bellas-logo.svg');
    pane.appendChild(el('div', { className: NS + '-brand-row' }, [
      el('img', {
        className: NS + '-logo',
        src: logoUrl,
        alt: state.copy.header_brand || "Bella's XV",
      }),
    ]));
    pane.appendChild(el('h2', { className: NS + '-title' }, [
      state.copy.header_title || 'Initial consultation',
    ]));
    pane.appendChild(el('p', { className: NS + '-subtitle' }, [
      state.copy.header_subtitle || '',
    ]));

    if (!compact) {
      var durations = (state.flow.duration_options_minutes || [45]);
      var available = availableDurations();
      var dur = el('div', { className: NS + '-duration', role: 'group', 'aria-label': 'Appointment length' });
      durations.forEach(function (m) {
        // Before availability loads, every option is shown enabled.
        // Once loaded, options the shop currently has no slots for are
        // visibly disabled rather than silently producing an empty calendar.
        var isAvailable = available === null || available.has(m);
        var attrs = {
          className: NS + '-duration-btn',
          type: 'button',
          'aria-pressed': state.selectedDuration === m ? 'true' : 'false',
        };
        if (!isAvailable) {
          attrs.disabled = 'disabled';
          attrs.title = 'Not currently offered';
        } else {
          attrs.onclick = (function (mins) {
            return function () {
              state.selectedDuration = mins;
              if (state.selectedSlot && state.selectedSlot.duration_minutes !== mins) {
                state.selectedSlot = null;
              }
              state.interactionCount++;
              render();
            };
          })(m);
        }
        dur.appendChild(el('button', attrs, [m < 60 ? m + 'm' : (m / 60) + 'h']));
      });
      pane.appendChild(dur);
    }

    pane.appendChild(el('div', { className: NS + '-meta' }, [
      el('div', { className: NS + '-meta-row' }, [
        el('span', { html: '\u{1F4CD}' }),
        document.createTextNode(state.copy.boutique_label || "Bella's XV boutique"),
      ]),
    ]));

    if (compact && state.selectedSlot) {
      pane.appendChild(el('div', { className: NS + '-success-meta', style: 'margin-top:16px' }, [
        formatSlotLabel(state.selectedSlot),
      ]));
    }
    return pane;
  }

  function formatSlotLabel(slot) {
    var d = new Date(slot.start);
    return WEEKDAYS[d.getDay()] + ', ' + MONTHS[d.getMonth()] + ' ' + d.getDate() +
      ' · ' + formatTime(slot.start);
  }

  // ----------------------------------------------------------------
  // Calendar pane
  // ----------------------------------------------------------------

  function renderCalendar() {
    var pane = el('div', {});
    var month = state.visibleMonth;
    pane.appendChild(renderCalHeader(month));
    pane.appendChild(renderCalGrid(month));
    if (state.selectedDate) {
      pane.appendChild(renderSlots(state.selectedDate));
    }
    return pane;
  }

  function renderCalHeader(month) {
    var prevAllowed = month > startOfMonth(new Date());
    var maxAhead = state.flow.max_days_ahead || 60;
    var maxMonth = startOfMonth(addDays(new Date(), maxAhead));
    var nextAllowed = month < maxMonth;

    var header = el('div', { className: NS + '-cal-header' });
    header.appendChild(el('div', { className: NS + '-cal-title' }, [
      MONTHS[month.getMonth()],
      el('span', { className: NS + '-cal-title-year' }, [' ' + month.getFullYear()]),
    ]));
    var nav = el('div', { className: NS + '-cal-nav' });
    var prevAttrs = {
      className: NS + '-cal-nav-btn',
      type: 'button',
      'aria-label': 'Previous month',
      onclick: function () {
        if (!prevAllowed) return;
        state.visibleMonth = addMonths(state.visibleMonth, -1);
        loadAvailability(state.visibleMonth);
        render();
      },
    };
    if (!prevAllowed) prevAttrs.disabled = 'disabled';
    nav.appendChild(el('button', prevAttrs, ['‹']));
    var nextAttrs = {
      className: NS + '-cal-nav-btn',
      type: 'button',
      'aria-label': 'Next month',
      onclick: function () {
        if (!nextAllowed) return;
        state.visibleMonth = addMonths(state.visibleMonth, 1);
        loadAvailability(state.visibleMonth);
        render();
      },
    };
    if (!nextAllowed) nextAttrs.disabled = 'disabled';
    nav.appendChild(el('button', nextAttrs, ['›']));
    header.appendChild(nav);
    return header;
  }

  function renderCalGrid(month) {
    var grid = el('div', { className: NS + '-cal-grid' });
    WEEKDAYS.forEach(function (w) {
      grid.appendChild(el('div', { className: NS + '-cal-dow' }, [w]));
    });
    var first = startOfMonth(month);
    var last = endOfMonth(month);
    // Pad the grid to start on Sunday.
    var leading = first.getDay();
    var totalCells = Math.ceil((leading + last.getDate()) / 7) * 7;
    for (var i = 0; i < totalCells; i++) {
      var dayDate = addDays(first, i - leading);
      var isOther = dayDate.getMonth() !== month.getMonth();
      var dateKey = ymd(dayDate);
      var slots = state.availability[dateKey] || [];
      var compatibleSlots = slots.filter(function (s) {
        return s.duration_minutes === state.selectedDuration;
      });
      var isClosed = compatibleSlots.length === 0;
      var dayState = isOther ? 'other' : (isClosed ? 'closed' : 'open');
      if (!isOther && state.selectedDate === dateKey) dayState = 'selected';

      var attrs = { className: NS + '-cal-day', 'data-state': dayState, type: 'button' };
      if (dayState === 'other' || dayState === 'closed') {
        attrs.disabled = 'disabled';
      } else {
        (function (key) {
          attrs.onclick = function () {
            state.selectedDate = key;
            state.selectedSlot = null;
            state.interactionCount++;
            track('date_selected', { date: key });
            render();
          };
        })(dateKey);
      }
      grid.appendChild(el('button', attrs, [String(dayDate.getDate())]));
    }
    return grid;
  }

  function renderSlots(dateKey) {
    var slots = (state.availability[dateKey] || []).filter(function (s) {
      return s.duration_minutes === state.selectedDuration;
    });
    if (!slots.length) {
      return el('div', { className: NS + '-empty' }, ['No times available for this day.']);
    }
    var wrap = el('div', { className: NS + '-slots' });
    slots.forEach(function (slot) {
      var btn = el('button', {
        className: NS + '-slot-btn',
        type: 'button',
        onclick: function () {
          state.selectedSlot = slot;
          state.interactionCount++;
          track('slot_selected', { start: slot.start });
          goToStep('step2');
        },
      }, []);
      btn.appendChild(el('span', {}, [formatTime(slot.start)]));
      btn.appendChild(el('span', { className: NS + '-slot-meta' }, [slot.duration_minutes + ' min']));
      wrap.appendChild(btn);
    });
    return wrap;
  }

  // ----------------------------------------------------------------
  // Step 2 — who is this for
  // ----------------------------------------------------------------

  function renderStep2() {
    var form = el('form', {
      className: NS + '-form',
      onsubmit: function (ev) {
        ev.preventDefault();
        if (!validateStep2()) return;
        track('step_2_submitted', {});
        goToStep('step3');
      },
    });

    form.appendChild(progressBar(2));
    form.appendChild(el('h3', {
      className: NS + '-title',
      style: 'font-size:22px;margin:0 0 8px 0',
    }, [state.copy.step2_heading || "Tell us who's coming"]));

    var nameRow = el('div', { className: NS + '-name-row' });
    nameRow.appendChild(field(
      state.copy.step2_parent_first_name_label || 'First name',
      input('text', 'parent_first_name', { required: 'required', autocomplete: 'given-name' })
    ));
    nameRow.appendChild(field(
      state.copy.step2_parent_last_name_label || 'Last name',
      input('text', 'parent_last_name', { required: 'required', autocomplete: 'family-name' })
    ));
    form.appendChild(fieldGroup(
      state.copy.step2_parent_heading || 'Your name',
      state.copy.step2_parent_hint || 'Who should we contact about the appointment?',
      nameRow
    ));

    form.appendChild(fieldGroup(
      state.copy.step2_celebrant_heading || "Quinceañera's first name",
      state.copy.step2_celebrant_hint || "So we know who we're celebrating.",
      input('text', 'celebrant_first_name', { required: 'required', autocomplete: 'given-name' })
    ));

    form.appendChild(field(
      state.copy.step2_event_date_label || 'Event date (if known)',
      input('date', 'event_date', { autocomplete: 'off' })
    ));

    var party = el('div', { className: NS + '-party-row', role: 'group' });
    var options = [
      { value: 'pair', label: state.copy.step2_party_pair || 'Me and my quinceañera' },
      { value: '3_4', label: state.copy.step2_party_3_4 || '3-4 of us' },
      { value: '5_plus', label: state.copy.step2_party_5_plus || '5 or more' },
    ];
    options.forEach(function (opt) {
      party.appendChild(el('button', {
        className: NS + '-party-btn',
        type: 'button',
        'aria-pressed': state.formData.party_size === opt.value ? 'true' : 'false',
        onclick: function () {
          state.formData.party_size = opt.value;
          state.interactionCount++;
          render();
        },
      }, [opt.label]));
    });
    form.appendChild(field(
      state.copy.step2_party_size_label || "Who's coming to the appointment?",
      party
    ));

    // Honeypot
    form.appendChild(el('input', {
      className: NS + '-honeypot',
      type: 'text',
      name: 'company_website',
      tabindex: '-1',
      autocomplete: 'off',
      'aria-hidden': 'true',
      oninput: function (ev) { state.formData.company_website = ev.target.value; },
    }));

    var actions = el('div', { className: NS + '-actions' });
    actions.appendChild(el('button', {
      className: NS + '-back',
      type: 'button',
      onclick: function () { goToStep('step1'); },
    }, ['‹ Change time']));
    actions.appendChild(el('button', { className: NS + '-cta', type: 'submit' }, ['Continue']));
    form.appendChild(actions);
    return form;
  }

  function validateStep2() {
    if (!state.formData.parent_first_name.trim() || !state.formData.parent_last_name.trim()) {
      alert('Please enter your first and last name.');
      return false;
    }
    if (!state.formData.celebrant_first_name.trim()) {
      alert("Please enter the quinceañera's first name.");
      return false;
    }
    if (!state.formData.party_size) {
      alert("Please pick who's coming to the appointment.");
      return false;
    }
    return true;
  }

  // ----------------------------------------------------------------
  // Step 3 — contact
  // ----------------------------------------------------------------

  function renderStep3() {
    var form = el('form', {
      className: NS + '-form',
      onsubmit: function (ev) {
        ev.preventDefault();
        if (!validateStep3()) return;
        submitBooking();
      },
    });

    form.appendChild(progressBar(3));
    form.appendChild(el('h3', {
      className: NS + '-title',
      style: 'font-size:22px;margin:0 0 8px 0',
    }, [state.copy.step3_heading || 'How do we reach you?']));

    form.appendChild(field(
      state.copy.step3_phone_label || 'Phone number',
      input('tel', 'phone', { required: 'required', autocomplete: 'tel', placeholder: '(210) 555-0142' })
    ));

    form.appendChild(field(
      state.copy.step3_email_label || 'Email',
      input('email', 'email', { required: 'required', autocomplete: 'email', placeholder: 'you@example.com' })
    ));

    if (state.notePrefilled && state.formData.note) {
      form.appendChild(renderPrefillNotice());
    }

    form.appendChild(field(
      state.copy.step3_note_label || "Anything you'd like us to know? (optional)",
      textarea('note')
    ));

    var consentLabel = el('label', { className: NS + '-consent' });
    consentLabel.appendChild(el('input', {
      type: 'checkbox',
      name: 'marketing_consent',
      checked: state.formData.marketing_consent ? 'checked' : null,
      onchange: function (ev) {
        state.formData.marketing_consent = !!ev.target.checked;
        state.interactionCount++;
      },
    }));
    consentLabel.appendChild(el('span', {}, [
      state.copy.marketing_consent_label ||
        'Send me promotions and event updates',
    ]));
    form.appendChild(consentLabel);

    var errBox = el('div', { className: NS + '-submit-error' });
    form.appendChild(errBox);

    var actions = el('div', { className: NS + '-actions' });
    actions.appendChild(el('button', {
      className: NS + '-back',
      type: 'button',
      onclick: function () { goToStep('step2'); },
    }, ['‹ Back']));
    var submitAttrs = { className: NS + '-cta', type: 'submit' };
    if (state.submitting) submitAttrs.disabled = 'disabled';
    actions.appendChild(el('button', submitAttrs, [
      state.submitting ? 'Booking…' : (state.copy.submit_label || 'Confirm appointment'),
    ]));
    form.appendChild(actions);
    return form;
  }

  function renderPrefillNotice() {
    var box = el('div', { className: NS + '-prefill-notice' });
    box.appendChild(el('span', { className: NS + '-prefill-notice-text' }, [
      'Your Boutique Experience answers were added as the appointment note. You can edit or remove it before submitting.',
    ]));
    box.appendChild(el('button', {
      className: NS + '-prefill-notice-remove',
      type: 'button',
      onclick: function () {
        state.formData.note = '';
        state.notePrefilled = false;
        try { window.localStorage.removeItem(SUMMARY_LS_KEY); } catch (e) { /* ignore */ }
        render();
      },
    }, ['Remove']));
    return box;
  }

  function validateStep3() {
    if (!state.formData.phone.trim()) { alert('Please enter your phone number.'); return false; }
    if (!state.formData.email.trim()) { alert('Please enter your email address.'); return false; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(state.formData.email)) {
      alert('Please enter a valid email address.'); return false;
    }
    return true;
  }

  // ----------------------------------------------------------------
  // Reusable form helpers
  // ----------------------------------------------------------------

  function field(labelText, control) {
    var wrap = el('label', { className: NS + '-field' });
    wrap.appendChild(el('span', { className: NS + '-label' }, [labelText]));
    wrap.appendChild(control);
    return wrap;
  }

  function fieldGroup(headingText, hintText, control) {
    var wrap = el('div', { className: NS + '-field-group' });
    wrap.appendChild(el('div', { className: NS + '-group-heading' }, [headingText]));
    if (hintText) {
      wrap.appendChild(el('div', { className: NS + '-hint' }, [hintText]));
    }
    wrap.appendChild(control);
    return wrap;
  }

  function input(type, name, extra) {
    var attrs = Object.assign({
      className: NS + '-input',
      type: type,
      name: name,
      value: state.formData[name] || '',
      oninput: function (ev) {
        state.formData[name] = ev.target.value;
        state.interactionCount++;
      },
    }, extra || {});
    return el('input', attrs);
  }

  function textarea(name) {
    return el('textarea', {
      className: NS + '-textarea',
      name: name,
      rows: '3',
      oninput: function (ev) {
        state.formData[name] = ev.target.value;
        state.interactionCount++;
      },
    }, [state.formData[name] || '']);
  }

  function progressBar(active) {
    var wrap = el('div', { className: NS + '-progress' });
    [1, 2, 3].forEach(function (i) {
      wrap.appendChild(el('div', {
        className: NS + '-progress-dot',
        'data-active': i <= active ? 'true' : 'false',
      }));
    });
    return wrap;
  }

  // ----------------------------------------------------------------
  // Success
  // ----------------------------------------------------------------

  function renderSuccess() {
    var c = state.confirmation || {};
    var slot = c.slot_start ? new Date(c.slot_start) : null;
    var wrap = el('div', { className: NS + '-success' });
    wrap.appendChild(el('div', { className: NS + '-success-icon' }, ['✓']));
    wrap.appendChild(el('h3', { className: NS + '-title', style: 'font-size:24px;margin:0' }, [
      state.copy.success_heading || "You're booked.",
    ]));
    if (slot) {
      wrap.appendChild(el('div', { className: NS + '-success-slot' }, [
        WEEKDAYS[slot.getDay()] + ', ' + MONTHS[slot.getMonth()] + ' ' + slot.getDate() +
          ' · ' + formatTime(c.slot_start),
      ]));
    }
    wrap.appendChild(el('div', { className: NS + '-success-meta' }, [
      state.copy.success_subtitle || "We just emailed your confirmation. We can't wait to meet you.",
    ]));
    if (c.confirmation_code) {
      wrap.appendChild(el('div', { className: NS + '-success-code' }, [
        'Confirmation: ' + c.confirmation_code,
      ]));
    }
    wrap.appendChild(renderBoutiqueExperienceCallout(c));
    return wrap;
  }

  function renderBoutiqueExperienceCallout(c) {
    // Already linked: short acknowledgement so the customer is not asked
    // to fill in answers a second time.
    if (c.boutique_experience_attached) {
      return el('div', { className: NS + '-success-be be-attached' }, [
        el('div', { className: NS + '-success-be-title' }, [
          'Boutique Experience profile added',
        ]),
        el('div', { className: NS + '-success-be-body' }, [
          'Your stylist will see your size estimate and style notes before you arrive.',
        ]),
      ]);
    }
    // Not linked yet: invite the customer to complete the profile via the
    // tokenized URL the API returned.
    if (c.boutique_experience_url) {
      return el('div', { className: NS + '-success-be be-cta' }, [
        el('div', { className: NS + '-success-be-title' }, [
          'One last optional step',
        ]),
        el('div', { className: NS + '-success-be-body' }, [
          'Tell us your size and style so your stylist can pull dresses for you before you arrive.',
        ]),
        el('a', {
          className: NS + '-success-be-link',
          href: c.boutique_experience_url,
        }, ['Complete your Boutique Experience Profile →']),
      ]);
    }
    // Older API not returning the URL: render nothing so the success
    // screen looks identical to the pre-Phase-5 release.
    return document.createDocumentFragment();
  }

  // ---------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------

  function bindAbandonHandlers() {
    function fire() { sendAbandon(); }
    window.addEventListener('pagehide', fire);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') fire();
    });
  }

  function init(opts) {
    config = Object.assign({}, config, opts || {});
    state.container = document.getElementById(config.containerId);
    if (!state.container) {
      console.warn('[bxv] container not found:', config.containerId);
      return;
    }
    state.container.innerHTML = '';
    state.root = document.createElement('div');
    state.root.className = NS + '-root';
    state.container.appendChild(state.root);

    state.startedAt = Date.now();
    state.visitorId = ensureVisitorId();
    state.sessionId = uuid();
    state.eventId = uuid();
    state.visibleMonth = startOfMonth(new Date());
    captureAttribution(); // primes localStorage even on bounces

    state.step = 'loading';

    // Boutique Experience handoff. The fit-prep widget writes these
    // localStorage keys when the customer finishes their profile before
    // booking. Config wins: only `undefined` (key absent) falls back to
    // localStorage. Any explicit value (including `null` or `""`)
    // suppresses the localStorage handoff so an embed can opt out
    // without race-conditioning on storage state.
    if (config.prefillNote === undefined) {
      var stored = readStoredSummary();
      if (stored) {
        state.formData.note = stored;
        state.notePrefilled = true;
      }
    } else if (typeof config.prefillNote === 'string' && config.prefillNote) {
      state.formData.note = config.prefillNote;
      state.notePrefilled = true;
    }
    // else: prefillNote is null/""/non-string -> leave note empty, no notice.

    if (config.boutiqueExperienceProfileId === undefined) {
      state.boutiqueExperienceProfileId = readStoredProfileId();
    } else {
      var n = parseInt(config.boutiqueExperienceProfileId, 10);
      state.boutiqueExperienceProfileId = (isFinite(n) && n > 0) ? n : null;
    }

    loadTheme().then(function () {
      injectStyles();
      track('widget_loaded', {});
      state.step = 'step1';
      render();
      return loadAvailability(state.visibleMonth);
    }).catch(function (err) {
      console.error('[bxv] init failed', err);
      state.step = 'error';
      render();
    });

    bindAbandonHandlers();
  }

  function destroy() {
    if (state.container) state.container.innerHTML = '';
    var style = document.getElementById(NS + '-styles');
    if (style) style.parentNode.removeChild(style);
  }

  // Public note setter so the Fit Prep Tool can drop its summary into the
  // booking note field after init (e.g. when both widgets are on the same
  // page and the user clicks "Save it for my booking"). Re-renders so the
  // textarea reflects the new value if step 3 is currently visible.
  function setNote(text) {
    var next = (typeof text === 'string') ? text : '';
    state.formData.note = next;
    state.notePrefilled = !!next;
    if (state.root && state.step !== 'loading' && state.step !== 'error') render();
  }

  // Public setter so the Fit Prep Tool can hand off the server-side
  // profile id after the customer completes their pre-booking profile.
  // Phase 5: the booking submission picks this up and sends it to the API.
  // Pass null/undefined to clear (e.g. on error fallback in the fit-prep
  // widget so a stale id doesn't ride along with the new note).
  function setBoutiqueExperienceProfileId(id) {
    var n = parseInt(id, 10);
    state.boutiqueExperienceProfileId = (isFinite(n) && n > 0) ? n : null;
  }

  window.BellasBookingWidget = {
    _initialized: true,
    init: init,
    destroy: destroy,
    setNote: setNote,
    setBoutiqueExperienceProfileId: setBoutiqueExperienceProfileId,
  };
})(window, document);
