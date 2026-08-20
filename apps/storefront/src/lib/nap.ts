// Resolved business NAP (name / address / phone) for the public site,
// sourced from the FastAPI business profile with neutral Kelley fallbacks.
//
// Source of truth is `getBusinessProfile()`; this helper applies the agreed
// fallbacks so no street address, phone, or hours is ever hardcoded in a
// component (which would go stale). Fill the real values via the admin
// business profile and every surface updates.

import {
  getBusinessProfile,
  type BusinessHours,
  type BusinessHoursDay,
} from "@/lib/publicApi";

export interface NapHours {
  /** Display timezone label, e.g. "America/Chicago", or null. */
  timezone: string | null;
  /** Ordered weekday rows ready to render. */
  days: { day: string; display: string; closed: boolean }[];
}

export interface Nap {
  name: string;
  legalName: string;
  /** Raw phone, or null when the profile has none. */
  phone: string | null;
  /** Display string — "Call for details" when blank. */
  phoneDisplay: string;
  /** tel: href, or null when blank (render as plain text, not a link). */
  telHref: string | null;
  /** Email, or null when blank (omit the mailto link). */
  email: string | null;
  /** Address lines to render, omitting blanks. Empty when no address. */
  addressLines: string[];
  /** The same address on one line — for tight header/sticky-bar rows. */
  addressOneLine: string | null;
  /**
   * Google Maps directions link, or null when there is no address. Built from
   * the address text rather than a place id: the profile stores no coordinates
   * and no place id, and `dir/?api=1&destination=` resolves a full street
   * address reliably. Opening DIRECTIONS rather than a pin is deliberate —
   * every visitor who taps this wants the route, and a pin costs them a
   * second tap.
   */
  directionsHref: string | null;
  /** "City, ST" when available, else null. */
  cityState: string | null;
  /** A location phrase for designs that need one — cityState or "Central Texas". */
  locationLabel: string;
  hasAddress: boolean;
  /** Structured opening hours, or null when the profile has none set. */
  hours: NapHours | null;
  /** Compact one-line hours summary, or the fallback when none are set. */
  hoursText: string;
}

const HOURS_FALLBACK =
  "Contact us for current hours and appointment availability";

const DAY_ABBR: Record<string, string> = {
  Sunday: "Sun",
  Monday: "Mon",
  Tuesday: "Tue",
  Wednesday: "Wed",
  Thursday: "Thu",
  Friday: "Fri",
  Saturday: "Sat",
};

function dayDisplay(d: BusinessHoursDay): string {
  if (d.closed || !d.open || !d.close) return "Closed";
  return `${d.open} – ${d.close}`;
}

/** Build the render-ready hours rows plus a compact one-line summary that
 * collapses consecutive days with identical hours into ranges
 * (e.g. "Mon–Sat: 9:00 AM – 7:00 PM · Sun: Closed"). */
function buildHours(raw: BusinessHours | null | undefined): {
  hours: NapHours | null;
  hoursText: string;
} {
  const days = raw?.days ?? [];
  if (days.length === 0) return { hours: null, hoursText: HOURS_FALLBACK };

  const rows = days.map((d) => ({
    day: d.day,
    display: dayDisplay(d),
    closed: Boolean(d.closed) || !d.open || !d.close,
  }));

  const parts: string[] = [];
  let i = 0;
  while (i < rows.length) {
    let j = i;
    while (j + 1 < rows.length && rows[j + 1].display === rows[i].display) j++;
    const startAbbr = DAY_ABBR[rows[i].day] ?? rows[i].day;
    const endAbbr = DAY_ABBR[rows[j].day] ?? rows[j].day;
    const label = i === j ? startAbbr : `${startAbbr}–${endAbbr}`;
    parts.push(`${label}: ${rows[i].display}`);
    i = j + 1;
  }

  return {
    hours: { timezone: raw?.timezone ?? null, days: rows },
    hoursText: parts.join(" · "),
  };
}

/** `tel:` href for a profile-entered phone number.
 *
 * The profile field is free text — "(210) 251-3644" today, but an owner
 * editing it could just as easily type "+1 210 251 3644" or "1-210-251-3644".
 * Blindly prefixing "+1" onto the digits turns those into +11210…, which a
 * phone will happily fail to dial. So: strip to digits, drop a leading US
 * country code if one is already there, and only prefix +1 for a 10-digit
 * number. Anything else is passed through as typed rather than mangled.
 */
function telHref(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) return `tel:+${digits}`;
  if (digits.length === 10) return `tel:+1${digits}`;
  return `tel:${digits || phone}`;
}

export async function resolveNap(): Promise<Nap> {
  const p = await getBusinessProfile();

  const phone = p?.phone?.trim() || null;
  const email = p?.email?.trim() || null;
  const a = p?.address;
  const { hours, hoursText } = buildHours(p?.hours);

  const lines: string[] = [];
  if (a?.line1) lines.push(a.line1);
  if (a?.line2) lines.push(a.line2);
  const cityStateZip = [
    a?.city,
    [a?.state, a?.postalCode].filter(Boolean).join(" ").trim(),
  ]
    .filter(Boolean)
    .join(", ");
  if (cityStateZip) lines.push(cityStateZip);

  const cityState =
    a?.city || a?.state
      ? [a?.city, a?.state].filter(Boolean).join(", ")
      : null;

  const addressOneLine = lines.length ? lines.join(", ") : null;
  const displayName = p?.name?.trim() || "Kelley Autoplex";

  return {
    name: displayName,
    legalName: p?.legalName?.trim() || "Kelley Autoplex",
    phone,
    phoneDisplay: phone || "Call for details",
    telHref: phone ? telHref(phone) : null,
    email,
    addressLines: lines,
    addressOneLine,
    directionsHref: addressOneLine
      ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(
          `${displayName} ${addressOneLine}`,
        )}`
      : null,
    cityState,
    locationLabel: cityState || "Central Texas",
    hasAddress: lines.length > 0,
    hours,
    hoursText,
  };
}
