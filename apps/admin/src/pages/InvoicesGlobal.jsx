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
  InputAdornment,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import ClearIcon from '@mui/icons-material/Clear'
import SearchIcon from '@mui/icons-material/Search'
import { Link as RouterLink, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { searchInvoices } from '../services/api'
import { formatUSD } from '../utils/money'

const STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'draft', label: 'Draft' },
  { value: 'sent', label: 'Sent' },
  { value: 'partial', label: 'Partial' },
  { value: 'paid', label: 'Paid' },
  { value: 'cancelled', label: 'Cancelled' },
]

const STATUS_COLOR = {
  draft: 'default',
  sent: 'primary',
  partial: 'warning',
  paid: 'success',
  cancelled: 'default',
  reversed: 'default',
}

export default function InvoicesGlobal() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Local form state, hydrated from URL query params for shareable links.
  const [q, setQ] = useState(searchParams.get('q') || '')
  const [status, setStatus] = useState(searchParams.get('status') || '')
  const [dateFrom, setDateFrom] = useState(searchParams.get('date_from') || '')
  const [dateTo, setDateTo] = useState(searchParams.get('date_to') || '')

  // Stable query key so React Query caches each search.
  const params = {
    q: searchParams.get('q') || undefined,
    status: searchParams.get('status') || undefined,
    dateFrom: searchParams.get('date_from') || undefined,
    dateTo: searchParams.get('date_to') || undefined,
  }
  const invoicesQuery = useQuery({
    queryKey: ['invoices', 'search', params],
    queryFn: () => searchInvoices(params),
  })

  // Refresh form fields when the URL changes (e.g. browser back).
  useEffect(() => {
    setQ(searchParams.get('q') || '')
    setStatus(searchParams.get('status') || '')
    setDateFrom(searchParams.get('date_from') || '')
    setDateTo(searchParams.get('date_to') || '')
  }, [searchParams])

  const submit = () => {
    const next = {}
    if (q) next.q = q
    if (status) next.status = status
    if (dateFrom) next.date_from = dateFrom
    if (dateTo) next.date_to = dateTo
    setSearchParams(next)
  }

  const clear = () => {
    setQ('')
    setStatus('')
    setDateFrom('')
    setDateTo('')
    setSearchParams({})
  }

  const invoices = invoicesQuery.data || []
  const hasActiveFilters = Boolean(
    params.q || params.status || params.dateFrom || params.dateTo,
  )

  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        <Typography variant="h4" gutterBottom>
          Invoices
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          Search by invoice number, customer name, status, or date.
        </Typography>

        <Stack
          direction={{ xs: 'column', md: 'row' }}
          spacing={2}
          sx={{ mb: 3 }}
          alignItems={{ xs: 'stretch', md: 'flex-end' }}
        >
          <TextField
            label="Search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="Invoice number or customer name"
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
              endAdornment: q ? (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setQ('')}>
                    <ClearIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ) : null,
            }}
            sx={{ flex: 1 }}
          />
          <TextField
            select
            label="Status"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            sx={{ minWidth: 150 }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <MenuItem key={opt.value || 'any'} value={opt.value}>
                {opt.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="Issued from"
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            InputLabelProps={{ shrink: true }}
            sx={{ minWidth: 160 }}
          />
          <TextField
            label="Issued to"
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            InputLabelProps={{ shrink: true }}
            sx={{ minWidth: 160 }}
          />
          <Stack direction="row" spacing={1}>
            <Button variant="contained" onClick={submit}>
              Search
            </Button>
            <Button onClick={clear}>Clear</Button>
          </Stack>
        </Stack>

        {invoicesQuery.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress />
          </Box>
        ) : invoicesQuery.error ? (
          <Alert severity="error">
            {invoicesQuery.error?.response?.data?.detail || 'Search failed.'}
          </Alert>
        ) : invoices.length === 0 ? (
          <Box sx={{ p: 4, textAlign: 'center' }}>
            <Stack spacing={1.5} alignItems="center">
              <Typography color="text.secondary">
                {hasActiveFilters
                  ? 'No invoices match those filters.'
                  : 'No invoices yet.'}
              </Typography>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                {hasActiveFilters && (
                  <Button variant="outlined" onClick={clear}>
                    Clear filters
                  </Button>
                )}
                <Button
                  variant={hasActiveFilters ? 'text' : 'contained'}
                  component={RouterLink}
                  to="/pipeline"
                >
                  Open pipeline
                </Button>
              </Stack>
            </Stack>
          </Box>
        ) : (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Number</TableCell>
                <TableCell>Customer</TableCell>
                <TableCell>Issued</TableCell>
                <TableCell>Due</TableCell>
                <TableCell align="right">Total</TableCell>
                <TableCell align="right">Balance</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {invoices.map((inv) => (
                <TableRow
                  key={inv.id}
                  component={RouterLink}
                  to={`/events/${inv.event_id}/invoices`}
                  sx={{
                    textDecoration: 'none',
                    cursor: 'pointer',
                    '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
                  }}
                  hover
                >
                  <TableCell>{inv.invoice_number || 'Draft'}</TableCell>
                  <TableCell>{inv.contact_name}</TableCell>
                  <TableCell>{dayjs(inv.issue_date).format('MMM D, YYYY')}</TableCell>
                  <TableCell>
                    {inv.due_date ? dayjs(inv.due_date).format('MMM D, YYYY') : '—'}
                  </TableCell>
                  <TableCell align="right">{formatUSD(inv.total_cents)}</TableCell>
                  <TableCell align="right">
                    {inv.balance_cents > 0 ? (
                      <Typography component="span" sx={{ color: 'warning.main', fontWeight: 600 }}>
                        {formatUSD(inv.balance_cents)}
                      </Typography>
                    ) : (
                      formatUSD(inv.balance_cents)
                    )}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      label={inv.status}
                      color={STATUS_COLOR[inv.status] || 'default'}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
