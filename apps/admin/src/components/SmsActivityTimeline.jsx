import { Box, Chip, Divider, Paper, Stack, Typography } from '@mui/material'
import SmsOutlinedIcon from '@mui/icons-material/SmsOutlined'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { getContactMessages, getEventMessages } from '../services/api'

// Read-only SMS activity timeline (Phase 8). Surfaces the CANONICAL
// ConversationMessage rows into the contact / deal activity views — no second
// synthetic row is ever written. Pass either contactId or eventId.

export default function SmsActivityTimeline({ contactId = null, eventId = null }) {
  const enabled = contactId != null || eventId != null
  const { data, isLoading } = useQuery({
    queryKey: contactId != null
      ? ['contact-sms-activity', contactId]
      : ['event-sms-activity', eventId],
    queryFn: () =>
      contactId != null ? getContactMessages(contactId) : getEventMessages(eventId),
    enabled,
  })

  const messages = Array.isArray(data?.messages) ? data.messages : []
  if (!isLoading && messages.length === 0) return null // nothing to show — stay quiet

  return (
    <Paper variant="outlined" sx={{ borderRadius: 2, mt: 3 }}>
      <Box sx={{ px: 3, py: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          Text messages
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {isLoading ? 'Loading…' : `${messages.length} SMS message${messages.length === 1 ? '' : 's'}`}
        </Typography>
      </Box>
      {messages.length > 0 && (
        <Stack divider={<Divider />}>
          {messages.map((m) => (
            <Box key={m.id} sx={{ px: 3, py: 1.5, display: 'flex', alignItems: 'flex-start', gap: 2 }}>
              <SmsOutlinedIcon fontSize="small" sx={{ color: 'text.secondary', mt: 0.25 }} />
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="body2">{m.body}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {m.created_at ? dayjs(m.created_at).format('MMM D, YYYY h:mm A') : ''}
                </Typography>
              </Box>
              <Chip
                size="small"
                variant="outlined"
                color={m.direction === 'outbound' ? 'primary' : 'default'}
                label={m.direction === 'outbound' ? 'Sent' : 'Received'}
              />
            </Box>
          ))}
        </Stack>
      )}
    </Paper>
  )
}
