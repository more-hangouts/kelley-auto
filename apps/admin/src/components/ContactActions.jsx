import { Stack } from '@mui/material'

import CallContact from './CallContact'
import MessageContactButton from './MessageContactButton'

// Shared contact action row (Phase 8): Call + Message as familiar icon actions
// beside a contact identity. Both are icon-only for stable, compact dimensions
// on mobile; each handles its own eligibility (a disabled icon + tooltip when a
// contact can't be called/messaged). Reused everywhere a contact appears so no
// surface re-implements the call/message logic.
//
// Props:
//   contact  — a contact-summary-shaped object: { id, display_name, phone,
//              phone_e164, sms_consent|sms_consent_at, sms_opted_out|sms_opted_out_at }
//   eventId  — optional originating deal id (links a NEW sms thread for context)
//   source   — screen label for call-attempt logging (e.g. 'contact_detail')
//   showCall / showMessage — toggle either action off for a surface
export default function ContactActions({
  contact,
  eventId = null,
  source,
  showCall = true,
  showMessage = true,
  spacing = 0.5,
}) {
  if (!contact) return null
  const phone = contact.phone_e164 || contact.phone
  return (
    <Stack direction="row" spacing={spacing} alignItems="center">
      {showCall && phone && (
        <CallContact
          variant="icon"
          contactId={contact.id}
          phone={contact.phone}
          phoneE164={contact.phone_e164}
          eventId={eventId}
          source={source}
        />
      )}
      {showMessage && (
        <MessageContactButton contactId={contact.id} contact={contact} eventId={eventId} />
      )}
    </Stack>
  )
}
