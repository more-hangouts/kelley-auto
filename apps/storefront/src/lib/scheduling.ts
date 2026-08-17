// The number customers are told to call or text to SET UP a visit or test
// drive.
//
// Deliberately NOT the business-profile NAP phone. This is the Twilio
// number, which supports SMS as well as voice and forwards inbound calls to
// the office line — so a "call or text us to schedule" CTA works on both
// channels and every scheduling contact is logged in the CRM.
//
// The NAP phone stays the dealership's published (210) number everywhere
// else (footer, header, contact card, schema.org) so citations stay
// consistent with Google Business Profile. Only the scheduling / test-drive
// CTA uses the number below.
//
// Hardcoded on purpose rather than read from an env var: NEXT_PUBLIC_* values
// are baked at build time, and this site has already been burned once by a
// stale .env override shipping the wrong value to production.
export const SCHEDULING_PHONE_DISPLAY = "(830) 268-9308";
export const SCHEDULING_PHONE_E164 = "+18302689308";
export const SCHEDULING_TEL_HREF = `tel:${SCHEDULING_PHONE_E164}`;
export const SCHEDULING_SMS_HREF = `sms:${SCHEDULING_PHONE_E164}`;

/** The line shown after a lead form is submitted. */
export const LEAD_RECEIVED_HEADLINE = "We received your request";
export const LEAD_RECEIVED_BODY =
  "Our team will follow up with you shortly.";
export const SCHEDULING_CTA_PREFIX =
  "Ready to schedule a test drive or visit? Call or text us at";
