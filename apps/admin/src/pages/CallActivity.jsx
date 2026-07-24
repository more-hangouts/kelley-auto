import {
  Box,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { getCallActivitySummary, getRecentCallActivity } from '../services/api'

// Manager view of native-dialer call activity (Phase 7). Per-rep counts for the
// business-local day + a recent-call list across the floor. Read-only; admin
// scope (the API 403s sales tokens). "Call initiated" is shown as pending —
// never counted as connected.

const OUTCOME_LABELS = {
  call_initiated: 'Call initiated',
  connected: 'Connected',
  left_voicemail: 'Left voicemail',
  no_answer: 'No answer',
  busy: 'Busy',
  wrong_number: 'Wrong number',
  cancelled: 'Cancelled',
}

const OUTCOME_COLOR = {
  connected: 'success',
  left_voicemail: 'info',
  busy: 'warning',
  wrong_number: 'error',
  no_answer: 'default',
  cancelled: 'default',
  call_initiated: 'default',
}

export default function CallActivity() {
  const summaryQ = useQuery({
    queryKey: ['call-activity-summary'],
    queryFn: () => getCallActivitySummary(),
  })
  const recentQ = useQuery({
    queryKey: ['call-activity-recent'],
    queryFn: () => getRecentCallActivity({ limit: 50 }),
  })

  const summary = summaryQ.data
  const reps = Array.isArray(summary?.reps) ? summary.reps : []
  const recent = Array.isArray(recentQ.data?.recent) ? recentQ.data.recent : []

  return (
    <Box sx={{ maxWidth: 1000, mx: 'auto' }}>
      <Stack direction="row" alignItems="center" spacing={1.5} mb={3}>
        <PhoneOutlinedIcon color="primary" />
        <Box>
          <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: 1 }}>
            Call activity
          </Typography>
          <Typography variant="h5" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
            Calls today
            {summary?.calls_today != null ? ` · ${summary.calls_today}` : ''}
          </Typography>
          {summary?.date && (
            <Typography variant="body2" color="text.secondary">
              {dayjs(summary.date).format('dddd, MMM D, YYYY')} (shop time)
            </Typography>
          )}
        </Box>
      </Stack>

      <Paper variant="outlined" sx={{ borderRadius: 2, mb: 3 }}>
        <Box sx={{ px: 3, py: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            By salesperson
          </Typography>
        </Box>
        {summaryQ.isLoading ? (
          <Box sx={{ p: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : reps.length === 0 ? (
          <Box sx={{ px: 3, py: 2 }}>
            <Typography variant="body2" color="text.secondary">
              No calls logged today.
            </Typography>
          </Box>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Salesperson</TableCell>
                  <TableCell align="right">Initiated</TableCell>
                  <TableCell align="right">Connected</TableCell>
                  <TableCell align="right">Voicemail</TableCell>
                  <TableCell align="right">No answer</TableCell>
                  <TableCell align="right">Pending</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {reps.map((r) => (
                  <TableRow key={r.salesperson_user_id ?? 'unknown'}>
                    <TableCell>{r.salesperson_display_name || 'Unknown rep'}</TableCell>
                    <TableCell align="right">{r.initiated}</TableCell>
                    <TableCell align="right">{r.connected}</TableCell>
                    <TableCell align="right">{r.left_voicemail}</TableCell>
                    <TableCell align="right">{r.no_answer}</TableCell>
                    <TableCell align="right">{r.pending}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Paper variant="outlined" sx={{ borderRadius: 2 }}>
        <Box sx={{ px: 3, py: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            Recent calls
          </Typography>
        </Box>
        {recentQ.isLoading ? (
          <Box sx={{ p: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : recent.length === 0 ? (
          <Box sx={{ px: 3, py: 2 }}>
            <Typography variant="body2" color="text.secondary">
              No recent calls.
            </Typography>
          </Box>
        ) : (
          <Stack divider={<Box sx={{ borderBottom: '1px solid', borderColor: 'divider' }} />}>
            {recent.map((c) => (
              <Box key={c.id} sx={{ px: 3, py: 1.5, display: 'flex', alignItems: 'center', gap: 2 }}>
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                    {c.contact_display_name || 'Unknown contact'}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {c.salesperson_display_name || 'Unknown rep'}
                    {c.created_at ? ` · ${dayjs(c.created_at).format('MMM D, h:mm A')}` : ''}
                  </Typography>
                </Box>
                <Chip
                  size="small"
                  variant="outlined"
                  color={OUTCOME_COLOR[c.outcome] || 'default'}
                  label={OUTCOME_LABELS[c.outcome] || OUTCOME_LABELS.call_initiated}
                />
              </Box>
            ))}
          </Stack>
        )}
      </Paper>
    </Box>
  )
}
