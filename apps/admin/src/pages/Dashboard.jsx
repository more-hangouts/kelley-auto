import { Box, Card, CardContent, Stack, Typography } from '@mui/material'

import AgendaWidget from '../components/dashboard/AgendaWidget'
import ARSummaryWidget from '../components/dashboard/ARSummaryWidget'
import AwaitingSignatureWidget from '../components/dashboard/AwaitingSignatureWidget'
import PipelineCountersWidget from '../components/dashboard/PipelineCountersWidget'
import QuickActionsBar from '../components/dashboard/QuickActionsBar'
import RecentPaymentsWidget from '../components/dashboard/RecentPaymentsWidget'
import SPLHLeaderboardWidget from '../components/dashboard/SPLHLeaderboardWidget'
import { useAuth } from '../contexts/AuthContext'

export default function Dashboard() {
  const { user } = useAuth()
  const greetingName = user?.full_name || user?.username || 'there'

  return (
    <Stack spacing={3}>
      <Card>
        <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Typography variant="h4" gutterBottom>
            Welcome back, {greetingName}
          </Typography>
          <Typography color="text.secondary">
            Jump into the pipeline or look up an invoice.
          </Typography>
        </CardContent>
      </Card>

      <QuickActionsBar />

      <AgendaWidget />

      <PipelineCountersWidget />

      {/* Phase 10 financial widgets */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: {
            xs: '1fr',
            md: 'repeat(2, minmax(0, 1fr))',
            lg: 'repeat(3, minmax(0, 1fr))',
          },
        }}
      >
        <ARSummaryWidget />
        <RecentPaymentsWidget />
        <AwaitingSignatureWidget />
        <SPLHLeaderboardWidget />
      </Box>
    </Stack>
  )
}
