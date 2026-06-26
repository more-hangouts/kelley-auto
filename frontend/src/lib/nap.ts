// Resolved business NAP (name / address / phone) for the public site,
// sourced from the FastAPI business profile with neutral Kelley fallbacks.
//
// Source of truth is `getBusinessProfile()`; this helper applies the agreed
// fallbacks so no street address, phone, or hours is ever hardcoded in a
// component (which would go stale). Fill the real values via the admin
// business profile and every surface updates.

import { getBusinessProfile } from "@/lib/publicApi";

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
  /** "City, ST" when available, else null. */
  cityState: string | null;
  /** A location phrase for designs that need one — cityState or "Central Texas". */
  locationLabel: string;
  hasAddress: boolean;
  hoursText: string;
}

const HOURS_FALLBACK =
  "Contact us for current hours and appointment availability";

export async function resolveNap(): Promise<Nap> {
  const p = await getBusinessProfile();

  const phone = p?.phone?.trim() || null;
  const email = p?.email?.trim() || null;
  const a = p?.address;

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

  return {
    name: p?.name?.trim() || "Kelley Autoplex",
    legalName: p?.legalName?.trim() || "Kelley Autoplex",
    phone,
    phoneDisplay: phone || "Call for details",
    telHref: phone ? `tel:+1${phone.replace(/\D/g, "")}` : null,
    email,
    addressLines: lines,
    cityState,
    locationLabel: cityState || "Central Texas",
    hasAddress: lines.length > 0,
    hoursText: HOURS_FALLBACK,
  };
}
