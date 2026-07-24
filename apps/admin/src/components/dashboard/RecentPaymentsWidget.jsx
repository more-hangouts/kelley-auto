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
import PaymentsOutlinedIcon from '@mui/icons-material/PaymentsOutlined'
import { useQuery } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import dayjs from 'dayjs'

import { getRecentPayments } from '../../services/api'
import { formatUSD } from '../../utils/money'

const METHOD_LABEL = {
  cash: 'Cash',
  check: 'Check',
  card: 'Card',
  transfer: 'Bank',
  zelle: 'Zelle',
  other: 'Other',
}

export default function RecentPaymentsWidget() {
  const query = useQuery({
    queryKey: ['dashboard', 'recent-payments'],
    queryFn: () => getRecentPayments(10),
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
          <PaymentsOutlinedIcon color="action" />
          <Typography variant="h6">Recent payments</Typography>
        </Stack>

        {query.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : query.error ? (
          <Alert severity="error">Could not load recent payments.</Alert>
        ) : query.data.length === 0 ? (
          <Stack spacing={1.25} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              No payments recorded yet.
            </Typography>
            <Button
              size="small"
              variant="outlined"
              component={RouterLink}
              to="/invoices?status=sent"
            >
              Review open invoices
            </Button>
          </Stack>
        ) : (
          <Stack divider={<Box sx={{ borderTop: '1px solid', borderColor: 'divider' }} />}>
            {query.data.map((p) => {
              const link = p.event_id
                ? `/events/${p.event_id}/payments`
                : null
              const row = (
                <Box
                  sx={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1.5,
                    py: 1,
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                      {p.contact_name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {dayjs(p.payment_date).format('MMM D')} ·{' '}
                      {METHOD_LABEL[p.method] || p.method}
                      {p.payment_number ? ` · ${p.payment_number}` : ''}
                    </Typography>
                  </Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {formatUSD(p.amount_cents)}
                  </Typography>
                </Box>
              )
              return link ? (
                <Box
                  key={p.id}
                  component={RouterLink}
                  to={link}
                  sx={{
                    color: 'inherit',
                    textDecoration: 'none',
                    '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
                    px: 1,
                    mx: -1,
                    borderRadius: 1,
                  }}
                >
                  {row}
                </Box>
              ) : (
                <Box key={p.id}>{row}</Box>
              )
            })}
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}
