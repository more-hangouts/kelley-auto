import { useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItemButton,
  ListItemText,
  Popover,
  Stack,
  Typography,
} from '@mui/material'
import ViewKanbanOutlinedIcon from '@mui/icons-material/ViewKanbanOutlined'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'

import { getEventBoard, getPipelineCounts } from '../../services/api'
import { useCommandPalette } from '../../contexts/CommandPaletteContext'
import { formatUSD } from '../../utils/money'

export default function PipelineCountersWidget() {
  const navigate = useNavigate()
  const palette = useCommandPalette()
  const counts = useQuery({
    queryKey: ['dashboard', 'pipeline-counts'],
    queryFn: () => getPipelineCounts(),
    staleTime: 60_000,
  })

  // The popover fetches the full board lazily — only when the user
  // actually clicks a chip. Shares the cache with /pipeline so going
  // from popover -> Pipeline page is instant.
  const [anchorEl, setAnchorEl] = useState(null)
  const [activeLane, setActiveLane] = useState(null)

  const board = useQuery({
    queryKey: ['events', 'board', 'quinceanera'],
    queryFn: () => getEventBoard('quinceanera'),
    enabled: Boolean(activeLane),
    staleTime: 30_000,
  })

  const laneCards = useMemo(() => {
    if (!activeLane || !board.data) return []
    const col = board.data.columns.find((c) => c.code === activeLane.code)
    return col?.cards || []
  }, [activeLane, board.data])

  function openLane(event, lane) {
    setAnchorEl(event.currentTarget)
    setActiveLane(lane)
  }

  function closeLane() {
    setAnchorEl(null)
    setActiveLane(null)
  }

  function openCard(card) {
    closeLane()
    navigate(`/events/${card.id}/overview`)
  }

  const lanes = counts.data?.lanes ?? []
  const active = lanes
    .filter((l) => !l.is_terminal)
    .sort((a, b) => a.sort_order - b.sort_order)
  const terminal = lanes.filter((l) => l.is_terminal)
  const activeCount = active.reduce((sum, lane) => sum + lane.count, 0)

  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
          <ViewKanbanOutlinedIcon color="action" />
          <Typography variant="h6">Pipeline</Typography>
        </Stack>

        {counts.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : counts.error ? (
          <Alert severity="error">Could not load pipeline counts.</Alert>
        ) : active.length === 0 || activeCount === 0 ? (
          <Stack spacing={1.25} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              All caught up. No active leads in the pipeline.
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <Button
                size="small"
                variant="contained"
                onClick={palette.openNewLead}
              >
                Add walk-in lead
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => navigate('/pipeline')}
              >
                Open pipeline
              </Button>
            </Stack>
            {terminal.length > 0 && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: 'block' }}
              >
                {terminal
                  .sort((a, b) => a.sort_order - b.sort_order)
                  .map((l) => `${l.count} ${l.label.toLowerCase()}`)
                  .join(' · ')}
              </Typography>
            )}
          </Stack>
        ) : (
          <>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
              {active.map((lane) => (
                <Chip
                  key={lane.code}
                  label={`${lane.label} · ${lane.count}`}
                  variant={lane.count > 0 ? 'filled' : 'outlined'}
                  color={lane.count > 0 ? 'primary' : 'default'}
                  onClick={(e) => (lane.count > 0 ? openLane(e, lane) : null)}
                  sx={{ cursor: lane.count > 0 ? 'pointer' : 'default' }}
                />
              ))}
            </Box>
            {terminal.length > 0 && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: 'block', mt: 1.5 }}
              >
                {terminal
                  .sort((a, b) => a.sort_order - b.sort_order)
                  .map((l) => `${l.count} ${l.label.toLowerCase()}`)
                  .join(' · ')}
              </Typography>
            )}
          </>
        )}
      </CardContent>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={closeLane}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        slotProps={{
          paper: { sx: { width: 360, maxHeight: 420, mt: 0.5 } },
        }}
      >
        <Box sx={{ px: 2, py: 1.5 }}>
          <Typography variant="subtitle2">
            {activeLane?.label}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {activeLane?.count}{' '}
            {activeLane?.count === 1 ? 'lead' : 'leads'}
          </Typography>
        </Box>
        <Divider />
        {board.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={20} />
          </Box>
        ) : board.error ? (
          <Box sx={{ p: 2 }}>
            <Alert severity="error">Could not load leads.</Alert>
          </Box>
        ) : laneCards.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No leads in this lane.
          </Typography>
        ) : (
          <List dense disablePadding sx={{ maxHeight: 300, overflowY: 'auto' }}>
            {laneCards.map((card) => (
              <ListItemButton key={card.id} onClick={() => openCard(card)}>
                <ListItemText
                  primary={card.primary_contact?.display_name || card.event_name}
                  primaryTypographyProps={{ fontSize: 14, fontWeight: 500, noWrap: true }}
                  secondary={
                    <Box component="span" sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                      <Box component="span" sx={{ color: 'text.secondary' }}>
                        {card.event_date
                          ? dayjs(card.event_date).format('MMM D, YYYY')
                          : 'No date'}
                      </Box>
                      {card.outstanding_balance_cents > 0 && (
                        <Chip
                          size="small"
                          label={formatUSD(card.outstanding_balance_cents)}
                          color="warning"
                          variant="outlined"
                          sx={{ height: 18, fontSize: 11 }}
                        />
                      )}
                    </Box>
                  }
                  secondaryTypographyProps={{ component: 'span', fontSize: 12 }}
                />
              </ListItemButton>
            ))}
          </List>
        )}
        <Divider />
        <Box sx={{ p: 1, display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            size="small"
            onClick={() => {
              closeLane()
              navigate('/pipeline')
            }}
          >
            Open pipeline
          </Button>
        </Box>
      </Popover>
    </Card>
  )
}
