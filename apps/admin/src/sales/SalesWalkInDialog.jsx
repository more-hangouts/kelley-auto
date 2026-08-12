import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'

import WalkInLeadIntakeForm from '../components/WalkInLeadIntakeForm'
import {
  canSaveWalkInLead,
  composeWalkInNotes,
  defaultDealName,
  emptyWalkInLeadForm,
  trimOrNull,
  walkInBuyerName,
} from '../utils/walkInLeadIntake'
import {
  salesCreateWalkIn,
  salesListAssignableStaff,
} from '../services/api'
import { attendanceGateMessage, isAttendanceGateError } from './attendanceGate'

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
    return 'Enter the customer name.'
  }
  if (status === 422 && detail === 'celebrant_first_name_required') {
    return 'Enter the buyer first name.'
  }
  if (status === 422 && detail === 'invalid_walk_in_source') {
    return 'Pick one of the source options.'
  }
  if (status === 422 && detail === 'walk_in_source_detail_too_long') {
    return 'Shorten the platform/post detail to 200 characters or less.'
  }
  if (status === 400 && detail === 'invalid_assigned_user_id') {
    return 'Pick an active sales stylist for assignment.'
  }
  if (status === 400 && detail === 'invalid_sales_credit_user_id') {
    return 'Pick an active salesperson for commission credit.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to create this walk-in.'
  }
  if (typeof detail === 'string') return detail
  return 'Could not create the walk-in. Try again.'
}

export default function SalesWalkInDialog({ open, onClose, onCreated }) {
  const navigate = useNavigate()
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))

  const [form, setForm] = useState(emptyWalkInLeadForm)
  const [error, setError] = useState(null)

  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (!open) return
    setForm(emptyWalkInLeadForm())
    setError(null)
  }, [open])

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

  function buildPayload() {
    const buyer = walkInBuyerName(form)

    return {
      contact: {
        first_name: trimOrNull(form.first_name),
        last_name: trimOrNull(form.last_name),
        display_name: null,
        email: trimOrNull(form.email),
        phone: (form.phone || '').trim(),
      },
      event: {
        celebrant_first_name: buyer.first,
        celebrant_last_name: buyer.last,
        event_name: defaultDealName(buyer.first, buyer.last),
        event_date: null,
        owner_user_id: null,
        sales_credit_user_id: form.sales_credit_user_id
          ? Number(form.sales_credit_user_id)
          : null,
        walk_in_source: trimOrNull(form.walk_in_source),
        walk_in_source_detail: trimOrNull(form.walk_in_source_detail),
      },
      enrichment: {
        budget_range: trimOrNull(form.budget_range),
        notes: composeWalkInNotes(form),
      },
      booking_context: form.booking_context,
      assigned_user_id: null,
    }
  }

  function handleSubmit(event) {
    event?.preventDefault?.()
    if (!canSaveWalkInLead(form) || submit.isPending) return
    setError(null)
    submit.mutate(buildPayload())
  }

  const assignees = staffQuery.data || []

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="md"
    >
      <DialogTitle>
        {form.booking_context === 'phone_call'
          ? 'Quick Add Phone Lead'
          : 'Quick Add Walk-In'}
      </DialogTitle>
      <DialogContent dividers>
        <Box component="form" onSubmit={handleSubmit}>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
          <WalkInLeadIntakeForm
            value={form}
            onChange={setForm}
            searchScope="sales"
            creditOptions={assignees}
            creditLoading={staffQuery.isLoading}
            creditError={staffQuery.isError}
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={submit.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={!canSaveWalkInLead(form) || submit.isPending}
          startIcon={submit.isPending ? <CircularProgress size={16} /> : null}
        >
          Save Lead
        </Button>
      </DialogActions>
    </Dialog>
  )
}
