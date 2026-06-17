import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
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
  Typography,
} from '@mui/material'
import { useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { createWalkInLead } from '../../services/api'
import { useSearch } from '../../hooks/useSearch'

// Step-1 (Contact) and Step-2 (Lead details) state is kept local to this
// component on purpose. The palette context only owns open/close — see
// CommandPaletteContext. Burying form state in the context would couple
// every consumer to its lifecycle and re-render shape.

const PARTY_OPTIONS = [
  { value: 'pair', label: 'Pair (2)' },
  { value: '3_4', label: '3–4 guests' },
  { value: '5_plus', label: '5+ guests' },
]

// Mirrors the buckets the public widget renders. Plain strings rather
// than a strict enum because the widget itself lets staff customize
// these later via business profile copy.
const BUDGET_OPTIONS = [
  'Under $1k',
  '$1k–$2k',
  '$2k–$4k',
  '$4k–$6k',
  '$6k+',
  'Not sure yet',
]

function emptyContactStep() {
  return {
    pickedContactId: null,
    pickedDisplayName: '',
    first_name: '',
    last_name: '',
    display_name: '',
    email: '',
    phone: '',
  }
}

function emptyDetailsStep() {
  return {
    celebrant_first_name: '',
    celebrant_last_name: '',
    event_name: '',
    event_date: '',
    party_size_bucket: '3_4',
    court_size: '',
    quince_theme: '',
    quince_theme_colors: [],
    budget_range: '',
    notes: '',
  }
}

function defaultEventName(celebrantFirst, celebrantLast) {
  const base = (celebrantFirst || '').trim()
  if (!base) return ''
  const last = (celebrantLast || '').trim()
  const owner = last ? `${base} ${last}` : base
  return `${owner}'s Quince`
}

function describeError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 422 && detail === 'invalid_phone') {
    return 'That phone number isn’t in a format we can dedupe by. Use a 10-digit US number or full international format.'
  }
  if (status === 422 && detail === 'phone_required') {
    return 'Phone is required.'
  }
  if (status === 422 && detail === 'contact_name_required') {
    return 'Enter a first/last name or display name for the contact.'
  }
  if (status === 422 && detail === 'celebrant_first_name_required') {
    return 'Celebrant first name is required.'
  }
  if (status === 422 && detail === 'invalid_party_size_bucket') {
    return 'Pick a party size.'
  }
  if (status === 401 || status === 403) {
    return 'You don’t have permission to create leads.'
  }
  if (typeof detail === 'string') return detail
  return err?.message || 'Failed to create lead.'
}

