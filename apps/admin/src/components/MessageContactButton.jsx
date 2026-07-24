import { useState } from 'react'
import { CircularProgress, IconButton, Tooltip } from '@mui/material'
import SmsOutlinedIcon from '@mui/icons-material/SmsOutlined'

import { startSmsConversation } from '../services/api'
import SmsComposerDialog from './SmsComposerDialog'

// Shared "Message" action (Phase 8). A single icon button that:
//   1. client-side pre-checks eligibility from data the surface already has, to
//      render a DISABLED icon + tooltip for ineligible contacts (no phone / no
//      consent / opted out) without a network round-trip;
//   2. on click, asks the SERVER (authoritative) to create/reuse the SMS
//      conversation, then opens the shared composer.
//
// The server enforces eligibility on the actual send, so the client pre-check is
// only a UX affordance — it can never let an ineligible send through.

// Compute a best-effort eligibility from a contact-summary-shaped object. The
// surface passes whatever it has; missing fields are treated conservatively.
export function precheckEligibility(contact) {
  if (!contact) return { eligible: false, reason: 'no_phone' }
  const phone = contact.phone_e164 || contact.phone
  if (!phone) return { eligible: false, reason: 'no_phone' }
  if (contact.sms_opted_out || contact.sms_opted_out_at) return { eligible: false, reason: 'opted_out' }
  // sms_consent may be a boolean (contact summary) or a timestamp (raw contact).
  const hasConsent = contact.sms_consent === true || Boolean(contact.sms_consent_at)
  if (!hasConsent) return { eligible: false, reason: 'no_consent' }
  return { eligible: true, reason: 'eligible' }
}

const REASON_TOOLTIP = {
  no_phone: 'No phone number on file',
  no_consent: 'No SMS consent — they must opt in or text first',
  opted_out: 'This contact opted out of SMS',
  sms_disabled: 'Outbound SMS is not enabled',
  transport_unavailable: 'SMS is not configured',
  eligible: 'Send a text message',
}

export default function MessageContactButton({
  contactId,
  contact,
  eventId = null,
  size = 'small',
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [convo, setConvo] = useState(null) // { conversation_id, contact }
  const [open, setOpen] = useState(false)

  const pre = precheckEligibility(contact)
  const disabled = !contactId || !pre.eligible

  async function handleClick() {
    if (loading || disabled) return
    setLoading(true)
    setError(null)
    try {
      const res = await startSmsConversation(contactId, eventId)
      setConvo(res)
      setOpen(true)
    } catch (err) {
      setError(err?.response?.data?.detail?.code || 'error')
    } finally {
      setLoading(false)
    }
  }

  const tooltip = error
    ? 'Could not open the conversation'
    : REASON_TOOLTIP[pre.reason] || 'Send a text message'

  return (
    <>
      <Tooltip title={tooltip} arrow>
        {/* span keeps the tooltip working while the button is disabled */}
        <span>
          <IconButton
            size={size}
            color="primary"
            onClick={handleClick}
            disabled={disabled || loading}
            aria-label="Message contact"
          >
            {loading ? <CircularProgress size={16} /> : <SmsOutlinedIcon fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>

      {convo && (
        <SmsComposerDialog
          open={open}
          onClose={() => setOpen(false)}
          conversationId={convo.conversation_id}
          contactName={convo.contact?.display_name || contact?.display_name}
          contactPhone={convo.contact?.phone || contact?.phone_e164 || contact?.phone}
        />
      )}
    </>
  )
}
