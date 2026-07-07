// First-party storefront analytics (browser only).
//
// Sets Kelley-owned cookies `ka_vid` (visitor, 1yr) and `ka_sid` (session,
// 30-min sliding) and POSTs behavioral beacons to the FastAPI backend at
// `/api/public/track`. It also captures UTM params, the referrer, the landing
// page, and Meta's `_fbp`/`_fbc` cookies so the same event stream is ready for
// Conversions API attribution later.
//
// Everything here is best-effort and must NEVER throw into the UI: a failed
// beacon or a cookie-less browser just means we don't record that event. No
// application PII (DOB/DL/SSN/address) is ever touched here.

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

const VID_COOKIE = "ka_vid";
const SID_COOKIE = "ka_sid";
const VID_MAX_AGE = 60 * 60 * 24 * 365; // 1 year
const SID_MAX_AGE = 60 * 30; // 30-minute sliding session
const LANDING_KEY = "ka_landing"; // sessionStorage: first page of the visit
const REFERRER_KEY = "ka_referrer"; // sessionStorage: external referrer

export type StorefrontEventName =
  | "page_view"
  | "vehicle_view"
  | "lead_form_opened"
  | "lead_form_started"
  | "lead_submitted";

export interface Utm {
  source?: string;
  medium?: string;
  campaign?: string;
  term?: string;
  content?: string;
}

export interface TrackingContext {
  ka_vid?: string;
  ka_sid?: string;
  event_id?: string;
  fbp?: string;
  fbc?: string;
  landing_page?: string;
  referrer?: string;
  utm?: Utm;
}

interface VehicleContext {
  listingCode?: string | null;
  vehicleId?: number | string | null;
}

function isBrowser(): boolean {
  return typeof window !== "undefined" && typeof document !== "undefined";
}

function readCookie(name: string): string | undefined {
  if (!isBrowser()) return undefined;
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&") + "=([^;]*)")
  );
  return match ? decodeURIComponent(match[1]) : undefined;
}

function writeCookie(name: string, value: string, maxAgeSeconds: number): void {
  if (!isBrowser()) return;
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie =
    `${name}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}` +
    `; Path=/; SameSite=Lax${secure}`;
}

function newId(): string {
  try {
    if (isBrowser() && window.crypto?.randomUUID) {
      return window.crypto.randomUUID().replace(/-/g, "");
    }
  } catch {
    /* fall through */
  }
  return (
    Date.now().toString(36) + Math.random().toString(36).slice(2, 12)
  ).slice(0, 32);
}

/** Ensure the visitor cookie exists; returns the visitor key. */
function ensureVisitor(): string | undefined {
  if (!isBrowser()) return undefined;
  let vid = readCookie(VID_COOKIE);
  if (!vid) vid = newId();
  writeCookie(VID_COOKIE, vid, VID_MAX_AGE); // refresh the 1yr window
  return vid;
}

/** Ensure the (sliding) session cookie exists; returns the session key. */
function ensureSession(): string | undefined {
  if (!isBrowser()) return undefined;
  let sid = readCookie(SID_COOKIE);
  if (!sid) sid = newId();
  writeCookie(SID_COOKIE, sid, SID_MAX_AGE); // refresh the 30-min window
  return sid;
}

function readUtm(): Utm {
  if (!isBrowser()) return {};
  const p = new URLSearchParams(window.location.search);
  const utm: Utm = {};
  const src = p.get("utm_source");
  const med = p.get("utm_medium");
  const camp = p.get("utm_campaign");
  const term = p.get("utm_term");
  const content = p.get("utm_content");
  if (src) utm.source = src;
  if (med) utm.medium = med;
  if (camp) utm.campaign = camp;
  if (term) utm.term = term;
  if (content) utm.content = content;
  return utm;
}

/** Landing page + external referrer, captured once and pinned for the visit. */
function sessionOrigin(): { landing?: string; referrer?: string } {
  if (!isBrowser()) return {};
  let landing: string | undefined;
  let referrer: string | undefined;
  try {
    landing = window.sessionStorage.getItem(LANDING_KEY) || undefined;
    if (!landing) {
      landing = window.location.href;
      window.sessionStorage.setItem(LANDING_KEY, landing);
    }
    referrer = window.sessionStorage.getItem(REFERRER_KEY) || undefined;
    if (referrer === null || referrer === undefined) {
      // Only keep an EXTERNAL referrer (not same-origin internal navigation).
      const ref = document.referrer || "";
      const external =
        ref && !ref.startsWith(window.location.origin) ? ref : "";
      window.sessionStorage.setItem(REFERRER_KEY, external);
      referrer = external || undefined;
    }
  } catch {
    /* sessionStorage may be blocked; degrade gracefully */
  }
  return { landing, referrer: referrer || undefined };
}

/**
 * Assemble the attribution context to attach to a lead submission. Generates a
 * fresh `event_id` for the conversion (shared later with the browser Pixel and
 * the server-side CAPI event for deduplication).
 */
export function getTrackingContext(): TrackingContext {
  if (!isBrowser()) return {};
  const vid = ensureVisitor();
  const sid = ensureSession();
  const { landing, referrer } = sessionOrigin();
  const utm = readUtm();
  const ctx: TrackingContext = {
    ka_vid: vid,
    ka_sid: sid,
    event_id: newId(),
    fbp: readCookie("_fbp"),
    fbc: readCookie("_fbc"),
    landing_page: landing,
    referrer,
  };
  if (Object.keys(utm).length) ctx.utm = utm;
  return ctx;
}

/**
 * Fire a storefront analytics beacon. Fire-and-forget: uses `keepalive` so it
 * survives a navigation, and swallows every error.
 */
export function track(
  eventName: StorefrontEventName,
  opts: {
    vehicle?: VehicleContext;
    eventId?: string;
    metadata?: Record<string, unknown>;
  } = {}
): void {
  if (!isBrowser()) return;
  try {
    const vid = ensureVisitor();
    const sid = ensureSession();
    const { landing, referrer } = sessionOrigin();
    const utm = readUtm();
    const body: Record<string, unknown> = {
      ka_vid: vid,
      ka_sid: sid,
      event_name: eventName,
      path: window.location.pathname + window.location.search,
      referrer,
      landing_page: landing,
    };
    if (opts.eventId) body.event_id = opts.eventId;
    if (utm.source) body.utm_source = utm.source;
    if (utm.medium) body.utm_medium = utm.medium;
    if (utm.campaign) body.utm_campaign = utm.campaign;
    if (utm.term) body.utm_term = utm.term;
    if (utm.content) body.utm_content = utm.content;
    if (opts.vehicle?.listingCode) body.listing_code = opts.vehicle.listingCode;
    if (opts.vehicle?.vehicleId !== undefined && opts.vehicle?.vehicleId !== null) {
      const token = String(opts.vehicle.vehicleId).trim();
      if (/^\d+$/.test(token)) body.vehicle_id = Number(token);
    }
    if (opts.metadata) body.metadata = opts.metadata;

    void fetch(`${API_BASE}/api/public/track`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {
      /* best-effort beacon */
    });
  } catch {
    /* analytics must never break the page */
  }
}
