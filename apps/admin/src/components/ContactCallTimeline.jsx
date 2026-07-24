import { useState } from 'react'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Paper,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { listCallAttempts, updateCallAttempt } from '../services/api'

// Call-attempt timeline on the contact detail page (Phase 7). Lists this
// contact's native-dialer call attempts, newest first. Uses "Call initiated"
// until a salesperson records an outcome. Pending rows get a "Log outcome"
// button so calls placed on desktop (where tel: never backgrounds the app to
// trigger the mobile outcome sheet) can still be resolved.

const OUTCOME_LABELS = {
  call_initiated: 'Call initiated',
  connected: 'Connected',
  left_voicemail: 'Left voicemail',
  no_answer: 'No answer',
  busy: 'Busy',
  wrong_number: 'Wrong number',
  cancelled: 'Cancelled',
}

const OUTCOME_COLOR = {
  connected: 'success',
  left_voicemail: 'info',
  no_answer: 'default',
  busy: 'warning',
  wrong_number: 'error',
  cancelled: 'default',
  call_initiated: 'default',
}

const REPORTABLE_OUTCOMES = [
  { value: 'connected', label: 'Connected' },
  { value: 'left_voicemail', label: 'Left voicemail' },
  { value: 'no_answer', label: 'No answer' },
  { value: 'busy', label: 'Busy' },
  { value: 'wrong_number', label: 'Wrong number' },
  { value: 'cancelled', label: 'Cancelled before calling' },
]

function labelFor(outcome) {
  // Treat a missing/blank outcome as the pending state rather than a blank chip.
  return OUTCOME_LABELS[outcome] || OUTCOME_LABELS.call_initiated
}

function colorFor(outcome) {
  return OUTCOME_COLOR[outcome] || 'default'
}

export default function ContactCallTimeline({ contactId }) {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['contact-call-attempts', contactId],
    queryFn: () => listCallAttempts(contactId),
    enabled: contactId != null,
  })

  const [editing, setEditing] = useState(null) // the attempt being resolved
  const [outcome, setOutcome] = useState(null)
  const [notes, setNotes] = useState('')

  const mutation = useMutation({
    mutationFn: ({ attemptId, patch }) =>
      updateCallAttempt(contactId, attemptId, patch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-call-attempts', contactId] })
      closeDialog()
    },
  })

  function openDialog(attempt) {
    setEditing(attempt)
    setOutcome(null)
    setNotes('')
  }
  function closeDialog() {
    setEditing(null)
  }
  function save() {
    if (!editing || (!outcome && !notes.trim())) return
    mutation.mutate({
      attemptId: editing.id,
      patch: {
        ...(outcome ? { outcome } : {}),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      },
    })
  }

  const attempts = Array.isArray(data?.call_attempts) ? data.call_attempts : []

  return (
    <Paper variant="outlined" sx={{ borderRadius: 2, mt: 3 }}>
      <Box sx={{ px: 3, py: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
          Call activity
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {isLoading
            ? 'Loading…'
            : attempts.length === 0
              ? 'No calls logged yet.'
              : `${attempts.length} call${attempts.length === 1 ? '' : 's'} logged`}
        </Typography>
      </Box>
      {attempts.length > 0 && (
        <Stack divider={<Divider />}>
          {attempts.map((a) => (
            <Box
              key={a.id}
              sx={{ px: 3, py: 1.75, display: 'flex', alignItems: 'center', gap: 2 }}
            >
              <PhoneOutlinedIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                  {a.salesperson_display_name || 'Unknown rep'}
                  {a.phone_e164 ? ` · ${a.phone_e164}` : ''}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {a.created_at ? dayjs(a.created_at).format('MMM D, YYYY h:mm A') : ''}
                  {a.notes ? ` — ${a.notes}` : ''}
                </Typography>
              </Box>
              {a.outcome_pending && (
                <Button size="small" onClick={() => openDialog(a)}>
                  Log outcome
                </Button>
              )}
              <Chip
                size="small"
                variant="outlined"
                color={colorFor(a.outcome)}
                label={labelFor(a.outcome)}
              />
            </Box>
          ))}
        </Stack>
      )}

      <Dialog open={editing != null} onClose={closeDialog} fullWidth maxWidth="xs">
        <DialogTitle>How did the call go?</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            <ToggleButtonGroup
              orientation="vertical"
              exclusive
              value={outcome}
              onChange={(_e, val) => val && setOutcome(val)}
              fullWidth
            >
              {REPORTABLE_OUTCOMES.map((o) => (
                <ToggleButton key={o.value} value={o.value} sx={{ justifyContent: 'flex-start' }}>
                  {o.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            <TextField
              label="Notes (optional)"
              multiline
              minRows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={save}
            disabled={mutation.isPending || (!outcome && !notes.trim())}
            startIcon={mutation.isPending ? <CircularProgress size={14} /> : null}
          >
            Save outcome
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}
