import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import {
  createEventNote,
  patchEventStatus,
  resolveEventNote,
} from '../services/api'

// Extended here too: this dialog uses fromNow() and must not depend on some
// other module having registered the plugin first.
dayjs.extend(relativeTime)

// Quick "call it again on…" presets, in the units a floor actually uses.
// 9am local is the default hour: a reminder is a morning to-do, not a
// timestamp anyone tunes.
const PRESETS = [
  { key: 'tomorrow', label: 'Tomorrow', add: [1, 'day'] },
  { key: 'in3', label: 'In 3 days', add: [3, 'day'] },
  { key: 'week', label: 'Next week', add: [1, 'week'] },
  { key: 'twoweeks', label: 'In 2 weeks', add: [2, 'week'] },
  { key: 'month', label: 'In a month', add: [1, 'month'] },
]

const REMIND_HOUR = 9

// Terminal outcomes. Picking one closes the deal, which is the ONLY way a
// lead leaves the follow-up queue for good — that is deliberate. A deal with
// no next date and no closing reason is exactly how 294 of them ended up in
// an untouched pile.
const OUTCOMES = [
  { key: 'sold', label: 'Sold', status: 'sold' },
  { key: 'lost', label: 'Lost / not interested', status: 'lost' },
]

function presetToIso(preset) {
  const p = PRESETS.find((x) => x.key === preset)
  if (!p) return null
  return dayjs().add(p.add[0], p.add[1]).hour(REMIND_HOUR).minute(0).second(0)
    .millisecond(0)
    .toISOString()
}

function toInputValue(iso) {
  return iso ? dayjs(iso).format('YYYY-MM-DDTHH:mm') : ''
}

/**
 * Work one lead without leaving the queue: write what happened, then say what
 * happens next.
 *
 * The save button stays disabled until the rep has chosen EITHER a next
 * follow-up date OR a terminal outcome. That is the whole point of the
 * dialog — a note on its own leaves the deal in limbo, which is the failure
 * mode this queue exists to fix. The rule is enforced here in the UI only;
 * the notes and status APIs stay permissive so the storefront, walk-in and
 * phone-lead intake paths (which create deals with no rep present) keep
 * working.
 */
export default function LogFollowUpDialog({ open, item, onClose }) {
  const queryClient = useQueryClient()
  const [body, setBody] = useState('')
  const [preset, setPreset] = useState(null)
  const [customAt, setCustomAt] = useState('')
  const [outcome, setOutcome] = useState(null)
  const [error, setError] = useState(null)

  // Reset every time a different lead is opened — a stale draft from the
  // previous customer landing on this one would be a real data error.
  useEffect(() => {
    if (open) {
      setBody('')
      setPreset(null)
      setCustomAt('')
      setOutcome(null)
      setError(null)
    }
  }, [open, item?.event_id])

  const nextRemindAt = useMemo(() => {
    if (outcome) return null
    if (customAt) return dayjs(customAt).toISOString()
    return presetToIso(preset)
  }, [preset, customAt, outcome])

  const canSave = Boolean(body.trim()) && Boolean(nextRemindAt || outcome)

  const save = useMutation({
    mutationFn: async () => {
      const eventId = item.event_id
      // 1. The note — always written, and it carries the new reminder so the
      //    "why" and the "when" live on the same row (migration 100's model).
      await createEventNote(eventId, {
        body: body.trim(),
        remind_at: nextRemindAt,
      })
      // 2. Retire the reminder that put this lead in the queue. Without this
      //    the old date keeps the deal in Overdue forever alongside its
      //    replacement.
      if (item.reminder_note_id) {
        await resolveEventNote(eventId, item.reminder_note_id, true)
      }
      // 3. Terminal outcome closes the deal, which drops it from the queue
      //    (the queue only ever contains non-terminal deals).
      const chosen = OUTCOMES.find((o) => o.key === outcome)
      if (chosen) {
        await patchEventStatus(eventId, chosen.status)
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['events', 'follow-ups'] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      if (item?.event_id != null) {
        queryClient.invalidateQueries({ queryKey: ['event', item.event_id] })
      }
      onClose()
    },
    onError: (err) => {
      setError(
        err?.response?.data?.detail || err.message || 'Could not save the follow-up.',
      )
    },
  })

  if (!item) return null

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" component="div">
          {item.contact_name || 'Customer'}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {item.vehicle_label || 'No vehicle linked'}
          {item.contact_phone ? ` · ${item.contact_phone}` : ''}
        </Typography>
      </DialogTitle>

      <DialogContent dividers>
        <Stack spacing={2.5}>
          {item.last_note_body && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                Last note
                {item.last_note_author ? ` · ${item.last_note_author}` : ''}
                {item.last_note_at ? ` · ${dayjs(item.last_note_at).fromNow()}` : ''}
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  mt: 0.5,
                  p: 1.25,
                  borderRadius: 1,
                  bgcolor: 'action.hover',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {item.last_note_body}
              </Typography>
            </Box>
          )}

          <TextField
            autoFocus
            label="What happened?"
            placeholder="Left voicemail, texted about the Challenger…"
            multiline
            minRows={2}
            fullWidth
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />

          <Divider />

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Next follow-up
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={1}>
              {PRESETS.map((p) => (
                <Chip
                  key={p.key}
                  label={p.label}
                  clickable
                  color={preset === p.key && !outcome ? 'primary' : 'default'}
                  variant={preset === p.key && !outcome ? 'filled' : 'outlined'}
                  onClick={() => {
                    setPreset(preset === p.key ? null : p.key)
                    setCustomAt('')
                    setOutcome(null)
                  }}
                />
              ))}
            </Stack>
            <TextField
              type="datetime-local"
              label="Or pick a date"
              size="small"
              sx={{ mt: 1.5 }}
              InputLabelProps={{ shrink: true }}
              value={customAt || (preset && !outcome ? toInputValue(nextRemindAt) : '')}
              onChange={(e) => {
                setCustomAt(e.target.value)
                setPreset(null)
                setOutcome(null)
              }}
            />
          </Box>

          <Box>
            <Typography variant="subtitle2" gutterBottom>
              …or close it out
            </Typography>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={outcome}
              onChange={(_e, v) => {
                setOutcome(v)
                if (v) {
                  setPreset(null)
                  setCustomAt('')
                }
              }}
            >
              {OUTCOMES.map((o) => (
                <ToggleButton key={o.key} value={o.key}>
                  {o.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>

          {!canSave && body.trim() && (
            <Alert severity="info">
              Pick a next follow-up date, or mark the deal sold or lost. Every
              lead leaves here with a decision.
            </Alert>
          )}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => save.mutate()}
          disabled={!canSave || save.isPending}
        >
          {save.isPending
            ? 'Saving…'
            : outcome
              ? 'Save & close deal'
              : 'Save & schedule'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
