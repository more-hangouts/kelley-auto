// Client-side SMS eligibility pre-check (Phase 8). A UX affordance only — it
// renders the disabled/enabled state of a Message action from data the surface
// already has, so no network round-trip is needed to know a contact can't be
// texted. The SERVER remains authoritative on the actual send (sms_eligibility
// + the send-path guards), so this can never let an ineligible send through.
//
// Accepts a contact-summary-shaped object; missing fields are treated
// conservatively. sms_consent may be a boolean (contact summary) or a timestamp
// (raw contact); sms_opted_out likewise.
export function precheckEligibility(contact) {
  if (!contact) return { eligible: false, reason: 'no_phone' }
  const phone = contact.phone_e164 || contact.phone
  if (!phone) return { eligible: false, reason: 'no_phone' }
  if (contact.sms_opted_out || contact.sms_opted_out_at) {
    return { eligible: false, reason: 'opted_out' }
  }
  const hasConsent = contact.sms_consent === true || Boolean(contact.sms_consent_at)
  if (!hasConsent) return { eligible: false, reason: 'no_consent' }
  return { eligible: true, reason: 'eligible' }
}

export const SMS_REASON_TOOLTIP = {
  no_phone: 'No phone number on file',
  no_consent: 'No SMS consent — they must opt in or text first',
  opted_out: 'This contact opted out of SMS',
  sms_disabled: 'Outbound SMS is not enabled',
  transport_unavailable: 'SMS is not configured',
  eligible: 'Send a text message',
}
