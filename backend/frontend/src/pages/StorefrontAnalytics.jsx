import { useState } from 'react'
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Grid,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import { useQuery } from '@tanstack/react-query'

import { getStorefrontAnalyticsSummary } from '../services/api'

// Sprint 3: owner-visible storefront analytics — the whole funnel from
// anonymous browsing to booked dollars, grouped by marketing channel.
// Sources are derived server-side (UTM → ad click-id → referrer), and
// revenue rows inherit the FIRST-touch source of the lead they belong to,
// so "which channel produced dollars" is answerable, not just "which
// channel produced clicks".

const RANGES = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
]

const FUNNEL_LABELS = {
  page_view: 'Page views',
  vehicle_view: 'Vehicle views',
  lead_form_opened: 'Form opened',
  lead_form_started: 'Form started',
  lead_submitted: 'Leads',
  payment_received: 'Payments',
}

function fmtInt(n) {
  return typeof n === 'number' ? n.toLocaleString() : '—'
}

function fmtMoney(cents) {
  if (typeof cents !== 'number') return '—'
  return (cents / 100).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  })
}

function channelLabel(row) {
  const medium = row.medium && row.medium !== '(none)' ? ` / ${row.medium}` : ''
  return `${row.source}${medium}`
}

function StatTile({ label, value, hint }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, height: '100%' }}>
      <Typography variant="overline" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="h5" sx={{ fontWeight: 600 }}>
        {value}
      </Typography>
      {hint ? (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      ) : null}
    </Paper>
  )
}

function Funnel({ funnel }) {
  const max = Math.max(1, ...funnel.map((s) => s.count))
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
        Funnel
      </Typography>
      <Stack spacing={1.25}>
        {funnel.map((step) => (
          <Box key={step.event_name}>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.25 }}>
              <Typography variant="body2">
                {FUNNEL_LABELS[step.event_name] || step.event_name}
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {fmtInt(step.count)}
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={(step.count / max) * 100}
              sx={{ height: 8, borderRadius: 1 }}
            />
          </Box>
        ))}
      </Stack>
    </Paper>
  )
}

