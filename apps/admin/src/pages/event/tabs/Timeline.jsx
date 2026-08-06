import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  Alert,
  AlertTitle,
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AlarmOnOutlinedIcon from '@mui/icons-material/AlarmOnOutlined'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import LanguageIcon from '@mui/icons-material/Language'
import NotesOutlinedIcon from '@mui/icons-material/NotesOutlined'
import NotificationsActiveOutlinedIcon from '@mui/icons-material/NotificationsActiveOutlined'
import NotificationsOffOutlinedIcon from '@mui/icons-material/NotificationsOffOutlined'
import PhoneInTalkIcon from '@mui/icons-material/PhoneInTalk'
import SettingsIcon from '@mui/icons-material/Settings'
import SmsOutlinedIcon from '@mui/icons-material/SmsOutlined'
import SwapHorizIcon from '@mui/icons-material/SwapHoriz'
import UndoOutlinedIcon from '@mui/icons-material/UndoOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import {
  createEventNote,
  deleteEventNote,
  getDealTimeline,
  resolveEventNote,
  updateEventNote,
} from '../../../services/api'

dayjs.extend(relativeTime)

// Quick-pick follow-up offsets. A rep saying "call them back Thursday"
// wants two taps, not a date picker.
const QUICK_REMINDERS = [
  { label: 'Tomorrow', at: () => dayjs().add(1, 'day').hour(9).minute(0) },
  { label: 'In 2 days', at: () => dayjs().add(2, 'day').hour(9).minute(0) },
  { label: 'Next week', at: () => dayjs().add(7, 'day').hour(9).minute(0) },
]

const INPUT_FORMAT = 'YYYY-MM-DDTHH:mm'

const toInputValue = (v) => (v ? dayjs(v).format(INPUT_FORMAT) : '')

function describeError(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return 'Something went wrong. Try again.'
}

// Salesperson-reported call outcomes, in the words a rep would say out
// loud. Mirrors CALL_OUTCOMES in services/call_attempts.py.
const CALL_OUTCOME_PHRASE = {
  call_initiated: 'Dialed',
  connected: 'Connected',
  voicemail: 'Left a voicemail',
  no_answer: 'No answer',
  busy: 'Line was busy',
  wrong_number: 'Wrong number',
  failed: 'Call failed',
  cancelled: 'Call cancelled',
}

const CHANNEL_WORD = {
  sms: 'texted',
  web_chat: 'messaged on web chat',
  facebook: 'messaged on Facebook',
  instagram: 'messaged on Instagram',
}

// Everything a rep should see, said the way a person would say it. Keys
// are activity_type from activity_log; anything not listed falls back to
// a de-underscored version of the type rather than disappearing.
const ACTIVITY_PHRASE = {
  'lead.public_submitted': () => 'Submitted a lead on the website',
  'lead.notification_sent': () => 'We were emailed about the new lead',
  'lead.notification_failed': () => 'Lead alert email failed to send',
  'lead.confirmation_sent': () => 'Customer was emailed a confirmation',
  'lead.confirmation_failed': () => 'Customer confirmation email failed',
  'call.initiated': (i) => `${i.actor_name || 'Someone'} called`,
  'appointment.arrived': () => 'Customer arrived for their appointment',
  'call.outcome_recorded': (i) =>
    `Call result: ${CALL_OUTCOME_PHRASE[i.payload?.outcome] || i.payload?.outcome || 'recorded'}`,
  'event.status_changed': (i) => {
    const to = (i.payload?.to_status || '').replace(/_/g, ' ')
    const from = (i.payload?.from_status || '').replace(/_/g, ' ')
    if (!from) return `Deal started in ${to || 'the first column'}`
    return `Moved from ${from} to ${to}`
  },
  'event.walk_in_created': (i) =>
    `${i.actor_name || 'Someone'} logged this walk-in`,
  'event.archived': () => 'Deal archived',
  'event.restored': () => 'Deal restored',
  'event.reassigned': (i) =>
    `Deal reassigned${i.payload?.to_display_name ? ` to ${i.payload.to_display_name}` : ''}`,
  'appointment.cancelled': () => 'Appointment cancelled',
}

