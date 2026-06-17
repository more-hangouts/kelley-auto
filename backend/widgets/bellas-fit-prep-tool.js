/*!
 * Bellas XV Fit Prep Tool — embeddable IIFE
 * Optional sizing/prep guide. Standalone from the booking widget.
 * No build step, no dependencies. Self-contained.
 */
(function (window, document) {
  'use strict';

  if (window.BellasFitPrepTool && window.BellasFitPrepTool._initialized) {
    return;
  }

  // ---------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------

  var NS = 'bxvfp';

  // localStorage handoff keys. The booking widget on index.html and
  // fit-prep.html reads them at init time:
  //   - SUMMARY_LS_KEY: legacy textual summary, dropped into the
  //     appointment-note field via BellasBookingWidget.setNote(). Kept as
  //     a fallback for browsers/sessions where the server-profile POST
  //     fails so customers still get a partial handoff.
  //   - PROFILE_ID_LS_KEY: server-side Boutique Experience profile id.
  //     Phase 5 will have the booking widget include it in the booking
  //     submission so the appointment binds to the prep answers.
  var SUMMARY_LS_KEY = 'bxv_fit_prep_summary';
  var PROFILE_ID_LS_KEY = 'bxv_boutique_profile_id';
  // Written by the booking widget after a successful booking. Lets the
  // standalone calculator attach answers to the appointment just booked when
  // the customer opens fit-prep.html without the tokenized success link.
  var POST_BOOKING_URL_LS_KEY = 'bxv_boutique_experience_url';
  var SUMMARY_MAX_CHARS = 1000; // matches services/booking_contracts.py max_length

  // Cookie shared with the booking widget so the same visitor record links
  // their pre-booking profile to the booking that follows.
  var VISITOR_COOKIE = 'bxv_vid';

  // ---------------------------------------------------------------------
  // Size chart — Bella's XV internal reference chart
  // ---------------------------------------------------------------------
  // This is Bella's XV's own working reference for prep estimates, not a
  // designer chart. Staff will confirm vendor-specific sizing in store
  // during the appointment, so the customer-facing output is a starting
  // point only.
  //
  // Customer-facing copy must say "Bella's XV reference formalwear chart"
  // and never name a designer.
  //
  // Reference points consulted while building this (not surfaced to users):
  //   House of Wu:  size 0 = 32/24/35.5,   size 18 = 43/35/46.5  (extends to 30)
  //   Morilee:      size 18 = 42.5/34/46
  //   PromGirl:     size 18 = 44.5/37.5/48
  // None of these matched the boutique's stated endpoints exactly, which
  // is expected — the boutique sees fittings across multiple vendors and
  // its working reference reflects that mix.
  //
  // Rows 0-18 interpolate between the boutique-stated endpoints; rows
  // 20-30 extrapolate using the same per-step deltas (~+2.5"/+2.5"/+2.5")
  // for inclusivity. To revise the chart, edit these rows directly — no
  // public launch process needed.
  // ---------------------------------------------------------------------
  var CHART_SOURCE = "Bella's XV reference formalwear chart";

  var SIZE_CHART = [
    { size: 0,  bust: 32.0, waist: 23.5, hips: 35.5 },
    { size: 2,  bust: 33.0, waist: 24.5, hips: 36.5 },
    { size: 4,  bust: 34.0, waist: 25.5, hips: 37.5 },
    { size: 6,  bust: 35.0, waist: 26.5, hips: 38.5 },
    { size: 8,  bust: 36.0, waist: 27.5, hips: 39.5 },
    { size: 10, bust: 37.0, waist: 28.5, hips: 40.5 },
    { size: 12, bust: 38.5, waist: 30.0, hips: 42.0 },
    { size: 14, bust: 40.0, waist: 31.5, hips: 43.5 },
    { size: 16, bust: 42.0, waist: 33.5, hips: 45.5 },
    { size: 18, bust: 44.5, waist: 36.0, hips: 48.0 },
    // Extended sizes — extrapolated from the per-step deltas above:
    { size: 20, bust: 47.0, waist: 38.5, hips: 50.5 },
    { size: 22, bust: 49.5, waist: 41.0, hips: 53.0 },
    { size: 24, bust: 52.0, waist: 43.5, hips: 55.5 },
    { size: 26, bust: 54.5, waist: 46.0, hips: 58.0 },
    { size: 28, bust: 57.0, waist: 48.5, hips: 60.5 },
    { size: 30, bust: 59.5, waist: 51.0, hips: 63.0 },
  ];

  // PLACEHOLDER style/budget options — confirm with merchant.
  var STYLE_OPTIONS = [
    { value: 'ball_gown',  label: 'Ball gown' },
    { value: 'a_line',     label: 'A-line' },
    { value: 'mermaid',    label: 'Mermaid / fitted' },
    { value: 'two_piece',  label: 'Two-piece' },
    { value: 'unsure',     label: 'Not sure yet' },
  ];

  var BACK_OPTIONS = [
    { value: 'corset', label: 'Corset' },
    { value: 'zipper', label: 'Zipper' },
    { value: 'unsure', label: 'Not sure' },
  ];

  var BUDGET_OPTIONS = [
    { value: 'under_1000',  label: 'Under $1,000' },
    { value: '1000_1500',   label: '$1,000-$1,500' },
    { value: '1500_2000',   label: '$1,500-$2,000' },
    { value: '2000_plus',   label: '$2,000+' },
    { value: 'unsure',      label: 'Not sure yet' },
  ];

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------

  var config = {
    containerId: 'bellas-fit-prep-tool',
    apiBaseUrl: '',
  };

  var state = {
    container: null,
    root: null,
    theme: null,
    step: 'step1', // step1 | step2 | result
    data: {
      bust: '',
      waist: '',
      hips: '',
      height_ft: '',
      height_in: '',
      event_date: '',
      style: '',
      back: '',
      budget: '',
      colors: '',
      likes: '',
      avoids: '',
    },
    sizing: null,
    // Token from the post-booking email link or same-browser booking handoff.
    // When set, the result screen shows a single "Send to my stylist" CTA
    // that POSTs to the token endpoint. When null, the result screen shows
    // "Book with this profile" and creates an unlinked profile + handoff to
    // the booking widget.
    token: null,
    handoff: freshHandoff(),
    bookingLookup: {
      confirmationCode: '',
      email: '',
    },
    sessionId: 'bxvfp-' + Math.random().toString(36).slice(2, 10),
  };

  // Single source of truth for the handoff state machine. Reset whenever
  // the customer enters or leaves the result step so a previous "sent"
  // doesn't carry over to a re-submission with edited answers.
  function freshHandoff() {
    return {
      status: 'idle',     // idle | submitting | sent | error
      profileId: null,    // server profile id once created
      sentMode: null,     // pre_booking | attached
      sentSlotLabel: null,// e.g. "Friday, May 2 at 3:00 PM" after token submit
      sentConfirmationCode: null,
      errorMsg: null,     // human-readable error string
    };
  }

  function readTokenFromUrl() {
    try {
      var params = new URLSearchParams(window.location.search);
      var raw = params.get('token');
      return raw ? raw.trim() : null;
    } catch (e) { return null; }
  }

  function readStoredPostBookingToken() {
    try {
      var rawUrl = window.localStorage.getItem(POST_BOOKING_URL_LS_KEY);
      if (!rawUrl) return null;
      var url = new URL(rawUrl, window.location.href);
      var raw = url.searchParams.get('token');
      return raw ? raw.trim() : null;
    } catch (e) { return null; }
  }

  function readVisitorCookie() {
    try {
      var match = document.cookie.match(
        new RegExp('(?:^|; )' + VISITOR_COOKIE + '=([^;]*)')
      );
      return match ? decodeURIComponent(match[1]) : null;
    } catch (e) { return null; }
  }

  // ---------------------------------------------------------------------
  // API + theme
  // ---------------------------------------------------------------------

  function apiUrl(path) {
    return (config.apiBaseUrl || '').replace(/\/+$/, '') + path;
  }

  function loadTheme() {
    return window.fetch(apiUrl('/api/booking/theme'), { credentials: 'omit' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (resp) {
        state.theme = (resp && resp.theme) || {};
      })
      .catch(function () { state.theme = {}; });
  }

  // ---------------------------------------------------------------------
  // Sizing math
  // ---------------------------------------------------------------------

  function smallestSizeFor(measurement, key) {
    if (!isFinite(measurement)) return null;
    for (var i = 0; i < SIZE_CHART.length; i++) {
      if (SIZE_CHART[i][key] >= measurement) return SIZE_CHART[i].size;
    }
    return SIZE_CHART[SIZE_CHART.length - 1].size; // off-chart, use largest
  }

  function offChart(measurement, key) {
    return measurement > SIZE_CHART[SIZE_CHART.length - 1][key];
  }

  // Silhouettes where hips usually have generous ease in the gown's skirt.
  // House of Wu's own measuring guide notes A-line hips can exceed the chart
  // without forcing a larger bodice size; ABC Fashion recommends choosing
  // primarily by bust + waist for full-skirt silhouettes. So for these
  // styles we estimate from bust + waist and surface hips separately as
  // context, instead of letting a high hip number drive the whole estimate.
  var FULL_SKIRT_STYLES = { ball_gown: true, a_line: true, two_piece: true };

  function computeSizing(data) {
    var bust = parseFloat(data.bust);
    var waist = parseFloat(data.waist);
    var hips = parseFloat(data.hips);
    if (!isFinite(bust) || !isFinite(waist) || !isFinite(hips)) return null;

    var byMeasure = {
      bust:  smallestSizeFor(bust,  'bust'),
      waist: smallestSizeFor(waist, 'waist'),
      hips:  smallestSizeFor(hips,  'hips'),
    };

    var fullSkirt = !!FULL_SKIRT_STYLES[data.style];
    var primary = fullSkirt
      ? [byMeasure.bust, byMeasure.waist]
      : [byMeasure.bust, byMeasure.waist, byMeasure.hips];

    var primaryMax = Math.max.apply(null, primary);
    var primaryMin = Math.min.apply(null, primary);
    var maxSize = SIZE_CHART[SIZE_CHART.length - 1].size;

    return {
      byMeasure: byMeasure,
      lowSize: primaryMax,
      highSize: Math.min(primaryMax + 2, maxSize),
      spread: primaryMax - primaryMin,
      hipsOutsizePrimary: byMeasure.hips > primaryMax,
      fullSkirt: fullSkirt,
      offChart: offChart(bust, 'bust') || offChart(waist, 'waist') || offChart(hips, 'hips'),
      chartSource: CHART_SOURCE,
    };
  }

  function alterationHints(data, sizing) {
    var hints = [];
    var ft = parseInt(data.height_ft, 10);
    var inch = parseInt(data.height_in, 10) || 0;
    var totalInches = (isFinite(ft) ? ft * 12 : 0) + inch;

    // Standard quince gowns assume ~5'5"-5'7" with heels.
    if (totalInches && totalInches < 63) {
      hints.push('Hem adjustment is likely. Most quinceañera gowns are cut for someone around 5\'5" to 5\'7" in heels.');
    } else if (totalInches && totalInches > 70) {
      hints.push('Worth a length review with your stylist. Taller heights sometimes need extra length, which can affect which designers work best.');
    }

    if (sizing && sizing.spread >= 4) {
      hints.push('Your bust, waist, and hip measurements suggest your fit varies a bit by area, so bodice or waist alterations are common.');
    }

    if (data.back === 'corset') {
      hints.push('Corset backs give more flexibility through the bodice, which can ease minor fit differences.');
    }
    if (data.back === 'zipper') {
      hints.push('Zipper backs need a closer match to the designer\'s chart, so your stylist will confirm sizing in store.');
    }

    if (data.style === 'mermaid') {
      hints.push('Fitted and mermaid styles need more careful hip and bodice fit. Bring shoes you might wear so we can check the silhouette.');
    }

    if (!hints.length) {
      hints.push('Most formal gowns need some adjustment. Common areas include the hem, bodice, and bust or waist fit.');
    }
    return hints;
  }

  function labelFor(options, value) {
    for (var i = 0; i < options.length; i++) {
      if (options[i].value === value) return options[i].label;
    }
    return null;
  }

  function formatHeight(data) {
    var ft = parseInt(data.height_ft, 10);
    var inch = parseInt(data.height_in, 10) || 0;
    if (!isFinite(ft)) return null;
    return ft + "'" + inch + '"';
  }

  function buildSummary(data, sizing) {
    if (!sizing) return '';
    var lines = ['Fit Prep Summary (Bella\'s XV)'];

    var measure = 'Measurements: bust ' + data.bust + '" · waist ' + data.waist + '" · hips ' + data.hips + '"';
    var h = formatHeight(data);
    if (h) measure += ' · height ' + h;
    lines.push(measure);

    if (sizing.offChart) {
      lines.push('Estimated range: at upper end of reference chart. Stylist to confirm fit with extended-size designers in store.');
    } else {
      lines.push(
        'Estimated range: size ' + sizing.lowSize + '-' + sizing.highSize +
        ' (by-measure: bust ' + sizing.byMeasure.bust +
        ' · waist ' + sizing.byMeasure.waist +
        ' · hips ' + sizing.byMeasure.hips + ')'
      );
    }
    lines.push('Chart: ' + sizing.chartSource);

    var prefs = [];
    var styleLabel = labelFor(STYLE_OPTIONS, data.style);
    if (styleLabel) prefs.push('Style: ' + styleLabel);
    var backLabel = labelFor(BACK_OPTIONS, data.back);
    if (backLabel) prefs.push('Back: ' + backLabel);
    var budgetLabel = labelFor(BUDGET_OPTIONS, data.budget);
    if (budgetLabel) prefs.push('Budget: ' + budgetLabel);
    if (prefs.length) lines.push(prefs.join(' · '));

    if (data.event_date) lines.push('Event date: ' + data.event_date);
    if (data.colors && data.colors.trim()) lines.push('Colors: ' + data.colors.trim());
    if (data.likes && data.likes.trim()) lines.push('Likes: ' + data.likes.trim());
    if (data.avoids && data.avoids.trim()) lines.push('Avoid: ' + data.avoids.trim());
    lines.push('Prep estimate only. Stylist will confirm in store.');

    var summary = lines.join('\n');
    if (summary.length <= SUMMARY_MAX_CHARS) return summary;

    // Trim free-text fields first if we somehow exceed (long likes/avoids).
    var hard = SUMMARY_MAX_CHARS - 3;
    return summary.slice(0, hard) + '...';
  }

  function saveSummary(summary) {
    try { window.localStorage.setItem(SUMMARY_LS_KEY, summary); } catch (e) { /* ignore */ }
    if (window.BellasBookingWidget && typeof window.BellasBookingWidget.setNote === 'function') {
      try { window.BellasBookingWidget.setNote(summary); } catch (e) { /* ignore */ }
    }
  }

  function clearSummary() {
    try { window.localStorage.removeItem(SUMMARY_LS_KEY); } catch (e) { /* ignore */ }
    if (window.BellasBookingWidget && typeof window.BellasBookingWidget.setNote === 'function') {
      try { window.BellasBookingWidget.setNote(''); } catch (e) { /* ignore */ }
    }
  }

  // Build the API payload from the current widget state. Empty strings
  // become null so the server-side validator accepts the submission only
  // when at least one meaningful field is set.
  function buildSubmissionPayload(data, sizing, summary) {
    function num(v) {
      var n = parseFloat(v);
      return isFinite(n) ? n : null;
    }
    function intval(v) {
      var n = parseInt(v, 10);
      return isFinite(n) ? n : null;
    }
    function str(v) {
      return v && String(v).trim() ? String(v).trim() : null;
    }

    return {
      measurements: {
        bust_inches: num(data.bust),
        waist_inches: num(data.waist),
        hips_inches: num(data.hips),
        height_ft: intval(data.height_ft),
        height_in: intval(data.height_in),
      },
      sizing: sizing ? {
        estimated_size_low: sizing.lowSize != null ? sizing.lowSize : null,
        estimated_size_high: sizing.highSize != null ? sizing.highSize : null,
        size_by_bust: sizing.byMeasure ? sizing.byMeasure.bust : null,
        size_by_waist: sizing.byMeasure ? sizing.byMeasure.waist : null,
        size_by_hips: sizing.byMeasure ? sizing.byMeasure.hips : null,
        chart_source: sizing.chartSource || null,
        off_chart: !!sizing.offChart,
      } : {},
      preferences: {
        style: str(data.style),
        back: str(data.back),
        budget: str(data.budget),
        colors: str(data.colors),
        likes: str(data.likes),
        avoids: str(data.avoids),
      },
      summary: str(summary),
      visitor_id: readVisitorCookie(),
      session_id: state.sessionId,
    };
  }

  function postJSON(path, body) {
    var url = (config.apiBaseUrl || '').replace(/\/+$/, '') + path;
    return window.fetch(url, {
      method: 'POST',
      credentials: 'omit',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      }).catch(function () {
        return { ok: res.ok, status: res.status, data: null };
      });
    });
  }

  function submitProfileWithToken(token, payload) {
    return postJSON(
      '/api/booking/boutique-experience/' + encodeURIComponent(token),
      payload
    );
  }

  function submitProfileWithConfirmation(confirmationCode, email, payload) {
    return postJSON('/api/booking/boutique-experience/confirm', {
      confirmation_code: confirmationCode,
      email: email,
      profile: payload,
    });
  }

  function submitPreBookingProfile(payload) {
    return postJSON('/api/booking/boutique-experience', payload);
  }

  function formatSlotLabel(slotStart, tz) {
    if (!slotStart) return '';
    try {
      var d = new Date(slotStart);
      var datePart = d.toLocaleDateString('en-US', {
        weekday: 'long', month: 'long', day: 'numeric',
        timeZone: tz || undefined,
      });
      var timePart = d.toLocaleTimeString('en-US', {
        hour: 'numeric', minute: '2-digit', hour12: true,
        timeZone: tz || undefined,
      });
      return datePart + ' at ' + timePart;
    } catch (e) {
      return '';
    }
  }

  function prepChecklist(data) {
    var items = [
      'Wear or bring nude, strapless undergarments if you have them.',
      'Bring shoes near the heel height you might wear. A similar pair works fine if you don\'t have the exact ones.',
      'Bring measurements written down (we have yours saved here as a starting point).',
      'Save 1-3 inspiration photos on your phone.',
    ];
    if (data.back === 'unsure' || !data.back) {
      items.push('Think about whether you prefer a corset back or a zipper back.');
    }
    return items;
  }

  // ---------------------------------------------------------------------
  // Validation
  // ---------------------------------------------------------------------

  function validateStep1() {
    var d = state.data;
    var issues = [];
    function checkMeasure(label, val, min, max) {
      var n = parseFloat(val);
      if (!isFinite(n)) issues.push('Enter your ' + label + ' in inches.');
      else if (n < min || n > max) issues.push(label + ' looks off. Expected ' + min + ' to ' + max + '".');
    }
    checkMeasure('bust',  d.bust,  24, 70);
    checkMeasure('waist', d.waist, 18, 60);
    checkMeasure('hips',  d.hips,  28, 75);
    var ft = parseInt(d.height_ft, 10);
    if (!isFinite(ft) || ft < 4 || ft > 7) issues.push('Enter your height in feet (4-7).');
    var inch = parseInt(d.height_in, 10);
    if (d.height_in !== '' && (!isFinite(inch) || inch < 0 || inch > 11)) {
      issues.push('Inches part of height should be 0-11.');
    }
    return issues;
  }

  // ---------------------------------------------------------------------
  // DOM helpers
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
  --fp-bg: ${t.color_bg || '#FBF5EF'};
  --fp-surface: ${t.color_surface || '#FFFFFF'};
  --fp-accent: ${t.color_accent || '#A7616F'};
  --fp-accent-dark: ${t.color_accent_dark || '#7E4451'};
  --fp-text: ${t.color_text || '#2A1B1F'};
  --fp-text-muted: ${t.color_text_muted || '#7A6A6F'};
  --fp-radius: ${t.radius || '16px'};
  --fp-radius-sm: 10px;
  --fp-border: rgba(0,0,0,0.06);
  display: block;
  width: 100%;
  max-width: 760px;
  margin: 0 auto;
  background: var(--fp-bg);
  border-radius: var(--fp-radius);
  padding: 8px;
  color: var(--fp-text);
  line-height: 1.45;
}
.${NS}-card {
  background: var(--fp-surface);
  border-radius: var(--fp-radius);
  padding: 28px;
}
@media (max-width: 720px) { .${NS}-card { padding: 20px; } }
.${NS}-eyebrow {
  font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--fp-accent-dark); font-weight: 600; margin: 0 0 8px 0;
}
.${NS}-title {
  font-family: ${t.font_heading || 'Playfair Display, serif'};
  font-size: 28px; font-weight: 600; margin: 0 0 8px 0;
}
.${NS}-subtitle { color: var(--fp-text-muted); font-size: 15px; margin: 0 0 18px 0; }
.${NS}-progress { display: flex; gap: 6px; margin-bottom: 18px; }
.${NS}-progress-dot { height: 4px; flex: 1; background: rgba(0,0,0,0.06); border-radius: 2px; }
.${NS}-progress-dot[data-active="true"] { background: var(--fp-accent); }
.${NS}-section { margin: 18px 0; }
.${NS}-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 540px) { .${NS}-row { grid-template-columns: 1fr; } }
.${NS}-field { display: flex; flex-direction: column; gap: 6px; }
.${NS}-label { font-size: 13px; color: var(--fp-text-muted); }
.${NS}-input, .${NS}-select, .${NS}-textarea {
  background: var(--fp-surface); border: 1px solid var(--fp-border);
  border-radius: var(--fp-radius-sm); padding: 11px 13px;
  font-size: 15px; color: var(--fp-text); width: 100%;
}
.${NS}-input:focus, .${NS}-select:focus, .${NS}-textarea:focus {
  outline: none; border-color: var(--fp-accent);
}
.${NS}-textarea { min-height: 64px; resize: vertical; }
.${NS}-pill-row { display: flex; flex-wrap: wrap; gap: 8px; }
.${NS}-pill {
  background: var(--fp-surface); border: 1px solid var(--fp-border);
  border-radius: 999px; padding: 8px 14px; cursor: pointer;
  font-size: 14px; color: var(--fp-text);
}
.${NS}-pill[aria-pressed="true"] {
  border-color: var(--fp-accent); background: rgba(167, 97, 111, 0.08);
  color: var(--fp-accent-dark); font-weight: 600;
}
.${NS}-edu {
  background: rgba(167, 97, 111, 0.06);
  border-left: 3px solid var(--fp-accent);
  padding: 12px 14px; border-radius: var(--fp-radius-sm);
  font-size: 14px; color: var(--fp-text); margin: 14px 0;
}
.${NS}-edu strong { color: var(--fp-accent-dark); }
.${NS}-actions { display: flex; gap: 8px; justify-content: space-between; margin-top: 18px; }
.${NS}-cta {
  background: var(--fp-accent); color: #fff; border: none; cursor: pointer;
  padding: 12px 22px; border-radius: var(--fp-radius-sm);
  font-size: 15px; font-weight: 600;
}
.${NS}-cta:hover { background: var(--fp-accent-dark); }
.${NS}-back {
  background: transparent; color: var(--fp-text-muted); border: none;
  cursor: pointer; padding: 12px 0; font-size: 14px;
}
.${NS}-back:hover { color: var(--fp-text); }
.${NS}-issue {
  color: #B83A3A; background: rgba(184, 58, 58, 0.08);
  padding: 10px 12px; border-radius: 8px; font-size: 14px; margin-top: 10px;
}
.${NS}-result-size {
  font-family: ${t.font_heading || 'Playfair Display, serif'};
  font-size: 36px; color: var(--fp-accent-dark); font-weight: 600;
  margin: 4px 0;
}
.${NS}-result-disclaimer {
  font-size: 13px; color: var(--fp-text-muted); margin-top: 6px;
}
.${NS}-list { padding-left: 18px; margin: 8px 0 0 0; }
.${NS}-list li { margin-bottom: 6px; font-size: 15px; }
.${NS}-by-measure {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
  margin: 12px 0;
}
.${NS}-by-measure-item {
  background: rgba(167, 97, 111, 0.06); border-radius: var(--fp-radius-sm);
  padding: 10px; text-align: center;
}
.${NS}-by-measure-item .label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--fp-text-muted);
}
.${NS}-by-measure-item .val {
  font-size: 18px; font-weight: 600; color: var(--fp-text);
  font-family: ${t.font_heading || 'Playfair Display, serif'};
}
.${NS}-divider { border: none; border-top: 1px solid var(--fp-border); margin: 22px 0; }
.${NS}-section-title {
  font-family: ${t.font_heading || 'Playfair Display, serif'};
  font-size: 20px; font-weight: 600; margin: 0 0 8px 0;
}
.${NS}-handoff {
  margin-top: 22px; padding: 18px; border-radius: var(--fp-radius-sm);
  background: rgba(167, 97, 111, 0.06); border: 1px solid var(--fp-border);
}
.${NS}-handoff-help {
  margin: 0 0 14px 0; font-size: 14px; color: var(--fp-text-muted);
}
.${NS}-handoff .${NS}-cta { width: 100%; }
.${NS}-handoff .${NS}-issue { margin: 0 0 12px 0; }
.${NS}-lookup {
  margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--fp-border);
}
.${NS}-lookup-title {
  font-size: 15px; font-weight: 600; margin: 0 0 6px 0; color: var(--fp-text);
}
.${NS}-lookup-row {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 10px 0;
}
@media (max-width: 540px) { .${NS}-lookup-row { grid-template-columns: 1fr; } }
`;
    var style = document.getElementById(NS + '-styles');
    if (style) style.parentNode.removeChild(style);
    style = document.createElement('style');
    style.id = NS + '-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------

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

  function field(labelText, control) {
    var wrap = el('label', { className: NS + '-field' });
    wrap.appendChild(el('span', { className: NS + '-label' }, [labelText]));
    wrap.appendChild(control);
    return wrap;
  }

  function input(name, opts) {
    opts = opts || {};
    var attrs = {
      className: NS + '-input',
      type: opts.type || 'text',
      name: name,
      value: state.data[name] || '',
      inputmode: opts.inputmode || null,
      placeholder: opts.placeholder || null,
      oninput: function (ev) { state.data[name] = ev.target.value; },
    };
    if (opts.min != null) attrs.min = opts.min;
    if (opts.max != null) attrs.max = opts.max;
    if (opts.step != null) attrs.step = opts.step;
    return el('input', attrs);
  }

  function pillGroup(name, options) {
    var wrap = el('div', { className: NS + '-pill-row', role: 'group' });
    options.forEach(function (opt) {
      wrap.appendChild(el('button', {
        className: NS + '-pill',
        type: 'button',
        'aria-pressed': state.data[name] === opt.value ? 'true' : 'false',
        onclick: function () { state.data[name] = opt.value; render(); },
      }, [opt.label]));
    });
    return wrap;
  }

  function edu(html) {
    return el('div', { className: NS + '-edu', html: html });
  }

  function render() {
    if (!state.root) return;
    state.root.innerHTML = '';
    var card = el('div', { className: NS + '-card' });
    if (state.step === 'step1') card.appendChild(renderStep1());
    else if (state.step === 'step2') card.appendChild(renderStep2());
    else card.appendChild(renderResult());
    state.root.appendChild(card);
    try { state.container.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    catch (e) { /* ignore */ }
  }

  function renderStep1() {
    var wrap = el('div', {});
    wrap.appendChild(progressBar(1));
    wrap.appendChild(el('p', { className: NS + '-eyebrow' }, ['Step 1 of 3']));
    wrap.appendChild(el('h2', { className: NS + '-title' }, ['Your measurements']));
    wrap.appendChild(el('p', { className: NS + '-subtitle' }, [
      'A starting point for your appointment. Your stylist will confirm everything in store.',
    ]));

    wrap.appendChild(edu(
      '<strong>How quince sizing works.</strong> Quinceañera dresses follow formalwear sizing, ' +
      'which often runs 1-3 sizes smaller than everyday clothing. Each designer uses their own chart, ' +
      'so this is only a prep estimate.'
    ));

    var row1 = el('div', { className: NS + '-row' });
    row1.appendChild(field('Bust (inches)', input('bust', { type: 'number', inputmode: 'decimal', step: '0.5', min: 24, max: 70, placeholder: 'e.g. 36' })));
    row1.appendChild(field('Waist (inches)', input('waist', { type: 'number', inputmode: 'decimal', step: '0.5', min: 18, max: 60, placeholder: 'e.g. 28' })));
    wrap.appendChild(row1);

    var row2 = el('div', { className: NS + '-row', style: 'margin-top:12px' });
    row2.appendChild(field('Hips (inches)', input('hips', { type: 'number', inputmode: 'decimal', step: '0.5', min: 28, max: 75, placeholder: 'e.g. 39' })));
    var heightWrap = el('div', { className: NS + '-field' });
    heightWrap.appendChild(el('span', { className: NS + '-label' }, ['Height']));
    var hRow = el('div', { className: NS + '-row', style: 'gap:8px' });
    hRow.appendChild(input('height_ft', { type: 'number', inputmode: 'numeric', min: 4, max: 7, placeholder: 'ft' }));
    hRow.appendChild(input('height_in', { type: 'number', inputmode: 'numeric', min: 0, max: 11, placeholder: 'in' }));
    heightWrap.appendChild(hRow);
    row2.appendChild(heightWrap);
    wrap.appendChild(row2);

    var actions = el('div', { className: NS + '-actions' });
    actions.appendChild(el('span', {}));
    actions.appendChild(el('button', {
      className: NS + '-cta',
      type: 'button',
      onclick: function () {
        var issues = validateStep1();
        var existing = wrap.querySelector('.' + NS + '-issue');
        if (existing) existing.parentNode.removeChild(existing);
        if (issues.length) {
          wrap.appendChild(el('div', { className: NS + '-issue' }, [issues[0]]));
          return;
        }
        state.step = 'step2'; render();
      },
    }, ['Continue to style preferences →']));
    wrap.appendChild(actions);
    return wrap;
  }

  function renderStep2() {
    var wrap = el('div', {});
    wrap.appendChild(progressBar(2));
    wrap.appendChild(el('p', { className: NS + '-eyebrow' }, ['Step 2 of 3']));
    wrap.appendChild(el('h2', { className: NS + '-title' }, ['Style preferences']));
    wrap.appendChild(el('p', { className: NS + '-subtitle' }, [
      'Optional, but it helps your stylist pull better dresses faster.',
    ]));

    wrap.appendChild(field('Preferred dress style', pillGroup('style', STYLE_OPTIONS)));

    var backField = el('div', { className: NS + '-section' });
    backField.appendChild(field('Back preference', pillGroup('back', BACK_OPTIONS)));
    wrap.appendChild(backField);

    wrap.appendChild(edu(
      '<strong>Corset vs. zipper.</strong> Corset backs give more flexibility through the bodice, ' +
      'which can ease minor fit differences. Zipper backs need a closer match to the designer\'s chart.'
    ));

    var budgetField = el('div', { className: NS + '-section' });
    budgetField.appendChild(field('Budget range', pillGroup('budget', BUDGET_OPTIONS)));
    wrap.appendChild(budgetField);

    wrap.appendChild(field('Favorite colors (optional)', input('colors', { placeholder: 'e.g. Red, champagne' })));
    var likes = el('textarea', {
      className: NS + '-textarea',
      rows: '2',
      placeholder: 'Sleeves, sparkle, off-shoulder, capes…',
      oninput: function (ev) { state.data.likes = ev.target.value; },
    }, [state.data.likes || '']);
    var avoids = el('textarea', {
      className: NS + '-textarea',
      rows: '2',
      placeholder: 'Anything you definitely do not want',
      oninput: function (ev) { state.data.avoids = ev.target.value; },
    }, [state.data.avoids || '']);
    wrap.appendChild(field('Things you like (optional)', likes));
    wrap.appendChild(field('Things to avoid (optional)', avoids));

    var actions = el('div', { className: NS + '-actions' });
    actions.appendChild(el('button', {
      className: NS + '-back',
      type: 'button',
      onclick: function () { state.step = 'step1'; render(); },
    }, ['‹ Back']));
    actions.appendChild(el('button', {
      className: NS + '-cta',
      type: 'button',
      onclick: function () {
        state.sizing = computeSizing(state.data);
        // Each "See my prep guide" click is a fresh CTA opportunity. If
        // the customer edited answers after a previous submit, drop the
        // old "sent" state so the result screen shows the submit button
        // again instead of a stale success indicator.
        state.handoff = freshHandoff();
        state.step = 'result';
        render();
      },
    }, ['See my prep guide']));
    wrap.appendChild(actions);
    return wrap;
  }

  function renderResult() {
    var wrap = el('div', {});
    wrap.appendChild(progressBar(3));
    wrap.appendChild(el('p', { className: NS + '-eyebrow' }, [
      'Your Boutique Experience Profile',
    ]));

    var s = state.sizing;
    if (!s) {
      wrap.appendChild(el('h2', { className: NS + '-title' }, ['We need your measurements']));
      wrap.appendChild(el('p', {}, ['Please go back and enter your bust, waist, and hips so we can estimate a starting size.']));
    } else {
      wrap.appendChild(el('h2', { className: NS + '-title' }, ['Estimated formalwear range']));

      if (s.offChart) {
        wrap.appendChild(el('p', { className: NS + '-subtitle' }, [
          'Your measurements are toward the upper end of our reference chart. Bella\'s XV carries designers with extended sizing, so your stylist will help you find the right fit in store.',
        ]));
      } else {
        wrap.appendChild(el('div', { className: NS + '-result-size' }, [
          'Size ' + s.lowSize + ' to ' + s.highSize,
        ]));
        wrap.appendChild(el('p', { className: NS + '-result-disclaimer' }, [
          'You may fall around this range depending on designer, dress structure, and fit preference. Your stylist will confirm using the designer\'s chart in store.',
        ]));
        wrap.appendChild(el('p', { className: NS + '-result-disclaimer' }, [
          'Estimated using ' + s.chartSource + '.',
        ]));

        var byM = el('div', { className: NS + '-by-measure' });
        ['bust', 'waist', 'hips'].forEach(function (k) {
          var item = el('div', { className: NS + '-by-measure-item' });
          item.appendChild(el('div', { className: 'label' }, [k.toUpperCase()]));
          item.appendChild(el('div', { className: 'val' }, ['Size ' + s.byMeasure[k]]));
          byM.appendChild(item);
        });
        wrap.appendChild(byM);

        if (s.fullSkirt && s.hipsOutsizePrimary) {
          wrap.appendChild(el('p', { className: NS + '-result-disclaimer' }, [
            'Your hip measurement maps to a larger size, but for ball gowns, A-lines, and two-piece styles the skirt usually has plenty of ease. Most stylists size from the bust and waist for these silhouettes.',
          ]));
        }
      }

      wrap.appendChild(edu(
        '<strong>This is a preparation estimate, not a final dress size.</strong> ' +
        'Bella\'s XV carries multiple designers (Morilee, House of Wu, and others), and each designer uses their own chart. ' +
        'Formalwear sizing often runs 1-3 sizes smaller than everyday clothing.'
      ));

      wrap.appendChild(el('hr', { className: NS + '-divider' }));

      wrap.appendChild(el('h3', { className: NS + '-section-title' }, ['Likely alteration areas']));
      var alterations = el('ul', { className: NS + '-list' });
      alterationHints(state.data, s).forEach(function (h) {
        alterations.appendChild(el('li', {}, [h]));
      });
      wrap.appendChild(alterations);
    }

    wrap.appendChild(el('hr', { className: NS + '-divider' }));
    wrap.appendChild(el('h3', { className: NS + '-section-title' }, ['What to bring to your appointment']));
    var checklist = el('ul', { className: NS + '-list' });
    prepChecklist(state.data).forEach(function (item) {
      checklist.appendChild(el('li', {}, [item]));
    });
    wrap.appendChild(checklist);

    if (s) {
      wrap.appendChild(renderHandoff(s));
    }

    var actions = el('div', { className: NS + '-actions' });
    actions.appendChild(el('button', {
      className: NS + '-back',
      type: 'button',
      onclick: function () {
        // Drop the success indicator now so it doesn't flash back if the
        // customer revisits result without retriggering "See my prep
        // guide" through some other path later.
        state.handoff = freshHandoff();
        state.step = 'step2';
        render();
      },
    }, ['‹ Edit answers']));
    wrap.appendChild(actions);

    return wrap;
  }

  // ---------------------------------------------------------------------
  // Result-screen handoff: token path or pre-booking handoff
  // ---------------------------------------------------------------------

  function renderHandoff(sizing) {
    var box = el('div', { className: NS + '-handoff' });
    return state.token
      ? renderTokenHandoff(box, sizing)
      : renderPreBookingHandoff(box, sizing);
  }

  function renderTokenHandoff(box, sizing) {
    var h = state.handoff;

    if (h.status === 'sent') {
      return renderAttachedHandoffSuccess(box);
    }

    box.appendChild(el('h3', { className: NS + '-section-title' }, [
      'Send this to your stylist',
    ]));
    box.appendChild(el('p', { className: NS + '-handoff-help' }, [
      'Your stylist will use these answers to pull dresses in your size and style before you arrive.',
    ]));

    if (h.status === 'error' && h.errorMsg) {
      box.appendChild(el('div', { className: NS + '-issue' }, [h.errorMsg]));
    }

    var attrs = {
      className: NS + '-cta',
      type: 'button',
      onclick: function () { submitTokenHandoff(sizing); },
    };
    if (h.status === 'submitting') attrs.disabled = 'disabled';
    box.appendChild(el('button', attrs, [
      h.status === 'submitting' ? 'Sending…' : 'Send to my stylist',
    ]));

    box.appendChild(renderExistingBookingLookup(sizing));
    return box;
  }

  function renderPreBookingHandoff(box, sizing) {
    var h = state.handoff;

    if (h.status === 'sent') {
      if (h.sentMode === 'attached') {
        return renderAttachedHandoffSuccess(box);
      }
      box.appendChild(el('h3', { className: NS + '-section-title' }, [
        'Saved for your booking',
      ]));
      box.appendChild(el('p', { className: NS + '-handoff-help' }, [
        'Your prep answers are ready for the booking form below. Pick a time and we will have your size and style notes when you arrive.',
      ]));
      box.appendChild(el('button', {
        className: NS + '-cta',
        type: 'button',
        onclick: function () {
          var target = document.getElementById(config.bookingAnchorId || 'book');
          if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        },
      }, ['Continue to booking']));
      return box;
    }

    box.appendChild(el('h3', { className: NS + '-section-title' }, [
      'Book with this profile',
    ]));
    box.appendChild(el('p', { className: NS + '-handoff-help' }, [
      'Save your answers and book a fitting. Your stylist will see your size estimate and style notes before you walk in.',
    ]));

    if (h.status === 'error' && h.errorMsg) {
      box.appendChild(el('div', { className: NS + '-issue' }, [h.errorMsg]));
    }

    var attrs = {
      className: NS + '-cta',
      type: 'button',
      onclick: function () { submitPreBookingHandoff(sizing); },
    };
    if (h.status === 'submitting') attrs.disabled = 'disabled';
    box.appendChild(el('button', attrs, [
      h.status === 'submitting' ? 'Saving…' : 'Book with this profile',
    ]));

    box.appendChild(renderExistingBookingLookup(sizing));
    return box;
  }

  function renderAttachedHandoffSuccess(box) {
    var h = state.handoff;
    box.appendChild(el('h3', { className: NS + '-section-title' }, [
      'Saved to your booked visit',
    ]));
    box.appendChild(el('p', { className: NS + '-handoff-help' }, [
      h.sentConfirmationCode
        ? 'We matched confirmation ' + h.sentConfirmationCode + ' and saved your prep answers to that visit.'
        : 'We matched your booking and saved your prep answers to that visit.',
    ]));
    if (h.sentSlotLabel) {
      box.appendChild(el('p', { className: NS + '-handoff-help' }, [
        'Your stylist will see these notes before ' + h.sentSlotLabel + '.',
      ]));
    } else {
      box.appendChild(el('p', { className: NS + '-handoff-help' }, [
        'Your stylist will see your size estimate and style notes before you arrive.',
      ]));
    }
    return box;
  }

  function renderExistingBookingLookup(sizing) {
    var lookup = el('div', { className: NS + '-lookup' });
    lookup.appendChild(el('p', { className: NS + '-lookup-title' }, [
      'Already booked?',
    ]));
    lookup.appendChild(el('p', { className: NS + '-handoff-help' }, [
      'Enter your confirmation code and email to save these answers to your visit.',
    ]));

    var row = el('div', { className: NS + '-lookup-row' });
    row.appendChild(field('Confirmation code', el('input', {
      className: NS + '-input',
      type: 'text',
      name: 'confirmation_code',
      value: state.bookingLookup.confirmationCode,
      placeholder: 'BX-UU5MY4',
      autocomplete: 'off',
      oninput: function (ev) {
        state.bookingLookup.confirmationCode = ev.target.value;
      },
    })));
    row.appendChild(field('Email', el('input', {
      className: NS + '-input',
      type: 'email',
      name: 'booking_email',
      value: state.bookingLookup.email,
      placeholder: 'you@example.com',
      autocomplete: 'email',
      oninput: function (ev) {
        state.bookingLookup.email = ev.target.value;
      },
    })));
    lookup.appendChild(row);

    var attrs = {
      className: NS + '-cta',
      type: 'button',
      onclick: function () { submitConfirmationHandoff(sizing); },
    };
    if (state.handoff.status === 'submitting') attrs.disabled = 'disabled';
    lookup.appendChild(el('button', attrs, [
      state.handoff.status === 'submitting' ? 'Saving…' : 'Save to my booked visit',
    ]));
    return lookup;
  }

  function submitTokenHandoff(sizing) {
    if (state.handoff.status === 'submitting') return;
    state.handoff.status = 'submitting';
    state.handoff.errorMsg = null;
    render();

    var summary = buildSummary(state.data, sizing);
    var payload = buildSubmissionPayload(state.data, sizing, summary);

    submitProfileWithToken(state.token, payload).then(function (res) {
      if (res.ok && res.data && res.data.profile_id) {
        state.handoff.profileId = res.data.profile_id;
        state.handoff.sentMode = 'attached';
        state.handoff.sentSlotLabel = formatSlotLabel(
          res.data.slot_start, res.data.timezone
        );
        state.handoff.sentConfirmationCode = res.data.confirmation_code || null;
        state.handoff.status = 'sent';
        // Token submission ties the profile to a real appointment, so
        // the booking-widget handoff localStorage is no longer relevant.
        clearSummary();
        clearProfileId();
        clearPostBookingUrl();
        render();
        return;
      }
      if (res.status === 404) {
        state.handoff.errorMsg = (
          'Your prep link is no longer valid. Please call us at ' +
          '(210) 670-5845 and we will save your answers for you.'
        );
      } else if (res.status === 409) {
        state.handoff.errorMsg = (
          'This appointment is no longer active. Please call us at ' +
          '(210) 670-5845 if you need to rebook.'
        );
      } else if (res.status === 422) {
        state.handoff.errorMsg = (
          'Please go back and fill in at least your measurements or a ' +
          'style preference so your stylist has something to work with.'
        );
      } else {
        state.handoff.errorMsg = (
          'Something went wrong sending your answers. Please try again, ' +
          'or call us at (210) 670-5845.'
        );
      }
      state.handoff.status = 'error';
      render();
    }).catch(function () {
      state.handoff.status = 'error';
      state.handoff.errorMsg = (
        'Something went wrong sending your answers. Please try again, ' +
        'or call us at (210) 670-5845.'
      );
      render();
    });
  }

  function submitConfirmationHandoff(sizing) {
    if (state.handoff.status === 'submitting') return;

    var code = (state.bookingLookup.confirmationCode || '').trim();
    var email = (state.bookingLookup.email || '').trim();
    if (!code || !email) {
      state.handoff.status = 'error';
      state.handoff.errorMsg = (
        'Enter your confirmation code and email so we can find your booking.'
      );
      render();
      return;
    }

    state.handoff.status = 'submitting';
    state.handoff.errorMsg = null;
    render();

    var summary = buildSummary(state.data, sizing);
    var payload = buildSubmissionPayload(state.data, sizing, summary);

    submitProfileWithConfirmation(code, email, payload).then(function (res) {
      if (res.ok && res.data && res.data.profile_id) {
        state.handoff.profileId = res.data.profile_id;
        state.handoff.sentMode = 'attached';
        state.handoff.sentSlotLabel = formatSlotLabel(
          res.data.slot_start, res.data.timezone
        );
        state.handoff.sentConfirmationCode = res.data.confirmation_code || code;
        state.handoff.status = 'sent';
        clearSummary();
        clearProfileId();
        clearPostBookingUrl();
        render();
        return;
      }
      if (res.status === 404) {
        state.handoff.errorMsg = (
          'We could not find a booking with those details. Please check both and try again.'
        );
      } else if (res.status === 409) {
        state.handoff.errorMsg = (
          'This appointment is no longer active. Please call us at ' +
          '(210) 670-5845 if you need help.'
        );
      } else if (res.status === 422) {
        state.handoff.errorMsg = (
          'Please check your confirmation code, email, and answers, then try again.'
        );
      } else {
        state.handoff.errorMsg = (
          'Something went wrong saving your answers. Please try again, ' +
          'or call us at (210) 670-5845.'
        );
      }
      state.handoff.status = 'error';
      render();
    }).catch(function () {
      state.handoff.status = 'error';
      state.handoff.errorMsg = (
        'Something went wrong saving your answers. Please try again, ' +
        'or call us at (210) 670-5845.'
      );
      render();
    });
  }

  function submitPreBookingHandoff(sizing) {
    if (state.handoff.status === 'submitting') return;
    state.handoff.status = 'submitting';
    state.handoff.errorMsg = null;
    render();

    var summary = buildSummary(state.data, sizing);
    var payload = buildSubmissionPayload(state.data, sizing, summary);

    submitPreBookingProfile(payload).then(function (res) {
      if (res.ok && res.data && res.data.profile_id) {
        state.handoff.profileId = res.data.profile_id;
        state.handoff.sentMode = 'pre_booking';
        state.handoff.status = 'sent';
        // Belt-and-suspenders: save the server profile id for the booking
        // widget AND keep the legacy textual summary so the booking widget
        // still shows its prefill notice if the booking widget hasn't been
        // updated to consume the profile id yet (Phase 5).
        saveProfileId(res.data.profile_id);
        saveSummary(summary);
        render();
        return;
      }

      // Server rejected the payload. Fall back to the legacy localStorage
      // handoff so the booking widget still gets the textual summary —
      // the customer is not blocked from booking. Clear any stale profile
      // id from a previous session so the booking widget does not submit
      // the wrong id alongside the new summary.
      clearProfileId();
      saveSummary(summary);
      state.handoff.status = 'sent';
      render();
    }).catch(function () {
      // Network error. Same fallback path; same stale-id concern.
      clearProfileId();
      saveSummary(buildSummary(state.data, sizing));
      state.handoff.status = 'sent';
      render();
    });
  }

  function saveProfileId(id) {
    try { window.localStorage.setItem(PROFILE_ID_LS_KEY, String(id)); }
    catch (e) { /* ignore */ }
    if (window.BellasBookingWidget &&
        typeof window.BellasBookingWidget.setBoutiqueExperienceProfileId === 'function') {
      try { window.BellasBookingWidget.setBoutiqueExperienceProfileId(id); }
      catch (e) { /* ignore */ }
    }
  }

  function clearProfileId() {
    try { window.localStorage.removeItem(PROFILE_ID_LS_KEY); }
    catch (e) { /* ignore */ }
    if (window.BellasBookingWidget &&
        typeof window.BellasBookingWidget.setBoutiqueExperienceProfileId === 'function') {
      try { window.BellasBookingWidget.setBoutiqueExperienceProfileId(null); }
      catch (e) { /* ignore */ }
    }
  }

  function clearPostBookingUrl() {
    try { window.localStorage.removeItem(POST_BOOKING_URL_LS_KEY); }
    catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------------
  // Lifecycle
  // ---------------------------------------------------------------------

  function init(opts) {
    config = Object.assign({}, config, opts || {});
    state.container = document.getElementById(config.containerId);
    if (!state.container) {
      console.warn('[bxvfp] container not found:', config.containerId);
      return;
    }
    state.container.innerHTML = '';
    state.root = document.createElement('div');
    state.root.className = NS + '-root';
    state.container.appendChild(state.root);

    // Customers arriving from a booking-confirmation email link land on
    // /fit-prep.html?token=... so we know which appointment to attach
    // their answers to without asking for a confirmation code or phone. If
    // they instead open the standalone calculator after booking in the same
    // browser, fall back to the tokenized URL the booking widget stored.
    state.token = readTokenFromUrl() || readStoredPostBookingToken();

    loadTheme().then(function () {
      injectStyles();
      render();
    });
  }

  function destroy() {
    if (state.container) state.container.innerHTML = '';
    var style = document.getElementById(NS + '-styles');
    if (style) style.parentNode.removeChild(style);
  }

  window.BellasFitPrepTool = {
    _initialized: true,
    init: init,
    destroy: destroy,
  };
})(window, document);
