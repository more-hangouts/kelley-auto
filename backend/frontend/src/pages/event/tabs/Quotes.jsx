import { useEffect, useMemo, useState } from 'react'
import {
  useNavigate,
  useOutletContext,
  useSearchParams,
} from 'react-router-dom'
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
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import DownloadIcon from '@mui/icons-material/Download'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import QuoteEditor from '../../../components/QuoteEditor'
import { listQuotes, viewQuotePdf } from '../../../services/api'
import { formatUSD } from '../../../utils/money'

const STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  approved: 'success',
  rejected: 'default',
  converted: 'info',
  expired: 'warning',
  cancelled: 'default',
}

const STATUS_LABEL = {
  draft: 'Draft',
  sent: 'Sent',
  approved: 'Approved',
  rejected: 'Rejected',
  converted: 'Converted',
  expired: 'Expired',
  cancelled: 'Cancelled',
}

export default function Quotes() {
  const { event } = useOutletContext()
  const eventId = event.id
  const contactId = event.primary_contact?.id
  const contactName = event.primary_contact?.display_name
  const navigate = useNavigate()

  const quotesQuery = useQuery({
    queryKey: ['event', eventId, 'quotes'],
    queryFn: () => listQuotes(eventId),
  })

  const [editorOpen, setEditorOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [searchParams, setSearchParams] = useSearchParams()

  // Phase 10.6: ?edit=<quoteId> deep-link. Lets the Overview tab's
  // buyer-journey rows open this tab and pop the editor directly. We
  // strip the param after consuming it so a refresh doesn't reopen.
  useEffect(() => {
    const editParam = searchParams.get('edit')
    if (!editParam) return
    const idNum = Number(editParam)
    if (Number.isFinite(idNum)) {
      setEditingId(idNum)
      setEditorOpen(true)
    }
    const next = new URLSearchParams(searchParams)
    next.delete('edit')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  const quotes = useMemo(() => quotesQuery.data || [], [quotesQuery.data])

  const openNew = () => {
    setEditingId(null)
    setEditorOpen(true)
  }
  const openEdit = (id) => {
    setEditingId(id)
    setEditorOpen(true)
  }

  return (
    <Box>
      {/* Action bar */}
      <Stack direction="row" spacing={1} mb={2}>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openNew}>
          New quote
        </Button>
      </Stack>

      {/* List */}
      {quotesQuery.isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
        </Box>
      ) : quotesQuery.error ? (
        <Alert severity="error">
          {quotesQuery.error?.response?.data?.detail || 'Failed to load quotes.'}
        </Alert>
      ) : quotes.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <DescriptionOutlinedIcon
            sx={{ fontSize: 36, color: 'text.disabled', mb: 1 }}
          />
          <Typography variant="body2" color="text.secondary">
            No quotes yet. Send one for the customer to review and sign before
            you bill anything.
          </Typography>
        </Paper>
      ) : (
        <Paper sx={{ overflow: 'hidden' }}>
          {quotes.map((q, i) => (
            <Box
              key={q.id}
              onClick={() => openEdit(q.id)}
              sx={{
                p: 2,
                cursor: 'pointer',
                borderBottom: i < quotes.length - 1 ? '1px solid' : 'none',
                borderColor: 'divider',
                display: 'flex',
                alignItems: 'center',
                gap: 2,
                flexWrap: 'wrap',
                '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
              }}
            >
              <DescriptionOutlinedIcon color="action" />
              <Box sx={{ flex: '1 1 200px', minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 500 }}>
                  {q.quote_number || 'Draft'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {q.sent_at
                    ? `sent ${dayjs(q.sent_at).format('MMM D, YYYY')}`
                    : `created ${dayjs(q.created_at).format('MMM D, YYYY')}`}
                </Typography>
              </Box>
              {q.expires_at && q.status === 'sent' && (
                <Chip
                  size="small"
                  label={`Expires ${dayjs(q.expires_at).format('MMM D')}`}
                  variant="outlined"
                />
              )}
              {q.status === 'converted' && q.converted_invoice_id && (
                <Chip
                  size="small"
                  label={`→ Invoice #${q.converted_invoice_id}`}
                  variant="outlined"
                  onClick={(e) => {
                    e.stopPropagation()
                    navigate(`/events/${eventId}/invoices`)
                  }}
                  sx={{ cursor: 'pointer' }}
                />
              )}
              <Box sx={{ minWidth: 110, textAlign: 'right' }}>
                <Typography variant="caption" color="text.secondary" display="block">
                  Total
                </Typography>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {formatUSD(q.total_cents)}
                </Typography>
              </Box>
              <Chip
                size="small"
                label={STATUS_LABEL[q.status] || q.status}
                color={STATUS_COLOR[q.status] || 'default'}
                sx={{ minWidth: 64 }}
              />
              <Button
                size="small"
                variant="text"
                startIcon={<DownloadIcon />}
                onClick={(e) => {
                  e.stopPropagation()
                  viewQuotePdf(q.id).catch(() => {
                    alert('Could not open the quote PDF. Please try again.')
                  })
                }}
              >
                PDF
              </Button>
            </Box>
          ))}
        </Paper>
      )}

      <QuoteEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onCreated={(newId) => setEditingId(newId)}
        onConverted={() => navigate(`/events/${eventId}/invoices`)}
        eventId={eventId}
        eventDate={event.event_date}
        contactId={contactId}
        contactName={contactName}
        quoteId={editingId}
      />
    </Box>
  )
}
