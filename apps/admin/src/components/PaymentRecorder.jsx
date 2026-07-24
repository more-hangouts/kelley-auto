import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import dayjs from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { listInvoices, recordPayment } from '../services/api'
import { formatUSD } from '../utils/money'
import CurrencyInput from './CurrencyInput'

const METHODS = [
  { value: 'cash', label: 'Cash' },
  { value: 'check', label: 'Check' },
  { value: 'card', label: 'Card' },
  { value: 'transfer', label: 'Bank transfer' },
  { value: 'zelle', label: 'Zelle' },
  { value: 'other', label: 'Other' },
]

export default function PaymentRecorder({
  open,
  onClose,
  eventId,
  contactId,
  onRecorded,
}) {
  const queryClient = useQueryClient()
  const [amountCents, setAmountCents] = useState(0)
  const [method, setMethod] = useState('cash')
  const [paymentDate, setPaymentDate] = useState(dayjs().format('YYYY-MM-DD'))
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [allocations, setAllocations] = useState({}) // { invoiceId: cents }
  const [error, setError] = useState(null)

  // Pull every invoice on the event so staff can allocate the new
  // payment across one or more. We filter to non-final statuses
  // (draft + cancelled + reversed are not allocation targets).
  const invoicesQuery = useQuery({
    queryKey: ['event', eventId, 'invoices'],
    queryFn: () => listInvoices(eventId),
    enabled: open,
  })

  useEffect(() => {
    if (!open) return
    setAmountCents(0)
    setMethod('cash')
    setPaymentDate(dayjs().format('YYYY-MM-DD'))
    setReference('')
    setNotes('')
    setAllocations({})
    setError(null)
  }, [open])

  const eligibleInvoices = useMemo(() => {
    return (invoicesQuery.data || []).filter(
      (inv) => !['draft', 'cancelled', 'reversed', 'paid'].includes(inv.status),
    )
  }, [invoicesQuery.data])

  const allocSum = useMemo(
    () => Object.values(allocations).reduce((s, n) => s + (Number(n) || 0), 0),
    [allocations],
  )
  const remaining = amountCents - allocSum
  const valid =
    amountCents > 0 &&
    remaining >= 0 &&
    Object.entries(allocations).every(([, c]) => Number(c) >= 0)

  const setAlloc = (invoiceId, cents) => {
    setAllocations((prev) => {
      const next = { ...prev }
      if (!cents || cents <= 0) delete next[invoiceId]
      else next[invoiceId] = cents
      return next
    })
  }

  const fillBalance = (invoiceId, balance) => {
    // Auto-fill this invoice's allocation from whatever's left of the
    // payment, capped at the invoice's outstanding balance. A
    // one-click "apply remainder here" affordance is the most common
    // staff flow.
    const cap = Math.min(remaining + (allocations[invoiceId] || 0), balance)
    setAlloc(invoiceId, cap > 0 ? cap : 0)
  }

  const recordMutation = useMutation({
    mutationFn: () =>
      recordPayment({
        contact_id: contactId,
        amount_cents: amountCents,
        method,
        payment_date: paymentDate || null,
        transaction_reference: reference || null,
        notes: notes || null,
        allocations: Object.entries(allocations)
          .filter(([, c]) => Number(c) > 0)
          .map(([invoiceId, c]) => ({
            invoice_id: Number(invoiceId),
            applied_cents: Number(c),
          })),
      }),
    onSuccess: (payment) => {
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'payments'] })
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'invoices'] })
      // Each touched invoice's detail also refreshes so an open editor
      // shows the new paid_to_date.
      for (const a of payment.allocations || []) {
        queryClient.invalidateQueries({ queryKey: ['invoice', a.invoice_id] })
      }
      // Phase 9: payment.created emits an activity row.
      queryClient.invalidateQueries({ queryKey: ['event', eventId, 'activity'] })
      // Phase 10: payments change AR + recent payments + the kanban
      // outstanding pill.
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      if (onRecorded) onRecorded(payment)
      onClose()
    },
    onError: (err) => setError(parseApiError(err)),
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          Record payment
          <IconButton size="small" onClick={onClose}>
            <CloseIcon fontSize="small" />
          </IconButton>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={2}
          sx={{ mb: 2 }}
        >
          <CurrencyInput
            valueCents={amountCents}
            onCommit={setAmountCents}
            label="Amount received"
            fullWidth
          />
          <TextField
            select
            size="small"
            label="Method"
            value={method}
            onChange={(e) => setMethod(e.target.value)}
            sx={{ minWidth: 180 }}
          >
            {METHODS.map((m) => (
              <MenuItem key={m.value} value={m.value}>
                {m.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            type="date"
            label="Payment date"
            value={paymentDate}
            onChange={(e) => setPaymentDate(e.target.value)}
            InputLabelProps={{ shrink: true }}
            sx={{ width: 170 }}
          />
        </Stack>
        <TextField
          size="small"
          label="Reference (check number, last 4, Zelle confirmation)"
          fullWidth
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          sx={{ mb: 2 }}
        />

        <Typography variant="subtitle2" sx={{ mt: 1, mb: 1 }}>
          Apply to invoices
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
          Anything left over goes to the unapplied pool. You can apply it later to a different invoice.
        </Typography>

        {invoicesQuery.isLoading ? (
          <Typography variant="body2" color="text.secondary">
            Loading invoices...
          </Typography>
        ) : eligibleInvoices.length === 0 ? (
          <Alert severity="info" sx={{ mt: 1 }}>
            No open invoices on this event. The full amount will go to the unapplied pool.
          </Alert>
        ) : (
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 120px 140px 100px', gap: 1, alignItems: 'center' }}>
            {eligibleInvoices.map((inv) => (
              <Row key={inv.id}>
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {inv.invoice_number || `Draft #${inv.id}`}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Balance {formatUSD(inv.balance_cents)} · {inv.status}
                  </Typography>
                </Box>
                <Box sx={{ textAlign: 'right' }}>
                  <Typography variant="caption" color="text.secondary">
                    Total
                  </Typography>
                  <Typography variant="body2">{formatUSD(inv.total_cents)}</Typography>
                </Box>
                <CurrencyInput
                  valueCents={allocations[inv.id] || 0}
                  onCommit={(v) => setAlloc(inv.id, Math.min(v, inv.balance_cents))}
                  label="Apply"
                />
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => fillBalance(inv.id, inv.balance_cents)}
                  disabled={remaining <= 0 && !(allocations[inv.id] > 0)}
                >
                  Fill
                </Button>
              </Row>
            ))}
          </Box>
        )}

        <Box sx={{ mt: 2, display: 'flex', justifyContent: 'space-between' }}>
          <Typography variant="body2" color="text.secondary">
            Allocated {formatUSD(allocSum)} of {formatUSD(amountCents)}
          </Typography>
          <Typography
            variant="body2"
            sx={{
              fontWeight: 600,
              color: remaining < 0 ? 'error.main' : 'text.primary',
            }}
          >
            Unapplied: {formatUSD(Math.max(remaining, 0))}
            {remaining < 0 && ` (over by ${formatUSD(-remaining)})`}
          </Typography>
        </Box>

        <TextField
          label="Notes (private)"
          multiline
          minRows={2}
          fullWidth
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          sx={{ mt: 2 }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => recordMutation.mutate()}
          disabled={!valid || recordMutation.isPending}
        >
          {recordMutation.isPending ? 'Recording…' : 'Record payment'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function Row({ children }) {
  return <>{children}</>
}

function parseApiError(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.code) {
    const map = {
      over_allocation: 'You allocated more than the payment amount.',
      invoice_overallocation:
        'One of the allocations exceeds that invoice’s outstanding balance.',
      invalid_allocation_target:
        'Cannot apply to a draft, cancelled, or deleted invoice.',
      invalid_amount: 'Amounts must be positive.',
      invalid_method: 'Pick a valid payment method.',
      contact_not_found: 'Customer record was not found.',
      invoice_not_found: 'One of the invoices was not found.',
    }
    return map[detail.code] || `Error: ${detail.code}`
  }
  return err?.message || 'Failed to record payment.'
}
