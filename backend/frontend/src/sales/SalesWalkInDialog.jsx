import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
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
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import {
  salesCreateWalkIn,
  salesListAssignableStaff,
} from '../services/api'
import { attendanceGateMessage, isAttendanceGateError } from './attendanceGate'

const PARTY_OPTIONS = [
  { value: 'pair', label: 'Pair (2)' },
  { value: '3_4', label: '3-4 guests' },
  { value: '5_plus', label: '5+ guests' },
]

const BUDGET_OPTIONS = [
  'Under $1k',
  '$1k-$2k',
  '$2k-$4k',
  '$4k-$6k',
  '$6k+',
  'Not sure yet',
]

function emptyContact() {
  return {
    first_name: '',
    last_name: '',
    display_name: '',
    phone: '',
    email: '',
  }
}

function emptyDetails() {
  return {
    celebrant_first_name: '',
    celebrant_last_name: '',
    event_name: '',
    event_date: '',
    party_size_bucket: '3_4',
    court_size: '',
    quince_theme: '',
    quince_theme_colors: '',
    budget_range: '',
    notes: '',
  }
}

function trimOrNull(value) {
  const t = (value || '').trim()
  return t === '' ? null : t
}

function defaultEventName(first, last) {
  const base = (first || '').trim()
  if (!base) return ''
  const surname = (last || '').trim()
  return `${surname ? `${base} ${surname}` : base}'s Quince`
}

function describeError(err) {
  if (isAttendanceGateError(err)) return attendanceGateMessage()

  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 422 && detail === 'invalid_phone') {
    return 'That phone number is not in a format we can match. Use a 10-digit US number or full international format.'
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
  if (status === 400 && detail === 'invalid_assigned_user_id') {
    return 'Pick an active sales stylist for assignment.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to create this walk-in.'
  }
  if (typeof detail === 'string') return detail
  return 'Could not create the walk-in. Try again.'
}

