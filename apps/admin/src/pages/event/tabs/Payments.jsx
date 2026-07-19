import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DownloadIcon from '@mui/icons-material/Download'
import PaymentsOutlinedIcon from '@mui/icons-material/PaymentsOutlined'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import PaymentRecorder from '../../../components/PaymentRecorder'
import RefundDialog from '../../../components/RefundDialog'
import {
  getPayment,
  listPaymentsForEvent,
  viewPaymentReceiptPdf,
} from '../../../services/api'
import { formatUSD } from '../../../utils/money'

const STATUS_COLOR = {
  pending: 'warning',
  completed: 'success',
  partially_refunded: 'warning',
  refunded: 'default',
  failed: 'error',
  cancelled: 'default',
}

const STATUS_LABEL = {
  pending: 'Pending',
  completed: 'Completed',
  partially_refunded: 'Partial refund',
  refunded: 'Refunded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const METHOD_LABEL = {
  cash: 'Cash',
  check: 'Check',
  card: 'Card',
  transfer: 'Bank transfer',
  zelle: 'Zelle',
  other: 'Other',
}

export default function Payments() {
  const { event } = useOutletContext()
  const eventId = event.id
  const contactId = event.primary_contact?.id

  const paymentsQuery = useQuery({
    queryKey: ['event', eventId, 'payments'],
    queryFn: () => listPaymentsForEvent(eventId),
  })

  const [recorderOpen, setRecorderOpen] = useState(false)
  const [refundPaymentId, setRefundPaymentId] = useState(null)

  // Refund dialog needs the FULL payment detail (allocations) so we
  // fetch it on demand when staff click Refund. Cached by ['payment',
  // id] so a re-open is free.
  const refundPaymentQuery = useQuery({
    queryKey: ['payment', refundPaymentId],
    queryFn: () => getPayment(refundPaymentId),
    enabled: refundPaymentId != null,
  })

  const payments = useMemo(() => paymentsQuery.data || [], [paymentsQuery.data])

  const totals = useMemo(() => {
    let received = 0
    let applied = 0
    let unapplied = 0
    let refunded = 0
    for (const p of payments) {
      if (p.status === 'cancelled' || p.status === 'failed') continue
      received += p.amount_cents
      applied += p.applied_cents
      unapplied += p.unapplied_cents
      refunded += p.refunded_cents
    }
    return { received, applied, unapplied, refunded }
  }, [payments])

  const refundable = (p) =>
    ['completed', 'partially_refunded'].includes(p.status) &&
    p.amount_cents - p.refunded_cents > 0

  return (
    <Box>
      {/* Totals */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={3}
          divider={<Box sx={{ borderLeft: '1px solid', borderColor: 'divider' }} />}
        >
          <SummaryCell label="Total received" value={totals.received} />
          <SummaryCell label="Applied" value={totals.applied} valueColor="success.main" />
          <SummaryCell
            label="Unapplied"
            value={totals.unapplied}
            valueColor={totals.unapplied > 0 ? 'warning.main' : 'text.primary'}
          />
          <SummaryCell
            label="Refunded"
            value={totals.refunded}
            valueColor={totals.refunded > 0 ? 'text.secondary' : 'text.primary'}
          />
        </Stack>
      </Paper>

      <Stack direction="row" spacing={1} mb={2}>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setRecorderOpen(true)}
        >
          Record payment
        </Button>
      </Stack>

      {paymentsQuery.isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
        </Box>
      ) : paymentsQuery.error ? (
        <Alert severity="error">
          {paymentsQuery.error?.response?.data?.detail || 'Failed to load payments.'}
        </Alert>
      ) : payments.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <PaymentsOutlinedIcon
            sx={{ fontSize: 36, color: 'text.disabled', mb: 1 }}
          />
          <Typography variant="body2" color="text.secondary">
            No payments yet. Record one when funds are received.
          </Typography>
        </Paper>
      ) : (
        <Paper sx={{ overflow: 'hidden' }}>
          {payments.map((p, i) => (
            <Box
              key={p.id}
              sx={{
                p: 2,
                borderBottom: i < payments.length - 1 ? '1px solid' : 'none',
                borderColor: 'divider',
                display: 'flex',
                alignItems: 'center',
                gap: 2,
                flexWrap: 'wrap',
              }}
            >
              <PaymentsOutlinedIcon color="action" />
              <Box sx={{ flex: '1 1 200px', minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  {p.payment_number || 'Payment'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {dayjs(p.payment_date).format('MMM D, YYYY')} ·{' '}
                  {METHOD_LABEL[p.method] || p.method}
                </Typography>
              </Box>
              <Box sx={{ minWidth: 110, textAlign: 'right' }}>
                <Typography variant="caption" color="text.secondary" display="block">
                  Amount
                </Typography>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {formatUSD(p.amount_cents)}
                </Typography>
              </Box>
              {p.unapplied_cents > 0 && (
                <Box sx={{ minWidth: 110, textAlign: 'right' }}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Unapplied
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600, color: 'warning.main' }}>
                    {formatUSD(p.unapplied_cents)}
                  </Typography>
                </Box>
              )}
              {p.refunded_cents > 0 && (
                <Box sx={{ minWidth: 110, textAlign: 'right' }}>
                  <Typography variant="caption" color="text.secondary" display="block">
                    Refunded
                  </Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600, color: 'text.secondary' }}>
                    {formatUSD(p.refunded_cents)}
                  </Typography>
                </Box>
              )}
              <Chip
                size="small"
                label={STATUS_LABEL[p.status] || p.status}
                color={STATUS_COLOR[p.status] || 'default'}
                sx={{ minWidth: 100 }}
              />
              {refundable(p) && (
                <Button
                  size="small"
                  variant="outlined"
                  color="error"
                  onClick={() => setRefundPaymentId(p.id)}
                >
                  Refund
                </Button>
              )}
              <Button
                size="small"
                variant="text"
                startIcon={<DownloadIcon />}
                onClick={() => {
                  viewPaymentReceiptPdf(p.id).catch(() => {
                    alert('Could not open the receipt. Please try again.')
                  })
                }}
              >
                Receipt
              </Button>
            </Box>
          ))}
        </Paper>
      )}

      <PaymentRecorder
        open={recorderOpen}
        onClose={() => setRecorderOpen(false)}
        eventId={eventId}
        contactId={contactId}
      />
      <RefundDialog
        open={refundPaymentId != null && !!refundPaymentQuery.data}
        onClose={() => setRefundPaymentId(null)}
        payment={refundPaymentQuery.data}
        eventId={eventId}
      />
    </Box>
  )
}

function SummaryCell({ label, value, valueColor }) {
  return (
    <Box sx={{ flex: 1 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h6" sx={{ fontWeight: 600, color: valueColor }}>
        {formatUSD(value)}
      </Typography>
    </Box>
  )
}
