import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import { Link as RouterLink } from 'react-router-dom'
import { getInboxConversation, sendInboxMessage } from '../services/api'

// Shared SMS composer (Phase 8). Opened by MessageContactButton after the
// server has created/reused the conversation. Shows the contact name + masked
// number and recent messages when the thread already exists; sending reuses the
// Inbox send path (quiet-hours confirm + carrier errors) and preserves the
// draft on failure. Does not duplicate Inbox's list — it's a focused thread.

function maskNumber(e164) {
  if (!e164) return ''
  const digits = String(e164).replace(/\D/g, '')
  if (digits.length < 4) return e164
  return `(•••) •••-${digits.slice(-4)}`
}

function sendErrorMessage(err) {
  const d = err?.response?.data?.detail
  if (d?.code === 'recipient_opted_out') return 'This contact opted out — you can’t text them.'
  if (d?.code === 'recipient_no_sms_consent')
    return 'No SMS consent on file — they must opt in, or text you first.'
  if (d?.code === 'recipient_has_no_valid_phone') return 'This contact has no valid phone number.'
  if (d?.code === 'sms_send_failed')
    return d.message ? `Carrier rejected it: ${d.message}` : 'The carrier rejected the message.'
  if (d?.code === 'sms_not_configured') return 'SMS isn’t configured yet.'
  if (d?.code === 'sms_sending_disabled') return 'Outbound SMS is not enabled.'
  return "Couldn't send — try again."
}

export default function SmsComposerDialog({
  open,
  onClose,
  conversationId,
  contactName,
  contactPhone,
}) {
  const [thread, setThread] = useState(null)
  const [loading, setLoading] = useState(false)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)
  const [quietHoursPrompt, setQuietHoursPrompt] = useState(false)
  const draftRef = useRef('')
  draftRef.current = draft

  // Load the thread (recent messages) whenever the dialog opens for a convo.
  // Reset transient send state too, so a stale quiet-hours banner / draft from a
  // previous open (possibly for a different contact) never leaks in.
  useEffect(() => {
    if (!open || !conversationId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setQuietHoursPrompt(false)
    setDraft('')
    getInboxConversation(conversationId)
      .then((data) => {
        if (!cancelled) setThread(data)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load the conversation.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, conversationId])

  async function doSend(allowQuietHours) {
    const text = draftRef.current.trim()
    if (!text || sending) return
    setSending(true)
    setError(null)
    try {
      await sendInboxMessage(conversationId, text, allowQuietHours)
      setDraft('') // clear only on success
      setQuietHoursPrompt(false)
      // Refresh THIS thread so the outbound row appears immediately. The Inbox
      // page polls on its own interval (it isn't a react-query consumer), so
      // there's no cache to invalidate here — it reconciles on its next poll.
      const refreshed = await getInboxConversation(conversationId).catch(() => null)
      if (refreshed) setThread(refreshed)
    } catch (err) {
      if (err?.response?.data?.detail?.code === 'quiet_hours') {
        setQuietHoursPrompt(true) // offer "send anyway"; draft preserved
      } else {
        setError(sendErrorMessage(err)) // draft preserved
      }
    } finally {
      setSending(false)
    }
  }

  const messages = Array.isArray(thread?.messages) ? thread.messages : []
  const replyEnabled = thread ? thread.reply_enabled !== false : true

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ pr: 6 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }} noWrap>
          {contactName || 'Message'}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {maskNumber(contactPhone)}
        </Typography>
        <IconButton onClick={onClose} sx={{ position: 'absolute', right: 8, top: 8 }} size="small">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={22} />
          </Box>
        ) : (
          <Stack spacing={1}>
            {messages.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                No messages yet. Your first text starts the conversation.
              </Typography>
            ) : (
              messages.slice(-12).map((m) => (
                <Box
                  key={m.id}
                  sx={{
                    alignSelf: m.direction === 'outbound' ? 'flex-end' : 'flex-start',
                    maxWidth: '80%',
                    bgcolor: m.direction === 'outbound' ? 'primary.main' : 'action.hover',
                    color: m.direction === 'outbound' ? 'common.white' : 'text.primary',
                    px: 1.5,
                    py: 0.75,
                    borderRadius: 2,
                  }}
                >
                  <Typography variant="body2">{m.body}</Typography>
                </Box>
              ))
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogContent sx={{ pt: 1.5 }}>
        {error && (
          <Alert severity="warning" sx={{ mb: 1 }}>
            {error}
          </Alert>
        )}
        {quietHoursPrompt && (
          <Alert
            severity="info"
            sx={{ mb: 1 }}
            action={
              <Button color="inherit" size="small" onClick={() => doSend(true)} disabled={sending}>
                Send anyway
              </Button>
            }
          >
            It’s quiet hours for this customer.
          </Alert>
        )}
        <TextField
          fullWidth
          multiline
          minRows={2}
          maxRows={5}
          placeholder={replyEnabled ? 'Type a message…' : 'Messaging is disabled for this contact.'}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={sending || !replyEnabled}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              doSend(false)
            }
          }}
        />
      </DialogContent>
      <Divider />
      <DialogActions sx={{ justifyContent: 'space-between', px: 2 }}>
        <Button
          component={RouterLink}
          to={`/inbox?conversation=${conversationId}`}
          size="small"
          startIcon={<OpenInNewIcon fontSize="small" />}
        >
          Open full conversation
        </Button>
        <Button
          variant="contained"
          onClick={() => doSend(false)}
          disabled={sending || !draft.trim() || !replyEnabled}
          startIcon={sending ? <CircularProgress size={14} /> : null}
        >
          Send
        </Button>
      </DialogActions>
    </Dialog>
  )
}