function activityPhrase(item) {
  const fn = ACTIVITY_PHRASE[item.subtype]
  if (fn) {
    try {
      return fn(item)
    } catch {
      /* fall through */
    }
  }
  return item.subtype.replace(/[._]/g, ' ')
}

/** Icon + tint per row kind, so the shape of the day is scannable. */
function visualFor(item) {
  if (item.kind === 'note') {
    return item.payload?.imported
      ? { Icon: LanguageIcon, bg: 'grey.200', fg: 'text.secondary' }
      : { Icon: NotesOutlinedIcon, bg: 'primary.main', fg: 'primary.contrastText' }
  }
  if (item.kind === 'message') {
    return item.subtype === 'inbound'
      ? { Icon: SmsOutlinedIcon, bg: 'secondary.main', fg: 'secondary.contrastText' }
      : { Icon: SmsOutlinedIcon, bg: 'primary.light', fg: 'primary.contrastText' }
  }
  if (item.subtype?.startsWith('call.')) {
    return { Icon: PhoneInTalkIcon, bg: 'success.light', fg: 'success.contrastText' }
  }
  if (item.subtype === 'lead.public_submitted') {
    return { Icon: LanguageIcon, bg: 'info.light', fg: 'info.contrastText' }
  }
  if (item.subtype === 'event.status_changed') {
    return { Icon: SwapHorizIcon, bg: 'grey.400', fg: 'common.white' }
  }
  return { Icon: SettingsIcon, bg: 'grey.300', fg: 'text.secondary' }
}

/** The one-line headline for a row. */
function headlineFor(item, customerName) {
  const who = customerName || 'The customer'
  if (item.kind === 'note') {
    return item.payload?.imported
      ? 'Website lead'
      : `${item.actor_name || 'Staff'} added a note`
  }
  if (item.kind === 'message') {
    const verb = CHANNEL_WORD[item.payload?.channel] || 'messaged'
    return item.subtype === 'inbound' ? `${who} ${verb} us` : `We ${verb} ${who}`
  }
  return activityPhrase(item)
}

/** Reminder state line under a note. */
function ReminderChip({ note }) {
  const remindAt = note.payload?.remind_at
  if (!remindAt) return null
  const when = dayjs(remindAt)
  if (note.payload?.resolved_at) {
    return (
      <Chip
        size="small"
        variant="outlined"
        icon={<CheckCircleOutlineIcon />}
        label={`Handled · was due ${when.format('MMM D, h:mm A')}`}
      />
    )
  }
  const overdue = when.isBefore(dayjs())
  return (
    <Chip
      size="small"
      color={overdue ? 'warning' : 'default'}
      variant={overdue ? 'filled' : 'outlined'}
      icon={overdue ? <AlarmOnOutlinedIcon /> : <NotificationsActiveOutlinedIcon />}
      label={
        (overdue ? 'Follow-up was due ' : 'Follow-up ') +
        when.format('ddd MMM D, h:mm A') +
        (note.payload?.reminder_sent_at ? ' · emailed' : '')
      }
    />
  )
}

