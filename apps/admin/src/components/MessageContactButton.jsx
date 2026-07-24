import { useState } from 'react'
import { CircularProgress, IconButton, Tooltip } from '@mui/material'
import SmsOutlinedIcon from '@mui/icons-material/SmsOutlined'

import { startSmsConversation } from '../services/api'
import { precheckEligibility, SMS_REASON_TOOLTIP } from '../utils/smsEligibility'
import SmsComposerDialog from './SmsComposerDialog'

// Shared "Message" action (Phase 8). A single icon button that:
//   1. client-side pre-checks eligibility (see utils/smsEligibility) from data
//      the surface already has, to render a DISABLED icon + tooltip for
//      ineligible contacts (no phone / no consent / opted out) without a
//      network round-trip;
//   2. on click, asks the SERVER (authoritative) to create/reuse the SMS
//      conversation, then opens the shared composer.
//
// The server enforces eligibility on the actual send, so the client pre-check is
// only a UX affordance — it can never let an ineligible send through.

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
    : SMS_REASON_TOOLTIP[pre.reason] || 'Send a text message'

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