function ChannelTable({ title, rows, columns, empty }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
        {title}
      </Typography>
      {rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {empty}
        </Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Channel</TableCell>
                {columns.map((c) => (
                  <TableCell key={c.key} align="right">
                    {c.label}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={channelLabel(row)} hover>
                  <TableCell>
                    <Chip
                      size="small"
                      label={channelLabel(row)}
                      variant={row.source === '(direct)' ? 'outlined' : 'filled'}
                    />
                  </TableCell>
                  {columns.map((c) => (
                    <TableCell key={c.key} align="right">
                      {c.money ? fmtMoney(row[c.key]) : fmtInt(row[c.key])}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  )
}

function DailyTraffic({ daily }) {
  const max = Math.max(1, ...daily.map((d) => d.page_view))
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
        Daily traffic
      </Typography>
      {daily.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No traffic recorded in this window.
        </Typography>
      ) : (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'flex-end',
            gap: '3px',
            height: 120,
            overflowX: 'auto',
          }}
        >
          {daily.map((d) => (
            <Tooltip
              key={d.day}
              title={`${d.day}: ${fmtInt(d.page_view)} views · ${fmtInt(
                d.vehicle_view
              )} vehicle views · ${fmtInt(d.lead_submitted)} leads`}
            >
              <Box
                sx={{
                  flex: '1 0 8px',
                  minWidth: 8,
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'flex-end',
                  height: '100%',
                }}
              >
                <Box
                  sx={{
                    height: `${Math.max(3, (d.page_view / max) * 100)}%`,
                    bgcolor: d.lead_submitted > 0 ? 'success.main' : 'primary.main',
                    borderRadius: '2px 2px 0 0',
                    opacity: 0.85,
                  }}
                />
              </Box>
            </Tooltip>
          ))}
        </Box>
      )}
      <Typography variant="caption" color="text.secondary">
        Bar height = page views (shop-local days). Green bars are days that
        produced a lead.
      </Typography>
    </Paper>
  )
}

function TopVehicles({ vehicles }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
        Most-viewed vehicles
      </Typography>
      {vehicles.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No vehicle views recorded in this window.
        </Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Vehicle</TableCell>
                <TableCell align="right">Views</TableCell>
                <TableCell align="right">Shoppers</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {vehicles.map((v) => (
                <TableRow key={v.vehicle_catalog_item_id} hover>
                  <TableCell>
                    {v.label || v.listing_code || `#${v.vehicle_catalog_item_id}`}
                    {v.listing_code ? (
                      <Typography
                        component="span"
                        variant="caption"
                        color="text.secondary"
                        sx={{ ml: 0.75 }}
                      >
                        {v.listing_code}
                      </Typography>
                    ) : null}
                  </TableCell>
                  <TableCell align="right">{fmtInt(v.views)}</TableCell>
                  <TableCell align="right">{fmtInt(v.visitors)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  )
}

export default function StorefrontAnalytics() {
  const [days, setDays] = useState(30)
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['storefront-analytics', days],
    queryFn: () => getStorefrontAnalyticsSummary(days),
  })

  const funnel = data?.funnel || []
  const byName = Object.fromEntries(funnel.map((s) => [s.event_name, s.count]))
  const leads = byName.lead_submitted || 0
  const vehicleViews = byName.vehicle_view || 0

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        gap={1}
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 600 }}>
            Website analytics
          </Typography>
          <Typography variant="body2" color="text.secondary">
            First-party storefront funnel — browsing to leads to dollars, by
            marketing channel.
          </Typography>
        </Box>
        <Stack direction="row" alignItems="center" gap={1}>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={days}
            onChange={(_, v) => v != null && setDays(v)}
          >
            {RANGES.map((r) => (
              <ToggleButton key={r.value} value={r.value}>
                {r.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Tooltip title="Refresh">
            <IconButton onClick={() => refetch()} disabled={isFetching}>
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {isLoading ? (
        <Box sx={{ py: 6, display: 'flex', justifyContent: 'center' }}>
          <CircularProgress />
        </Box>
      ) : isError ? (
        <Alert severity="error">Couldn't load storefront analytics.</Alert>
      ) : (
        <Grid container spacing={2}>
          <Grid item xs={6} sm={3}>
            <StatTile
              label="Shoppers"
              value={fmtInt(data?.uniques?.visitors)}
              hint={`${fmtInt(data?.uniques?.sessions)} visits`}
            />
          </Grid>
          <Grid item xs={6} sm={3}>
            <StatTile label="Vehicle views" value={fmtInt(vehicleViews)} />
          </Grid>
          <Grid item xs={6} sm={3}>
            <StatTile label="Leads" value={fmtInt(leads)} />
          </Grid>
          <Grid item xs={6} sm={3}>
            <StatTile
              label="Revenue attributed"
              value={fmtMoney(data?.total_revenue_cents)}
              hint="payments on deals from the site"
            />
          </Grid>

          <Grid item xs={12}>
            <DailyTraffic daily={data?.daily || []} />
          </Grid>

          <Grid item xs={12} md={4}>
            <Funnel funnel={funnel} />
          </Grid>
          <Grid item xs={12} md={8}>
            <Stack spacing={2}>
              <ChannelTable
                title="Leads by channel"
                rows={data?.leads_by_source || []}
                columns={[{ key: 'leads', label: 'Leads' }]}
                empty="No attributed leads in this window."
              />
              <ChannelTable
                title="Revenue by channel"
                rows={data?.revenue_by_source || []}
                columns={[
                  { key: 'payments', label: 'Payments' },
                  { key: 'revenue_cents', label: 'Revenue', money: true },
                ]}
                empty="No payments attributed to website leads in this window."
              />
            </Stack>
          </Grid>

          <Grid item xs={12} md={6}>
            <ChannelTable
              title="Traffic by channel"
              rows={data?.traffic_by_source || []}
              columns={[
                { key: 'page_views', label: 'Page views' },
                { key: 'visitors', label: 'Shoppers' },
              ]}
              empty="No traffic recorded in this window."
            />
          </Grid>
          <Grid item xs={12} md={6}>
            <TopVehicles vehicles={data?.top_vehicles || []} />
          </Grid>
        </Grid>
      )}
    </Box>
  )
}
