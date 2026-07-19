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
  MenuItem,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import {
  adminGetOwnerCascadePreview,
  adminReassignEventOwner,
  salesListAssignableStaff,
} from '../services/api'

const UNASSIGNED_VALUE = '__unassigned__'

// Phase 11: admin lead-owner reassignment. Lead-scope only — admin
// per-appointment assignment is intentionally deferred (2026-05-18
// decision). The cascade rules, audit shape, and notification ordering
// are owned by services/sales_assignment.py so this surface can't drift
// from the sales-side equivalent.

function describeError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 400 && detail === 'invalid_assigned_user_id') {
    return 'Pick an active sales stylist.'
  }
  if (status === 404 && detail === 'event_not_found') {
    return 'This event no longer exists. Reload and try again.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to reassign this lead.'
  }
  if (typeof detail === 'string') return detail
  return 'Could not save the change. Try again.'
}

function fullName(first, last) {
  return [first, last].filter(Boolean).join(' ').trim()
}

export default function AdminEventOwnerDialog({
  open,
  onClose,
  eventId,
  currentOwnerUserId,
  currentOwnerName,
  onSuccess,
}) {
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))

  const [ownerSelection, setOwnerSelection] = useState(UNASSIGNED_VALUE)
  const [error, setError] = useState(null)

  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  const cascadeQuery = useQuery({
    queryKey: ['admin', 'owner-cascade-preview', eventId],
    queryFn: () => adminGetOwnerCascadePreview(eventId),
    enabled: open && Boolean(eventId),
  })

  useEffect(() => {
    if (!open) return
    setOwnerSelection(
      currentOwnerUserId == null ? UNASSIGNED_VALUE : String(currentOwnerUserId),
    )
    setError(null)
  }, [open, currentOwnerUserId])

  const submit = useMutation({
    mutationFn: () => {
      const value =
        ownerSelection === UNASSIGNED_VALUE ? null : Number(ownerSelection)
      return adminReassignEventOwner(eventId, value)
    },
    onSuccess: (result) => {
      onSuccess?.(result)
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
    const currentAsValue =
      currentOwnerUserId == null
        ? UNASSIGNED_VALUE
        : String(currentOwnerUserId)
    return ownerSelection === currentAsValue
  }, [ownerSelection, currentOwnerUserId])

  const staff = staffQuery.data || []
  const cascadeRows = cascadeQuery.data?.future_appointments || []

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>Change event owner</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          <Box>
            <Typography variant="overline" color="text.secondary">
              Current owner
            </Typography>
            <Typography variant="body2">
              {currentOwnerName || (currentOwnerUserId == null ? 'Unassigned' : '—')}
            </Typography>
          </Box>

          <TextField
            select
            fullWidth
            size="small"
            label="New owner"
            value={ownerSelection}
            onChange={(e) => setOwnerSelection(e.target.value)}
            disabled={staffQuery.isLoading}
            helperText={
              staffQuery.isError ? 'Could not load staff list. Reload.' : null
            }
          >
            <MenuItem value={UNASSIGNED_VALUE}>
              <em>Unassigned</em>
            </MenuItem>
            {staff.map((row) => (
              <MenuItem key={row.id} value={String(row.id)}>
                {row.full_name}
              </MenuItem>
            ))}
          </TextField>

          <Box>
            <Typography variant="overline" color="text.secondary">
              Future appointments that will move
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
                        minWidth: 130,
                        fontVariantNumeric: 'tabular-nums',
                      }}
                    >
                      {dayjs(row.slot_start_at).format('MMM D, h:mm A')}
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
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mt: 1 }}
            >
              Past appointments stay frozen for attribution.
            </Typography>
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={submit.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={submit.isPending || !eventId || isUnchanged}
          startIcon={submit.isPending ? <CircularProgress size={16} /> : null}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}
