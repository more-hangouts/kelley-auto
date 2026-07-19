// Thin, safe wrapper around the Meta Pixel's global `fbq`.
//
// The Pixel itself is loaded by <MetaPixel /> (root layout), gated on
// NEXT_PUBLIC_META_PIXEL_ID — so in any environment without a pixel ID these
// helpers are silent no-ops. Like lib/analytics.ts, nothing here may ever
// throw into the UI, and no BHPH application PII (DOB/DL/address) is ever
// passed to Meta from the browser: only vehicle/commerce context. Identity
// matching happens server-side (hashed) via the Conversions API.

declare global {
  interface Window {
    fbq?: (...args: unknown[]) => void;
  }
}

export const META_PIXEL_ID = process.env.NEXT_PUBLIC_META_PIXEL_ID;

type FbqParams = Record<string, string | number | string[] | undefined>;

/**
 * Fire a standard Pixel event. `eventId` is the CAPI dedup id — pass the SAME
 * id the backend sends server-side (see getTrackingContext().event_id) so
 * Meta collapses the browser + server pair into one conversion.
 */
export function fbqTrack(
  event: "PageView" | "ViewContent" | "Lead" | "Contact",
  params?: FbqParams,
  eventId?: string
): void {
  try {
    if (typeof window === "undefined" || !window.fbq) return;
    const clean: FbqParams = {};
    for (const [k, v] of Object.entries(params ?? {})) {
      if (v !== undefined && v !== null && v !== "") clean[k] = v;
    }
    if (eventId) {
      window.fbq("track", event, clean, { eventID: eventId });
    } else {
      window.fbq("track", event, clean);
    }
  } catch {
    /* pixel must never break the page */
  }
}

/** Commerce params for a vehicle, shared by ViewContent and Lead events. */
export function vehicleContentParams(v: {
  listingCode?: string | null;
  vehicleId?: number | string | null;
  year?: string | number | null;
  make?: string | null;
  model?: string | null;
  priceCents?: number | null;
}): FbqParams {
  const id = v.listingCode || (v.vehicleId != null ? String(v.vehicleId) : undefined);
  const name =
    [v.year, v.make, v.model].filter(Boolean).join(" ").trim() || undefined;
  const params: FbqParams = {
    content_type: "vehicle",
    content_ids: id ? [id] : undefined,
    content_name: name,
  };
  if (typeof v.priceCents === "number" && v.priceCents > 0) {
    params.currency = "USD";
    params.value = Math.round(v.priceCents) / 100;
  }
  return params;
}
