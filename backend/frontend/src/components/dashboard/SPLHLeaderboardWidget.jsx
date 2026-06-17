import {
  Alert,
  Box,
  Card,
  CardContent,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material'
import LeaderboardIcon from '@mui/icons-material/Leaderboard'
import { useQuery } from '@tanstack/react-query'

import { getSplhLeaderboard } from '../../services/api'
import { formatUSD } from '../../utils/money'

export default function SPLHLeaderboardWidget() {
  const query = useQuery({
    queryKey: ['dashboard', 'splh-leaderboard'],
    queryFn: () => getSplhLeaderboard({ limit: 5 }),
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
          <LeaderboardIcon color="action" />
          <Box>
            <Typography variant="h6">Sales per labor hour</Typography>
            {query.data ? (
              <Typography variant="caption" color="text.secondary">
                {query.data.from_date} to {query.data.to_date}
              </Typography>
            ) : null}
          </Box>
        </Stack>

        {query.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : query.error ? (
          <Alert severity="error">Could not load SPLH leaderboard.</Alert>
        ) : query.data.rows.length === 0 ? (
          <Box
            sx={{
              border: '1px dashed',
              borderColor: 'divider',
              borderRadius: 1,
              px: 2,
              py: 2.5,
            }}
          >
            <Typography variant="body2" color="text.secondary">
              No paid attributed sales with matched hours this week.
            </Typography>
          </Box>
        ) : (
          <Stack divider={<Divider flexItem />} spacing={1.25}>
            {query.data.rows.map((row, index) => {
              const name = row.full_name || row.username || `User ${row.user_id}`
              return (
                <Stack
                  key={row.user_id}
                  direction="row"
                  spacing={1.5}
                  alignItems="center"
                  justifyContent="space-between"
                >
                  <Stack direction="row" spacing={1.25} alignItems="center">
                    <RankBadge rank={index + 1} />
                    <Box>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {formatUSD(row.revenue_cents)} sales /{' '}
                        {row.actual_hours.toFixed(2)} hrs /{' '}
                        {row.invoice_count} invoices
                      </Typography>
                    </Box>
                  </Stack>
                  <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                    {row.splh_cents_per_hour == null
                      ? 'N/A'
                      : `${formatUSD(row.splh_cents_per_hour)}/hr`}
                  </Typography>
                </Stack>
              )
            })}
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}

function RankBadge({ rank }) {
  return (
    <Box
      sx={{
        width: 28,
        height: 28,
        borderRadius: '50%',
        bgcolor: rank === 1 ? 'success.light' : 'action.hover',
        color: rank === 1 ? 'success.contrastText' : 'text.secondary',
        display: 'grid',
        placeItems: 'center',
        fontSize: 13,
        fontWeight: 700,
        flex: '0 0 auto',
      }}
    >
      {rank}
    </Box>
  )
}