export default function NewLeadDialog({ open, onClose }) {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [contactStep, setContactStep] = useState(emptyContactStep)
  const [detailsStep, setDetailsStep] = useState(emptyDetailsStep)
  const [error, setError] = useState(null)

  // Reset whenever the dialog opens — same idea as CommandPalette.
  // Keeps form state from leaking between two unrelated walk-ins.
  useEffect(() => {
    if (!open) return
    setStep(0)
    setContactStep(emptyContactStep())
    setDetailsStep(emptyDetailsStep())
    setError(null)
  }, [open])

  const submit = useMutation({
    mutationFn: (payload) => createWalkInLead(payload),
    onSuccess: (resp) => {
      onClose?.()
      if (resp?.event?.id) {
        navigate(`/events/${resp.event.id}/overview`)
      }
    },
    onError: (err) => setError(describeError(err)),
  })

  const canAdvanceFromContact = useMemo(() => {
    const hasName =
      contactStep.pickedContactId != null ||
      (contactStep.first_name || '').trim() !== '' ||
      (contactStep.last_name || '').trim() !== '' ||
      (contactStep.display_name || '').trim() !== ''
    const hasPhone = (contactStep.phone || '').trim() !== ''
    return hasName && hasPhone
  }, [contactStep])

  const canSubmit = useMemo(
    () => (detailsStep.celebrant_first_name || '').trim() !== '',
    [detailsStep],
  )

  function buildPayload() {
    const trimOrNull = (v) => {
      const t = (v || '').trim()
      return t === '' ? null : t
    }
    return {
      contact: {
        first_name: trimOrNull(contactStep.first_name),
        last_name: trimOrNull(contactStep.last_name),
        display_name: trimOrNull(contactStep.display_name),
        email: trimOrNull(contactStep.email),
        phone: (contactStep.phone || '').trim(),
      },
      event: {
        celebrant_first_name: (detailsStep.celebrant_first_name || '').trim(),
        celebrant_last_name: trimOrNull(detailsStep.celebrant_last_name),
        event_name: trimOrNull(detailsStep.event_name),
        event_date: trimOrNull(detailsStep.event_date),
        owner_user_id: null,
      },
      enrichment: {
        party_size_bucket: detailsStep.party_size_bucket,
        court_size:
          detailsStep.court_size === '' ? null : Number(detailsStep.court_size),
        quince_theme: trimOrNull(detailsStep.quince_theme),
        quince_theme_colors: detailsStep.quince_theme_colors.length
          ? detailsStep.quince_theme_colors
          : null,
        budget_range: trimOrNull(detailsStep.budget_range),
        dress_styles: null,
        colors: null,
        notes: trimOrNull(detailsStep.notes),
      },
    }
  }

  function handleSubmit(e) {
    e?.preventDefault?.()
    if (!canSubmit || submit.isPending) return
    setError(null)
    submit.mutate(buildPayload())
  }

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>New walk-in lead</DialogTitle>
      <DialogContent dividers>
        <Stepper activeStep={step} sx={{ mb: 3 }}>
          <Step><StepLabel>Contact</StepLabel></Step>
          <Step><StepLabel>Lead details</StepLabel></Step>
        </Stepper>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {step === 0 ? (
          <ContactStep
            value={contactStep}
            onChange={setContactStep}
          />
        ) : (
          <DetailsStep
            value={detailsStep}
            onChange={setDetailsStep}
            contactDisplayName={
              contactStep.pickedDisplayName ||
              [contactStep.first_name, contactStep.last_name]
                .filter(Boolean)
                .join(' ') ||
              contactStep.display_name
            }
          />
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        {step === 1 && (
          <Button
            onClick={() => setStep(0)}
            disabled={submit.isPending}
          >
            Back
          </Button>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button onClick={onClose} disabled={submit.isPending}>
          Cancel
        </Button>
        {step === 0 ? (
          <Button
            variant="contained"
            onClick={() => {
              setError(null)
              // Carry the contact's name forward into Step 2 so staff
              // don't retype it for a self-celebrant case.
              setDetailsStep((d) => ({
                ...d,
                celebrant_first_name:
                  d.celebrant_first_name ||
                  contactStep.first_name ||
                  contactStep.pickedDisplayName.split(' ')[0] ||
                  '',
                celebrant_last_name:
                  d.celebrant_last_name || contactStep.last_name || '',
              }))
              setStep(1)
            }}
            disabled={!canAdvanceFromContact}
          >
            Next
          </Button>
        ) : (
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={!canSubmit || submit.isPending}
            startIcon={submit.isPending ? <CircularProgress size={16} /> : null}
          >
            Create lead
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — Contact picker / manual entry
// ---------------------------------------------------------------------------

function ContactStep({ value, onChange }) {
  const [search, setSearch] = useState('')
  const { isFetching, data } = useSearch(search)

  const options = useMemo(() => {
    const results = data?.results || []
    return results.filter((r) => r.type === 'contact')
  }, [data])

  function patch(updates) {
    onChange((v) => ({ ...v, ...updates }))
  }

  return (
    <Stack spacing={2}>
      <Autocomplete
        freeSolo
        options={options}
        loading={isFetching}
        // Only the picked option is used to set pickedContactId; the
        // typed string is for autocomplete control only and never
        // back-fills phone/name. Manual entry happens in the fields
        // below.
        getOptionLabel={(opt) =>
          typeof opt === 'string' ? opt : opt.label || ''
        }
        filterOptions={(x) => x}
        onInputChange={(_, next) => setSearch(next || '')}
        onChange={(_, picked) => {
          if (!picked || typeof picked === 'string') {
            patch({ pickedContactId: null, pickedDisplayName: '' })
            return
          }
          // Server returns label/sublabel/route; phone/email are not in
          // the search response. We carry only the id + label and let
          // the backend dedupe by phone. The staff then types the phone
          // explicitly so the server can confirm identity.
          patch({
            pickedContactId: picked.id,
            pickedDisplayName: picked.label || '',
            display_name: picked.label || '',
          })
        }}
        renderInput={(params) => (
          <TextField
            {...params}
            label="Search existing contact (optional)"
            placeholder="Type a name…"
            size="small"
            helperText="Pick an existing contact to dedupe on phone, or enter a new contact below."
          />
        )}
      />

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          label="First name"
          value={value.first_name}
          onChange={(e) => patch({ first_name: e.target.value })}
          size="small"
        />
        <TextField
          fullWidth
          label="Last name"
          value={value.last_name}
          onChange={(e) => patch({ last_name: e.target.value })}
          size="small"
        />
      </Stack>

      <TextField
        fullWidth
        label="Display name (optional override)"
        value={value.display_name}
        onChange={(e) => patch({ display_name: e.target.value })}
        size="small"
        helperText={
          value.pickedContactId != null
            ? 'Existing contact — display name will not be modified on save.'
            : 'Defaults to first + last name when blank.'
        }
      />

      <TextField
        fullWidth
        label="Phone (required)"
        value={value.phone}
        onChange={(e) => patch({ phone: e.target.value })}
        size="small"
        required
        helperText="10-digit US or full international format. Used to dedupe."
      />

      <TextField
        fullWidth
        label="Email (optional)"
        type="email"
        value={value.email}
        onChange={(e) => patch({ email: e.target.value })}
        size="small"
      />
    </Stack>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — Lead details
// ---------------------------------------------------------------------------

function DetailsStep({ value, onChange, contactDisplayName }) {
  function patch(updates) {
    onChange((v) => ({ ...v, ...updates }))
  }

  // Auto-compose the event name when staff haven't typed one themselves.
  // We re-derive on every render but only when the field is empty, so a
  // manual edit sticks.
  const autoEventName = defaultEventName(
    value.celebrant_first_name,
    value.celebrant_last_name,
  )

  return (
    <Stack spacing={2}>
      {contactDisplayName && (
        <Typography variant="body2" color="text.secondary">
          Filing lead for <strong>{contactDisplayName}</strong>.
        </Typography>
      )}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          label="Celebrant first name"
          value={value.celebrant_first_name}
          onChange={(e) => patch({ celebrant_first_name: e.target.value })}
          size="small"
          required
        />
        <TextField
          fullWidth
          label="Celebrant last name"
          value={value.celebrant_last_name}
          onChange={(e) => patch({ celebrant_last_name: e.target.value })}
          size="small"
        />
      </Stack>

      <TextField
        fullWidth
        label="Event name"
        value={value.event_name}
        onChange={(e) => patch({ event_name: e.target.value })}
        size="small"
        placeholder={autoEventName}
        helperText={
          value.event_name
            ? null
            : `Will default to "${autoEventName || '…'}" when blank.`
        }
      />

      <TextField
        fullWidth
        label="Event date"
        type="date"
        value={value.event_date}
        onChange={(e) => patch({ event_date: e.target.value })}
        size="small"
        InputLabelProps={{ shrink: true }}
      />

      <Box>
        <Typography variant="overline" color="text.secondary">
          Party size
        </Typography>
        <Stack direction="row" spacing={1} sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
          {PARTY_OPTIONS.map((opt) => (
            <Chip
              key={opt.value}
              label={opt.label}
              color={value.party_size_bucket === opt.value ? 'primary' : 'default'}
              variant={value.party_size_bucket === opt.value ? 'filled' : 'outlined'}
              onClick={() => patch({ party_size_bucket: opt.value })}
            />
          ))}
        </Stack>
      </Box>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          label="Court size"
          type="number"
          value={value.court_size}
          onChange={(e) => patch({ court_size: e.target.value })}
          size="small"
          inputProps={{ min: 0, max: 100 }}
        />
        <TextField
          fullWidth
          select
          label="Budget"
          value={value.budget_range}
          onChange={(e) => patch({ budget_range: e.target.value })}
          size="small"
        >
          <MenuItem value="">
            <em>Unspecified</em>
          </MenuItem>
          {BUDGET_OPTIONS.map((opt) => (
            <MenuItem key={opt} value={opt}>
              {opt}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      <TextField
        fullWidth
        label="Theme"
        value={value.quince_theme}
        onChange={(e) => patch({ quince_theme: e.target.value })}
        size="small"
      />

      <TextField
        fullWidth
        label="Theme colors (comma-separated)"
        value={value.quince_theme_colors.join(', ')}
        onChange={(e) =>
          patch({
            quince_theme_colors: e.target.value
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean),
          })
        }
        size="small"
        helperText="Free text, e.g. sage, blush, gold."
      />

      <TextField
        fullWidth
        label="Internal notes"
        value={value.notes}
        onChange={(e) => patch({ notes: e.target.value })}
        size="small"
        multiline
        minRows={2}
      />
    </Stack>
  )
}
