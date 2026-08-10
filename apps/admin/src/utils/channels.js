import LanguageIcon from '@mui/icons-material/Language'
import SmsOutlinedIcon from '@mui/icons-material/SmsOutlined'

// Presentation for the omnichannel inbox channels (migration 094 + 097).
// Lives here rather than in Inbox.jsx because the dashboard's new-message
// toast renders the same chip from DashboardLayout — and importing it from
// the page would drag the whole lazy-loaded Inbox chunk into the layout
// bundle, undoing the route splitting from the Phase 4 SPA work.
export const CHANNEL_META = {
  sms: { label: 'SMS', icon: SmsOutlinedIcon, color: '#2563eb' },
  facebook: { label: 'Facebook', icon: SmsOutlinedIcon, color: '#1877f2' },
  instagram: { label: 'Instagram', icon: SmsOutlinedIcon, color: '#c13584' },
  web_chat: { label: 'Web', icon: LanguageIcon, color: '#157A33' },
}

// A channel the server knows about but this build does not still needs to
// render — falling back to the raw value keeps an unknown channel visible
// instead of silently blank.
export function channelMeta(channel) {
  return (
    CHANNEL_META[channel] || {
      label: channel || 'Message',
      icon: SmsOutlinedIcon,
      color: '#6b7280',
    }
  )
}

// Who the message is from, for surfaces that have no room for the full
// conversation header. Unlinked web-chat threads have no name at all —
// "Website visitor" is truer there than an empty string or a raw session id.
export function conversationSender(displayName, channel) {
  if (displayName) return displayName
  return channel === 'web_chat' ? 'Website visitor' : 'Unknown number'
}
