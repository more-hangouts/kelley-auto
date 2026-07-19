import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'

import InvoiceEditor from '../components/InvoiceEditor'
import QuoteEditor from '../components/QuoteEditor'
import { listQuotes } from '../services/api'
import { formatUSD } from '../utils/money'

const STATUS_LABEL = {
  draft: 'Draft',
  sent: 'Sent',
  approved: 'Approved',
  rejected: 'Rejected',
  converted: 'Converted',
  expired: 'Expired',
  cancelled: 'Cancelled',
}

const STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  approved: 'success',
  rejected: 'default',
  converted: 'info',
  expired: 'warning',
  cancelled: 'default',
}

function formatRelative(iso) {
  if (!iso) return ''
  const ts = new Date(iso).getTime()
  const diffMs = Date.now() - ts
  const minutes = Math.round(diffMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

function lastActionTimestamp(q) {
  return (
    q.converted_at ||
    q.signature_signed_at ||
    q.approved_at ||
    q.sent_at ||
    q.updated_at ||
    q.created_at
  )
}

export default function QuotesSection({
  event,
  contactId,
  contactName,
  onArrivePrompt,
}) {
  const [quotes, setQuotes] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [refreshTick, setRefreshTick] = useState(0)

  // Editor mode is a small state machine:
  //   none           — both drawers closed
  //   quote(id|null) — QuoteEditor open
  //   invoice(id)    — InvoiceEditor open (after conversion)
  const [editor, setEditor] = useState({ kind: 'none' })

  const eventId = event?.id || null

  useEffect(() => {
    if (!eventId) {
      setQuotes([])
      return
    }
    let cancelled = false
    setLoadError(null)
    listQuotes(eventId)
      .then((rows) => {
        if (!cancelled) setQuotes(rows || [])
      })
      .catch(() => {
        if (cancelled) return
        setLoadError('Could not load quotes.')
      })
    return () => {
      cancelled = true
    }
  }, [eventId, refreshTick])

  function handleNewQuote() {
    setEditor({ kind: 'quote', id: null })
  }
  function handleEditQuote(id) {
    setEditor({ kind: 'quote', id })
  }
  function handleCloseEditor() {
    setEditor({ kind: 'none' })
    setRefreshTick((n) => n + 1)
  }
  function handleConverted(invoiceId) {
    // QuoteEditor's onConverted carries the new invoice id when it
    // wires the call. Fall through gracefully if a future version of
    // the editor changes that contract; the user can reach the invoice
    // through the admin path anyway.
    if (invoiceId) {
      setEditor({ kind: 'invoice', id: invoiceId })
    } else {
      setEditor({ kind: 'none' })
    }
    setRefreshTick((n) => n + 1)
  }

  if (!event) {
    return (
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="overline" color="text.secondary">
              Quotes
            </Typography>
            <Button size="small" variant="contained" disabled>
              New quote
            </Button>
          </Stack>
          <Alert
            severity="info"
            action={
              onArrivePrompt && (
                <Button color="inherit" size="small" onClick={onArrivePrompt}>
                  Mark arrived
                </Button>
              )
            }
          >
            Quotes attach to a CRM event. Mark the appointment arrived
            to start one.
          </Alert>
        </CardContent>
      </Card>
    )
  }

  return (
    <>
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 1 }}
          >
            <Typography variant="overline" color="text.secondary">
              Quotes
            </Typography>
            <Button
              size="small"
              variant="contained"
              startIcon={<AddIcon />}
              onClick={handleNewQuote}
            >
              New quote
            </Button>
          </Stack>

          {loadError && <Alert severity="error">{loadError}</Alert>}

          {quotes === null && !loadError ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={20} />
            </Box>
          ) : quotes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No quotes yet.
            </Typography>
          ) : (
            <Stack
              divider={
                <Box sx={{ borderTop: '1px solid', borderColor: 'divider' }} />
              }
            >
              {quotes.map((q) => (
                <Stack
                  key={q.id}
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{ py: 1.25 }}
                >
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Stack direction="row" spacing={1} alignItems="baseline">
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {q.quote_number || 'Draft'}
                      </Typography>
                      <Chip
                        size="small"
                        label={STATUS_LABEL[q.status] || q.status}
                        color={STATUS_COLOR[q.status] || 'default'}
                        variant={q.status === 'approved' ? 'filled' : 'outlined'}
                      />
                    </Stack>
                    <Typography variant="caption" color="text.secondary">
                      {formatUSD(q.total_cents)} · {formatRelative(lastActionTimestamp(q))}
                    </Typography>
                  </Box>
                  <IconButton
                    size="small"
                    onClick={() => handleEditQuote(q.id)}
                    aria-label={`Open quote ${q.quote_number || q.id}`}
                  >
                    <EditOutlinedIcon fontSize="small" />
                  </IconButton>
                </Stack>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>

      <QuoteEditor
        open={editor.kind === 'quote'}
        onClose={handleCloseEditor}
        onCreated={(newId) => setEditor({ kind: 'quote', id: newId })}
        onConverted={handleConverted}
        eventId={eventId}
        eventDate={event.event_date}
        contactId={contactId}
        contactName={contactName}
        quoteId={editor.kind === 'quote' ? editor.id : null}
      />

      <InvoiceEditor
        open={editor.kind === 'invoice'}
        onClose={handleCloseEditor}
        onCreated={(newId) => setEditor({ kind: 'invoice', id: newId })}
        eventId={eventId}
        eventDate={event.event_date}
        contactId={contactId}
        contactName={contactName}
        invoiceId={editor.kind === 'invoice' ? editor.id : null}
      />
    </>
  )
}
