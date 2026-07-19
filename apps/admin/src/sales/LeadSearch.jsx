import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Box,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import { useQuery } from '@tanstack/react-query'

import { salesSearchLeads } from '../services/api'

const DEBOUNCE_MS = 250
const MIN_LENGTH = 2

const TYPE_LABEL = {
  appointment: 'Appointment',
  event: 'Lead',
  contact: 'Contact',
}

const TYPE_COLOR = {
  appointment: 'primary',
  event: 'secondary',
  contact: 'default',
}

// Phase 3: sales-safe global lead search box. Backed by
// /api/sales/search/leads (no invoice/quote/payment fields). Each
// result navigates into an appointment the stylist can already open
// through /api/sales/appointments/*. Empty input → no fetch; the
// dropdown only renders when there's an active query.
export default function LeadSearch() {
  const navigate = useNavigate()
  const [rawQuery, setRawQuery] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const trimmed = rawQuery.trim()
    const id = setTimeout(() => setDebounced(trimmed), DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [rawQuery])

  const enabled = debounced.length >= MIN_LENGTH
  const { data, isFetching, error } = useQuery({
    queryKey: ['sales', 'search', 'leads', debounced],
    queryFn: ({ signal }) =>
      salesSearchLeads({ q: debounced, limit: 5, signal }),
    enabled,
    staleTime: 30_000,
  })

  const results = enabled ? data?.results ?? [] : []
  const showEmpty = enabled && !isFetching && !error && results.length === 0
  const showResults = enabled && results.length > 0

  return (
    <Box>
      <TextField
        fullWidth
        size="small"
        placeholder="Search by name, phone, code, or event"
        value={rawQuery}
        onChange={(e) => setRawQuery(e.target.value)}
        autoComplete="off"
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SearchIcon fontSize="small" />
            </InputAdornment>
          ),
          endAdornment: isFetching ? (
            <InputAdornment position="end">
              <CircularProgress size={16} />
            </InputAdornment>
          ) : null,
        }}
      />

      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }}>
          Search hiccupped. Try again.
        </Alert>
      )}

      {showEmpty && (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 1.5, px: 1 }}
        >
          No matches for "{debounced}".
        </Typography>
      )}

      {showResults && (
        <Stack spacing={1} sx={{ mt: 1.5 }}>
          {results.map((r) => (
            <Card key={`${r.type}:${r.id}`} variant="outlined">
              <CardActionArea onClick={() => navigate(r.route)}>
                <CardContent sx={{ py: 1.5 }}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{ mb: 0.25 }}
                  >
                    <Typography variant="body1" sx={{ fontWeight: 600 }}>
                      {r.label}
                    </Typography>
                    <Chip
                      size="small"
                      label={TYPE_LABEL[r.type] || r.type}
                      color={TYPE_COLOR[r.type] || 'default'}
                      variant="outlined"
                    />
                  </Stack>
                  {r.sublabel && (
                    <Typography variant="body2" color="text.secondary">
                      {r.sublabel}
                    </Typography>
                  )}
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Stack>
      )}
    </Box>
  )
}
