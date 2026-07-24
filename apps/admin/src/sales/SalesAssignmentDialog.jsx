import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import {
  salesAssignAppointment,
  salesAssignLead,
  salesGetLeadCascadePreview,
  salesListAssignableStaff,
} from '../services/api'
import { attendanceGateMessage, isAttendanceGateError } from './attendanceGate'

const UNASSIGNED_VALUE = '__unassigned__'

function describeError(err) {
  if (isAttendanceGateError(err)) return attendanceGateMessage()

  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 400 && detail === 'invalid_assigned_user_id') {
    return 'Pick an active sales stylist.'
  }
  if (status === 404 && detail === 'event_not_found') {
    return 'This event no longer exists. Reload and try again.'
  }
  if (status === 404 && detail === 'appointment_not_found') {
    return 'This appointment no longer exists. Reload and try again.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to reassign this.'
  }
  if (typeof detail === 'string') return detail
  return 'Could not save the assignment. Try again.'
}

function formatSlot(iso, tz) {
  if (!iso) return ''
  try {
    return new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleString()
  }
}

function fullName(first, last) {
  return [first, last].filter(Boolean).join(' ').trim()
}

export default function SalesAssignmentDialog({
  open,
  onClose,
  appointmentId,
  appointmentTimezone,
  eventId,
  currentAssignedUserId,
  currentEventOwnerId,
  onSuccess,
}) {
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))
  const { user } = useSalesAuth()

  const [scope, setScope] = useState('appointment')
  const [assignedSelection, setAssignedSelection] = useState(UNASSIGNED_VALUE)
  const [error, setError] = useState(null)

  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  const cascadeQuery = useQuery({
    queryKey: ['sales', 'lead-cascade-preview', eventId],
    queryFn: () => salesGetLeadCascadePreview(eventId),
    enabled: open && scope === 'lead' && Boolean(eventId),
  })

  useEffect(() => {
    if (!open) return
    setScope('appointment')
    setError(null)
  }, [open])

  // Seed the picker with the current value for the active scope so the
  // dropdown opens already pointing at "whoever has this today." Re-runs
  // when the user toggles scope so flipping to "lead" lands on the event
  // owner, not on whoever the per-appointment assignee was.
  useEffect(() => {
    if (!open) return
    const current =
      scope === 'appointment' ? currentAssignedUserId : currentEventOwnerId
    setAssignedSelection(
      current == null ? UNASSIGNED_VALUE : String(current),
    )
    setError(null)
  }, [scope, currentAssignedUserId, currentEventOwnerId, open])

  const submit = useMutation({
    mutationFn: () => {
      const value =
        assignedSelection === UNASSIGNED_VALUE
          ? null
          : Number(assignedSelection)
      if (scope === 'appointment') {
        return salesAssignAppointment(appointmentId, value)
      }
      return salesAssignLead(eventId, value)
    },
    onSuccess: (result) => {
      onSuccess?.({ scope, result })
      onClose?.()
    },
    onError: (err) => setError(describeError(err)),
  })

  function handleSubmit(e) {
    e?.preventDefault?.()
    if (submit.isPending) return
    setError(null)
    submit.mutate()
  }

  const isUnchanged = useMemo(() => {
    const currentInScope =
      scope === 'appointment' ? currentAssignedUserId : currentEventOwnerId
    const currentAsValue =
      currentInScope == null ? UNASSIGNED_VALUE : String(currentInScope)
    return assignedSelection === currentAsValue
  }, [scope, currentAssignedUserId, currentEventOwnerId, assignedSelection])

  const assignees = staffQuery.data || []
  const meRow =
    user?.id != null
      ? {
          id: user.id,
          full_name: user.full_name || user.username || 'Me',
        }
      : null
  const others = assignees.filter((row) => row.id !== user?.id)
  const cascadeRows = cascadeQuery.data?.future_appointments || []
  const canSubmit = scope === 'appointment' || Boolean(eventId)

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>Reassign</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          <FormControl>
            <FormLabel>Scope</FormLabel>
            <RadioGroup
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <FormControlLabel
                value="appointment"
                control={<Radio />}
                label="This appointment only"
              />
              <FormControlLabel
                value="lead"
                control={<Radio />}
                label="All future appointments for this lead"
                disabled={!eventId}
              />
            </RadioGroup>
            {!eventId && (
              <Typography variant="caption" color="text.secondary">
                Lead reassignment is available once the appointment has a
                linked event.
              </Typography>
            )}
          </FormControl>

          <TextField
            select
            fullWidth
            size="small"
            label="Assign to"
            value={assignedSelection}
            onChange={(e) => setAssignedSelection(e.target.value)}
            disabled={staffQuery.isLoading}
            helperText={
              staffQuery.isError ? 'Could not load staff list. Reload.' : null
            }
          >
            <MenuItem value={UNASSIGNED_VALUE}>
              <em>Unassigned</em>
            </MenuItem>
            {meRow && (
              <MenuItem value={String(meRow.id)}>
                {meRow.full_name} (me)
              </MenuItem>
            )}
            {others.map((row) => (
              <MenuItem key={row.id} value={String(row.id)}>
                {row.full_name}
              </MenuItem>
            ))}
          </TextField>

          {scope === 'lead' && eventId && (
            <Box>
              <Typography variant="overline" color="text.secondary">
                This will also move
              </Typography>
              {cascadeQuery.isLoading ? (
                <Stack alignItems="center" sx={{ py: 1 }}>
                  <CircularProgress size={20} />
                </Stack>
              ) : cascadeQuery.isError ? (
                <Typography variant="body2" color="error">
                  Could not load the future-appointment list.
                </Typography>
              ) : cascadeRows.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  No future appointments tied to this lead yet. Only the
                  lead owner will change.
                </Typography>
              ) : (
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {cascadeRows.map((row) => (
                    <Stack
                      key={row.id}
                      direction="row"
                      spacing={1}
                      alignItems="baseline"
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          minWidth: 110,
                          fontVariantNumeric: 'tabular-nums',
                        }}
                      >
                        {formatSlot(row.slot_start_at, appointmentTimezone)}
                      </Typography>
                      <Typography variant="body2" sx={{ flex: 1 }}>
                        {fullName(
                          row.celebrant_first_name,
                          row.celebrant_last_name,
                        ) || '(no name)'}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {row.assigned_user_full_name || 'Unassigned'}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={submit.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={submit.isPending || !canSubmit || isUnchanged}
          startIcon={submit.isPending ? <CircularProgress size={16} /> : null}
        >
          {scope === 'lead' ? 'Move lead' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
