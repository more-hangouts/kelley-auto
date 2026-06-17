import {
  Alert,
  Box,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material'
import AccountBalanceIcon from '@mui/icons-material/AccountBalance'
import { useQuery } from '@tanstack/react-query'

import { getArSummary } from '../../services/api'
import { formatUSD } from '../../utils/money'

export default function ARSummaryWidget() {
  const query = useQuery({
    queryKey: ['dashboard', 'ar-summary'],
    queryFn: getArSummary,
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
          <AccountBalanceIcon color="action" />
          <Typography variant="h6">Accounts receivable</Typography>
        </Stack>

        {query.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : query.error ? (
          <Alert severity="error">Could not load AR summary.</Alert>
        ) : (
          <Stack spacing={1.5}>
            <Stat
              label="Outstanding balance"
              value={formatUSD(query.data.outstanding_balance_cents)}
              subtext={`${query.data.outstanding_invoice_count} unpaid invoices`}
              emphasis
            />
            <Stat
              label="Overdue"
              value={formatUSD(query.data.overdue_balance_cents)}
              subtext={`${query.data.overdue_invoice_count} past due date`}
              warn={query.data.overdue_balance_cents > 0}
            />
            <Stat
              label="Deposits this month"
              value={formatUSD(query.data.deposits_collected_this_month_cents)}
              subtext="Net of refunds"
            />
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value, subtext, emphasis = false, warn = false }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant={emphasis ? 'h5' : 'h6'}
        sx={{
          fontWeight: 600,
          color: warn ? 'warning.dark' : 'text.primary',
        }}
      >
        {value}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {subtext}
      </Typography>
    </Box>
  )
}
