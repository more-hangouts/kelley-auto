import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  Step,
  StepLabel,
  Stepper,
  TextField,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { addEventParticipant } from '../services/api'

const STEPS = ['Parent', 'Celebrant', 'Contact']

// PartySizeBucket vocabulary mirrors the backend Pydantic Literal in
// `api/routers/event_participants.py`. The legacy values
// (`solo` / `2_3` / `4_plus`) are intentionally absent — the booking
// widget no longer emits them, and Phase 6 prunes them so new
// participant rows do not collect drift values.
const PARTY_BUCKETS = [
  { value: 'pair', label: 'Parent + celebrant' },
  { value: '3_4', label: '3-4 of us' },
  { value: '5_plus', label: '5 or more' },
]

const INITIAL_FORM = {
  parent_first_name: '',
  parent_last_name: '',
  celebrant_first_name: '',
  celebrant_last_name: '',
  phone: '',
  email: '',
  party_size_bucket: '',
  role: 'other',
}

const ROLE_OPTIONS = [
  { value: 'quinceanera', label: 'Quinceañera' },
  { value: 'dama', label: 'Court — dama' },
  { value: 'chambelan', label: 'Court — chambelán' },
  { value: 'parent', label: 'Parent' },
  { value: 'other', label: 'Other' },
]

/**
 * Shared add-participant dialog. Mounted from the admin event Overview
 * AND the sales appointment detail; both surfaces hit the canonical
 * POST /api/events/{event_id}/participants endpoint.
 *
 * Contract:
 *   - `eventId`: int, required when `open` is true.
 *   - `open` / `onClose`: standard MUI dialog control.
 *   - `onAdded(payload)`: optional callback fired with the response
 *     after a successful add. Surfaces use this to refresh their
 *     local participant list / activity log queries.
 */
export default function AddParticipantDialog({ eventId, open, onClose, onAdded }) {
  const queryClient = useQueryClient()
  const [activeStep, setActiveStep] = useState(0)
  const [form, setForm] = useState(INITIAL_FORM)

  useEffect(() => {
    if (open) {
      setForm(INITIAL_FORM)
      setActiveStep(0)
    }
  }, [open])

  const buildPayload = () => ({
    parent_first_name: form.parent_first_name.trim(),
    parent_last_name: form.parent_last_name.trim() || null,
    celebrant_first_name: form.celebrant_first_name.trim(),
    celebrant_last_name: form.celebrant_last_name.trim() || null,
    phone: form.phone.trim(),
    email: form.email.trim() || null,
    party_size_bucket: form.party_size_bucket || null,
    role: form.role,
  })

  const mutation = useMutation({
    mutationFn: (payload) => addEventParticipant(eventId, payload),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['event', eventId] })
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'activity'] })
      if (onAdded) onAdded(created)
      onClose()
    },
  })

  const setField = (field) => (e) => {
    setForm((current) => ({ ...current, [field]: e.target.value }))
  }

  const handleClose = () => {
    if (mutation.isPending) return
    onClose()
  }

  const canContinue =
    activeStep === 0
      ? !!form.parent_first_name.trim()
      : activeStep === 1
        ? !!form.celebrant_first_name.trim()
        : !!form.phone.trim() && !!form.party_size_bucket

  const errorMessage = (() => {
    if (!mutation.isError) return null
    const detail = mutation.error?.response?.data?.detail
    if (detail === 'phone_invalid')
      return "That phone number doesn't look like a real phone."
    if (detail === 'event_not_found') return 'Event not found.'
    return mutation.error?.message || 'Could not add participant.'
  })()

  return (
    <Dialog open={open} onClose={handleClose} fullWidth maxWidth="sm">
      <DialogTitle>Add participant</DialogTitle>
      <DialogContent>
        <Stepper activeStep={activeStep} sx={{ mb: 3 }}>
          {STEPS.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {activeStep === 0 && (
          <Stack spacing={2}>
            <TextField
              label="Parent first name"
              value={form.parent_first_name}
              onChange={setField('parent_first_name')}
              autoFocus
              required
            />
            <TextField
              label="Parent last name"
              value={form.parent_last_name}
              onChange={setField('parent_last_name')}
            />
          </Stack>
        )}

        {activeStep === 1 && (
          <Stack spacing={2}>
            <TextField
              label="Celebrant first name"
              value={form.celebrant_first_name}
              onChange={setField('celebrant_first_name')}
              autoFocus
              required
            />
            <TextField
              label="Celebrant last name"
              value={form.celebrant_last_name}
              onChange={setField('celebrant_last_name')}
            />
          </Stack>
        )}

        {activeStep === 2 && (
          <Stack spacing={2}>
            <TextField
              label="Phone"
              value={form.phone}
              onChange={setField('phone')}
              autoFocus
              required
            />
            <TextField
              label="Email"
              type="email"
              value={form.email}
              onChange={setField('email')}
            />
            <TextField
              select
              label="Party size"
              value={form.party_size_bucket}
              onChange={setField('party_size_bucket')}
              required
            >
              {PARTY_BUCKETS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label="Role"
              value={form.role}
              onChange={setField('role')}
            >
              {ROLE_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>
        )}

        {errorMessage && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {errorMessage}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={mutation.isPending}>
          Cancel
        </Button>
        {activeStep > 0 && (
          <Button
            onClick={() => setActiveStep((step) => step - 1)}
            disabled={mutation.isPending}
          >
            Back
          </Button>
        )}
        {activeStep < STEPS.length - 1 ? (
          <Button
            variant="contained"
            onClick={() => setActiveStep((step) => step + 1)}
            disabled={!canContinue}
          >
            Continue
          </Button>
        ) : (
          <Button
            variant="contained"
            onClick={() => mutation.mutate(buildPayload())}
            disabled={!canContinue || mutation.isPending}
          >
            Add
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