/** Add a note, optionally with a reminder. */
function Composer({ eventId, onSaved }) {
  const [body, setBody] = useState('')
  const [remindAt, setRemindAt] = useState('')
  const [error, setError] = useState(null)

  const save = useMutation({
    mutationFn: () =>
      createEventNote(eventId, {
        body: body.trim(),
        remind_at: remindAt ? new Date(remindAt).toISOString() : null,
      }),
    onSuccess: () => {
      setBody('')
      setRemindAt('')
      setError(null)
      onSaved()
    },
    onError: (err) => setError(describeError(err)),
  })

  const canSave = body.trim().length > 0 && !save.isPending

  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
      <TextField
        label="What happened?"
        placeholder="Called again, asked to call back Thursday"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        multiline
        minRows={2}
        fullWidth
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canSave) save.mutate()
        }}
      />

      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={1.5}
        alignItems={{ sm: 'center' }}
        sx={{ mt: 2 }}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography variant="body2" color="text.secondary">
          Remind me:
        </Typography>
        {QUICK_REMINDERS.map((q) => {
          const value = q.at().format(INPUT_FORMAT)
          const active = remindAt === value
          return (
            <Button
              key={q.label}
              size="small"
              variant={active ? 'contained' : 'outlined'}
              onClick={() => setRemindAt(active ? '' : value)}
            >
              {q.label}
            </Button>
          )
        })}
        <TextField
          size="small"
          type="datetime-local"
          label="Or pick a time"
          value={remindAt}
          onChange={(e) => setRemindAt(e.target.value)}
          InputLabelProps={{ shrink: true }}
          sx={{ minWidth: 230 }}
        />
        {remindAt && (
          <Tooltip title="Clear reminder">
            <IconButton size="small" onClick={() => setRemindAt('')}>
              <NotificationsOffOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </Stack>

      {remindAt && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
          We'll email you {dayjs(remindAt).format('dddd, MMM D [at] h:mm A')}.
        </Typography>
      )}

      {error && (
        <Alert severity="error" sx={{ mt: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2 }}>
        <Button variant="contained" disabled={!canSave} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Add to timeline'}
        </Button>
      </Stack>
    </Paper>
  )
}

/** One row of the story. Notes are editable in place; everything else is
    a record of something that happened and is read-only. */
function TimelineRow({ eventId, item, customerName, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(item.body || '')
  const [draftRemindAt, setDraftRemindAt] = useState(toInputValue(item.payload?.remind_at))
  const [error, setError] = useState(null)

  const editable = item.kind === 'note' && !item.payload?.imported
  const { Icon, bg, fg } = visualFor(item)
  const openFollowUp = item.payload?.remind_at && !item.payload?.resolved_at

  const save = useMutation({
    mutationFn: () =>
      updateEventNote(eventId, item.id, {
        body: draft.trim(),
        remind_at: draftRemindAt ? new Date(draftRemindAt).toISOString() : null,
        clear_reminder: !!item.payload?.remind_at && !draftRemindAt,
      }),
    onSuccess: () => {
      setEditing(false)
      setError(null)
      onChanged()
    },
    onError: (err) => setError(describeError(err)),
  })

  const resolve = useMutation({
    mutationFn: (resolved) => resolveEventNote(eventId, item.id, resolved),
    onSuccess: onChanged,
    onError: (err) => setError(describeError(err)),
  })

  const remove = useMutation({
    mutationFn: () => deleteEventNote(eventId, item.id),
    onSuccess: onChanged,
    onError: (err) => setError(describeError(err)),
  })

  return (
    <Stack direction="row" spacing={2} sx={{ position: 'relative', pb: 3 }}>
      {/* The connecting spine. Absolute so rows of any height stay joined. */}
      <Box
        sx={{
          position: 'absolute',
          left: 15,
          top: 32,
          bottom: 0,
          width: '2px',
          bgcolor: 'divider',
        }}
      />
      <Avatar sx={{ width: 32, height: 32, bgcolor: bg, color: fg, zIndex: 1 }}>
        <Icon sx={{ fontSize: 18 }} />
      </Avatar>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap">
          <Typography variant="subtitle2">
            {headlineFor(item, customerName)}
          </Typography>
          <Tooltip title={dayjs(item.at).format('dddd, MMM D YYYY h:mm A')}>
            <Typography variant="caption" color="text.secondary">
              {dayjs(item.at).format('MMM D, h:mm A')}
            </Typography>
          </Tooltip>
          {item.payload?.edited_at && (
            <Typography variant="caption" color="text.secondary">
              · edited
            </Typography>
          )}
          {item.payload?.failed && (
            <Chip size="small" color="error" variant="outlined" label="not delivered" />
          )}
        </Stack>

        {editing ? (
          <Box sx={{ mt: 1 }}>
            <TextField
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              multiline
              minRows={2}
              fullWidth
              autoFocus
            />
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={1.5}
              alignItems={{ sm: 'center' }}
              sx={{ mt: 1.5 }}
            >
              <TextField
                size="small"
                type="datetime-local"
                label="Reminder"
                value={draftRemindAt}
                onChange={(e) => setDraftRemindAt(e.target.value)}
                InputLabelProps={{ shrink: true }}
                sx={{ minWidth: 230 }}
              />
              {draftRemindAt && (
                <Button size="small" onClick={() => setDraftRemindAt('')}>
                  Clear reminder
                </Button>
              )}
            </Stack>
            <Stack direction="row" spacing={1} justifyContent="flex-end" sx={{ mt: 1.5 }}>
              <Button
                size="small"
                onClick={() => {
                  setEditing(false)
                  setDraft(item.body || '')
                  setDraftRemindAt(toInputValue(item.payload?.remind_at))
                  setError(null)
                }}
              >
                Cancel
              </Button>
              <Button
                size="small"
                variant="contained"
                disabled={!draft.trim() || save.isPending}
                onClick={() => save.mutate()}
              >
                {save.isPending ? 'Saving…' : 'Save'}
              </Button>
            </Stack>
          </Box>
        ) : (
          item.body && (
            <Typography
              variant="body2"
              sx={{
                mt: 0.5,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                ...(item.kind === 'message' || item.payload?.imported
                  ? {
                      // Quoted speech — theirs or ours — reads as a quote.
                      pl: 1.5,
                      borderLeft: '3px solid',
                      borderColor: 'divider',
                      color: 'text.primary',
                    }
                  : {}),
              }}
            >
              {item.body}
            </Typography>
          )
        )}

        {!editing && item.payload?.remind_at && (
          <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
            <ReminderChip note={item} />
            <Button
              size="small"
              startIcon={openFollowUp ? <CheckCircleOutlineIcon /> : <UndoOutlinedIcon />}
              onClick={() => resolve.mutate(!!openFollowUp)}
              disabled={resolve.isPending}
            >
              {openFollowUp ? 'Mark handled' : 'Reopen'}
            </Button>
          </Stack>
        )}

        {error && (
          <Alert severity="error" sx={{ mt: 1.5 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
      </Box>

      {editable && !editing && (
        <Stack direction="row">
          <Tooltip title="Edit note">
            <IconButton size="small" onClick={() => setEditing(true)}>
              <EditOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Delete note">
            <IconButton size="small" onClick={() => remove.mutate()} disabled={remove.isPending}>
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      )}
    </Stack>
  )
}

export default function Timeline() {
  const { event } = useOutletContext()
  const eventId = event.id
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ['event', eventId, 'timeline'],
    queryFn: () => getDealTimeline(eventId),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'timeline'] })
    queryClient.invalidateQueries({ queryKey: ['event', eventId, 'document-counts'] })
  }

  const summary = data?.summary
  const items = data?.items || []

  return (
    <Box>
      {/* Anything worth acting on, before the story. */}
      {(summary?.flags || []).map((flag) => (
        <Alert key={flag.code} severity={flag.severity || 'warning'} sx={{ mb: 2 }}>
          <AlertTitle>{flag.label}</AlertTitle>
          {flag.detail}
        </Alert>
      ))}

      <Composer eventId={eventId} onSaved={refresh} />

      {isLoading ? (
        <Box sx={{ p: 4, textAlign: 'center' }}>
          <CircularProgress size={20} />
        </Box>
      ) : error ? (
        <Alert severity="error">Couldn't load this deal's timeline.</Alert>
      ) : !items.length ? (
        <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
          Nothing has happened on this deal yet. Calls, texts, and notes will
          show up here in order.
        </Typography>
      ) : (
        <>
          {data?.truncated && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Showing the most recent {items.length} events on this deal.
            </Alert>
          )}
          {items.map((item) => (
            <TimelineRow
              key={`${item.kind}-${item.id}`}
              eventId={eventId}
              item={item}
              customerName={summary?.customer_name}
              onChanged={refresh}
            />
          ))}
        </>
      )}
    </Box>
  )
}
