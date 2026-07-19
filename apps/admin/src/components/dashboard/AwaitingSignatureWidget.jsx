import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material'
import RequestQuoteOutlinedIcon from '@mui/icons-material/RequestQuoteOutlined'
import { useQuery } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'

import { getAwaitingSignatureQuotes } from '../../services/api'
import { formatUSD } from '../../utils/money'

export default function AwaitingSignatureWidget() {
  const query = useQuery({
    queryKey: ['dashboard', 'awaiting-signature'],
    queryFn: () => getAwaitingSignatureQuotes({ minAgeDays: 3, limit: 25 }),
    staleTime: 60_000,
  })

  return (
    <Card>
      <CardContent>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mb: 1.5 }}
        >
          <RequestQuoteOutlinedIcon color="action" />
          <Typography variant="h6">Quotes awaiting signature</Typography>
        </Stack>

        {query.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : query.error ? (
          <Alert severity="error">Could not load quote list.</Alert>
        ) : query.data.length === 0 ? (
          <Stack spacing={1.25} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              Nothing pending more than 3 days. Nice work.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              component={RouterLink}
              to="/pipeline"
            >
              Open pipeline
            </Button>
          </Stack>
        ) : (
          <Stack divider={<Box sx={{ borderTop: '1px solid', borderColor: 'divider' }} />}>
            {query.data.map((q) => (
              <Box
                key={q.id}
                component={RouterLink}
                to={`/events/${q.event_id}/quotes`}
                sx={{
                  color: 'inherit',
                  textDecoration: 'none',
                  '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
                  px: 1,
                  mx: -1,
                  borderRadius: 1,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  py: 1,
                }}
              >
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                    {q.contact_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {q.quote_number || 'Draft'} · sent {q.days_since_sent}d ago
                  </Typography>
                </Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {formatUSD(q.total_cents)}
                </Typography>
              </Box>
            ))}
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}