function splitCsv(value) {
  return (value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export default function SalesWalkInDialog({ open, onClose, onCreated }) {
  const navigate = useNavigate()
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))
  const { user } = useSalesAuth()

  const [step, setStep] = useState(0)
  const [contact, setContact] = useState(emptyContact)
  const [details, setDetails] = useState(emptyDetails)
  const [assignedUserId, setAssignedUserId] = useState('')
  const [error, setError] = useState(null)

  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (!open) return
    setStep(0)
    setContact(emptyContact())
    setDetails(emptyDetails())
    setAssignedUserId(user?.id ? String(user.id) : '')
    setError(null)
  }, [open, user?.id])

  const canAdvanceFromContact = useMemo(() => {
    const hasName = Boolean(
      trimOrNull(contact.first_name) ||
        trimOrNull(contact.last_name) ||
        trimOrNull(contact.display_name),
    )
    return hasName && Boolean(trimOrNull(contact.phone))
  }, [contact])

  const canSubmit = useMemo(
    () => Boolean(trimOrNull(details.celebrant_first_name)),
    [details.celebrant_first_name],
  )

  const submit = useMutation({
    mutationFn: (payload) => salesCreateWalkIn(payload),
    onSuccess: (resp) => {
      onCreated?.(resp)
      onClose?.()
      if (resp?.route) {
        navigate(resp.route)
      }
    },
    onError: (err) => setError(describeError(err)),
  })

  function patchContact(updates) {
    setContact((prev) => ({ ...prev, ...updates }))
  }

  function patchDetails(updates) {
    setDetails((prev) => ({ ...prev, ...updates }))
  }

  function buildPayload() {
    const currentUserId = user?.id ? String(user.id) : ''
    const selectedAssignee = assignedUserId ? Number(assignedUserId) : null
    const eventName =
      trimOrNull(details.event_name) ||
      defaultEventName(
        details.celebrant_first_name,
        details.celebrant_last_name,
      ) ||
      null

    return {
      contact: {
        first_name: trimOrNull(contact.first_name),
        last_name: trimOrNull(contact.last_name),
        display_name: trimOrNull(contact.display_name),
        email: trimOrNull(contact.email),
        phone: (contact.phone || '').trim(),
      },
      event: {
        celebrant_first_name: (details.celebrant_first_name || '').trim(),
        celebrant_last_name: trimOrNull(details.celebrant_last_name),
        event_name: eventName,
        event_date: trimOrNull(details.event_date),
        owner_user_id: null,
      },
      enrichment: {
        party_size_bucket: details.party_size_bucket,
        court_size:
          details.court_size === '' ? null : Number(details.court_size),
        quince_theme: trimOrNull(details.quince_theme),
        quince_theme_colors: splitCsv(details.quince_theme_colors),
        budget_range: trimOrNull(details.budget_range),
        dress_styles: null,
        colors: null,
        notes: trimOrNull(details.notes),
      },
      assigned_user_id:
        selectedAssignee && String(selectedAssignee) !== currentUserId
          ? selectedAssignee
          : null,
    }
  }

  function handleNext() {
    setError(null)
    setDetails((prev) => ({
      ...prev,
      celebrant_first_name:
        prev.celebrant_first_name || contact.first_name || '',
      celebrant_last_name:
        prev.celebrant_last_name || contact.last_name || '',
    }))
    setStep(1)
  }

  function handleSubmit(event) {
    event?.preventDefault?.()
    if (!canSubmit || submit.isPending) return
    setError(null)
    submit.mutate(buildPayload())
  }

  const assignees = staffQuery.data || []
  const autoEventName = defaultEventName(
    details.celebrant_first_name,
    details.celebrant_last_name,
  )

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>Add walk-in</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} component="form" onSubmit={handleSubmit}>
          <Stepper activeStep={step}>
            <Step>
              <StepLabel>Contact</StepLabel>
            </Step>
            <Step>
              <StepLabel>Walk-in</StepLabel>
            </Step>
          </Stepper>

          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          {step === 0 ? (
            <ContactFields value={contact} onPatch={patchContact} />
          ) : (
            <Stack spacing={2}>
              <TextField
                select
                fullWidth
                size="small"
                label="Assigned stylist"
                value={assignedUserId}
                onChange={(e) => setAssignedUserId(e.target.value)}
                disabled={staffQuery.isLoading}
                helperText={
                  staffQuery.isError
                    ? 'Could not load staff. The walk-in can still default to you.'
                    : 'Defaults to the signed-in stylist.'
                }
              >
                {user?.id && (
                  <MenuItem value={String(user.id)}>
                    {(user.full_name || user.username || 'Me') + ' (me)'}
                  </MenuItem>
                )}
                {assignees
                  .filter((row) => row.id !== user?.id)
                  .map((row) => (
                    <MenuItem key={row.id} value={String(row.id)}>
                      {row.full_name}
                    </MenuItem>
                  ))}
              </TextField>

              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  fullWidth
                  required
                  size="small"
                  label="Celebrant first name"
                  value={details.celebrant_first_name}
                  onChange={(e) =>
                    patchDetails({ celebrant_first_name: e.target.value })
                  }
                />
                <TextField
                  fullWidth
                  size="small"
                  label="Celebrant last name"
                  value={details.celebrant_last_name}
                  onChange={(e) =>
                    patchDetails({ celebrant_last_name: e.target.value })
                  }
                />
              </Stack>

              <TextField
                fullWidth
                size="small"
                label="Event name"
                value={details.event_name}
                onChange={(e) => patchDetails({ event_name: e.target.value })}
                placeholder={autoEventName}
                helperText={
                  details.event_name
                    ? null
                    : `Will default to "${autoEventName || 'celebrant'}" when blank.`
                }
              />

              <TextField
                fullWidth
                size="small"
                type="date"
                label="Event date"
                value={details.event_date}
                onChange={(e) => patchDetails({ event_date: e.target.value })}
                InputLabelProps={{ shrink: true }}
              />

              <Box>
                <Typography variant="overline" color="text.secondary">
                  Party size
                </Typography>
                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ mt: 0.5 }}
                  flexWrap="wrap"
                  useFlexGap
                >
                  {PARTY_OPTIONS.map((opt) => (
                    <Chip
                      key={opt.value}
                      label={opt.label}
                      color={
                        details.party_size_bucket === opt.value
                          ? 'primary'
                          : 'default'
                      }
                      variant={
                        details.party_size_bucket === opt.value
                          ? 'filled'
                          : 'outlined'
                      }
                      onClick={() =>
                        patchDetails({ party_size_bucket: opt.value })
                      }
                    />
                  ))}
                </Stack>
              </Box>

              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  fullWidth
                  size="small"
                  type="number"
                  label="Court size"
                  value={details.court_size}
                  onChange={(e) =>
                    patchDetails({ court_size: e.target.value })
                  }
                  inputProps={{ min: 0, max: 100 }}
                />
                <TextField
                  select
                  fullWidth
                  size="small"
                  label="Budget"
                  value={details.budget_range}
                  onChange={(e) =>
                    patchDetails({ budget_range: e.target.value })
                  }
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
                size="small"
                label="Theme"
                value={details.quince_theme}
                onChange={(e) =>
                  patchDetails({ quince_theme: e.target.value })
                }
              />

              <TextField
                fullWidth
                size="small"
                label="Theme colors"
                value={details.quince_theme_colors}
                onChange={(e) =>
                  patchDetails({ quince_theme_colors: e.target.value })
                }
                helperText="Comma-separated, e.g. sage, blush, gold."
              />

              <TextField
                fullWidth
                multiline
                minRows={2}
                size="small"
                label="Internal notes"
                value={details.notes}
                onChange={(e) => patchDetails({ notes: e.target.value })}
              />
            </Stack>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        {step === 1 && (
          <Button onClick={() => setStep(0)} disabled={submit.isPending}>
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
            onClick={handleNext}
            disabled={!canAdvanceFromContact}
          >
            Next
          </Button>
        ) : (
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={!canSubmit || submit.isPending}
            startIcon={
              submit.isPending ? <CircularProgress size={16} /> : null
            }
          >
            Create walk-in
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

function ContactFields({ value, onPatch }) {
  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          size="small"
          label="First name"
          value={value.first_name}
          onChange={(e) => onPatch({ first_name: e.target.value })}
        />
        <TextField
          fullWidth
          size="small"
          label="Last name"
          value={value.last_name}
          onChange={(e) => onPatch({ last_name: e.target.value })}
        />
      </Stack>

      <TextField
        fullWidth
        size="small"
        label="Display name"
        value={value.display_name}
        onChange={(e) => onPatch({ display_name: e.target.value })}
        helperText="Use this only when first and last name are not the best label."
      />

      <TextField
        fullWidth
        required
        size="small"
        label="Phone"
        value={value.phone}
        onChange={(e) => onPatch({ phone: e.target.value })}
        helperText="Used to match an existing contact when possible."
      />

      <TextField
        fullWidth
        size="small"
        type="email"
        label="Email"
        value={value.email}
        onChange={(e) => onPatch({ email: e.target.value })}
      />
    </Stack>
  )
}
