import { useMemo, useState } from 'react'
import {
  Alert,
  Avatar,
  Box,
  Card,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import EventIcon from '@mui/icons-material/Event'
import GroupIcon from '@mui/icons-material/Group'
import PaletteIcon from '@mui/icons-material/Palette'
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined'
import ShoppingBagOutlinedIcon from '@mui/icons-material/ShoppingBagOutlined'
import DirectionsCarOutlinedIcon from '@mui/icons-material/DirectionsCarOutlined'
import FingerprintOutlinedIcon from '@mui/icons-material/FingerprintOutlined'
import SpeedOutlinedIcon from '@mui/icons-material/SpeedOutlined'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  rectIntersection,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import { getEventBoard, patchEventStatus } from '../services/api'
import EventQuickViewDrawer from '../components/EventQuickViewDrawer'
import { formatUSD } from '../utils/money'

dayjs.extend(relativeTime)

// Inventory-status chip color for vehicle_sale cards. Mirrors the
// vehicle_status CHECK values; unknowns fall through to a neutral chip.
const VEHICLE_STATUS_COLORS = {
  available: { bg: 'success.light', fg: 'success.dark' },
  pending: { bg: 'warning.light', fg: 'warning.dark' },
  sold: { bg: 'info.light', fg: 'info.dark' },
  delivered: { bg: 'action.selected', fg: 'text.secondary' },
  wholesale: { bg: 'action.hover', fg: 'text.secondary' },
  hidden: { bg: 'action.hover', fg: 'text.disabled' },
}

function formatMileage(mi) {
  if (mi == null) return null
  return `${mi.toLocaleString()} mi`
}

function columnCollisionDetection(args) {
  const pointerCollisions = pointerWithin(args)
  return pointerCollisions.length > 0 ? pointerCollisions : rectIntersection(args)
}

function avatarInitials(name) {
  if (!name) return '?'
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() || '')
    .join('') || '?'
}

function daysSince(iso) {
  if (!iso) return null
  const now = dayjs()
  const then = dayjs(iso)
  const diff = now.diff(then, 'day')
  if (diff <= 0) return 'today'
  return `${diff}d`
}

function daysUntil(date) {
  if (!date) return null
  const target = dayjs(date)
  const diff = target.diff(dayjs(), 'day')
  if (diff < 0) return `${Math.abs(diff)}d ago`
  if (diff === 0) return 'today'
  return `in ${diff}d`
}

function moveCardOptimistic(board, eventId, newStatus) {
  if (!board) return board
  const columns = board.columns.map((col) => ({ ...col, cards: [...col.cards] }))
  let movedCard = null
  for (const col of columns) {
    const idx = col.cards.findIndex((c) => c.id === eventId)
    if (idx >= 0) {
      movedCard = col.cards[idx]
      col.cards.splice(idx, 1)
      break
    }
  }
  if (!movedCard) return board
  const dest = columns.find((c) => c.code === newStatus)
  if (!dest) return board
  dest.cards.unshift({
    ...movedCard,
    status: newStatus,
    status_changed_at: new Date().toISOString(),
  })
  return { ...board, columns }
}

function CardBody({ card, dragging = false }) {
  return (
    <Card
      sx={{
        p: 1.5,
        borderRadius: 2,
        border: '1px solid',
        borderColor: 'divider',
        boxShadow: dragging ? 6 : 0,
        cursor: dragging ? 'grabbing' : 'grab',
        bgcolor: 'background.paper',
        userSelect: 'none',
        transition: 'box-shadow 120ms ease, opacity 120ms ease',
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography
            variant="subtitle2"
            sx={{ fontWeight: 600, lineHeight: 1.3, mb: 0.25 }}
            noWrap
          >
            {card.event_name}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            {card.primary_contact?.display_name}
          </Typography>
        </Box>
        <Stack direction="row" spacing={0.5} alignItems="center">
          {card.has_outstanding_invoice && (
            <Tooltip title="Outstanding invoice — sent and unpaid">
              <ReceiptLongOutlinedIcon
                sx={{ fontSize: 18, color: 'warning.main' }}
              />
            </Tooltip>
          )}
          {card.owner?.full_name && (
            <Tooltip title={card.owner.full_name}>
              <Avatar
                sx={{
                  width: 26,
                  height: 26,
                  fontSize: 11,
                  bgcolor: 'primary.main',
                  ml: 1,
                }}
              >
                {avatarInitials(card.owner.full_name)}
              </Avatar>
            </Tooltip>
          )}
        </Stack>
      </Stack>

      <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" mt={1.25}>
        <Tooltip title="Time in this status">
          <Chip
            size="small"
            icon={<AccessTimeIcon sx={{ fontSize: 14 }} />}
            label={daysSince(card.status_changed_at) || '—'}
            variant="outlined"
            sx={{ fontSize: 11, height: 22 }}
          />
        </Tooltip>
        <Tooltip
          title={
            card.event_date
              ? `Event on ${dayjs(card.event_date).format('MMM D, YYYY')}`
              : 'Event date not set yet'
          }
        >
          <Chip
            size="small"
            icon={<EventIcon sx={{ fontSize: 14 }} />}
            label={card.event_date ? daysUntil(card.event_date) : 'TBD'}
            variant="outlined"
            sx={{
              fontSize: 11,
              height: 22,
              ...(card.event_date
                ? null
                : {
                    opacity: 0.5,
                    color: 'text.secondary',
                    borderStyle: 'dashed',
                  }),
            }}
          />
        </Tooltip>
        {card.vehicle && (
          <>
            {[card.vehicle.year, card.vehicle.make, card.vehicle.model]
              .filter(Boolean)
              .join(' ') && (
              <Tooltip title="Linked vehicle">
                <Chip
                  size="small"
                  icon={<DirectionsCarOutlinedIcon sx={{ fontSize: 14 }} />}
                  label={[card.vehicle.year, card.vehicle.make, card.vehicle.model]
                    .filter(Boolean)
                    .join(' ')}
                  variant="outlined"
                  sx={{ fontSize: 11, height: 22, maxWidth: 170 }}
                />
              </Tooltip>
            )}
            {card.vehicle.vehicle_status && (
              <Tooltip title={`Inventory status: ${card.vehicle.vehicle_status}`}>
                <Chip
                  size="small"
                  label={card.vehicle.vehicle_status}
                  sx={{
                    fontSize: 11,
                    height: 22,
                    textTransform: 'capitalize',
                    fontWeight: 600,
                    bgcolor:
                      VEHICLE_STATUS_COLORS[card.vehicle.vehicle_status]?.bg ||
                      'action.hover',
                    color:
                      VEHICLE_STATUS_COLORS[card.vehicle.vehicle_status]?.fg ||
                      'text.secondary',
                  }}
                />
              </Tooltip>
            )}
            {card.vehicle.mileage != null && (
              <Tooltip title="Mileage">
                <Chip
                  size="small"
                  icon={<SpeedOutlinedIcon sx={{ fontSize: 14 }} />}
                  label={formatMileage(card.vehicle.mileage)}
                  variant="outlined"
                  sx={{ fontSize: 11, height: 22 }}
                />
              </Tooltip>
            )}
            {card.vehicle.vin && (
              <Tooltip title={`VIN ${card.vehicle.vin}`}>
                <Chip
                  size="small"
                  icon={<FingerprintOutlinedIcon sx={{ fontSize: 14 }} />}
                  label={card.vehicle.vin.slice(-6)}
                  variant="outlined"
                  sx={{ fontSize: 11, height: 22 }}
                />
              </Tooltip>
            )}
          </>
        )}
        {card.court_size != null && (
          <Tooltip title="Court size">
            <Chip
              size="small"
              icon={<GroupIcon sx={{ fontSize: 14 }} />}
              label={card.court_size}
              variant="outlined"
              sx={{ fontSize: 11, height: 22 }}
            />
          </Tooltip>
        )}
        {card.quince_theme && (
          <Tooltip title={card.quince_theme}>
            <Chip
              size="small"
              icon={<PaletteIcon sx={{ fontSize: 14 }} />}
              label={card.quince_theme}
              variant="outlined"
              sx={{ fontSize: 11, height: 22, maxWidth: 140 }}
            />
          </Tooltip>
        )}
        {card.outstanding_balance_cents > 0 && (
          <Tooltip
            title={`${formatUSD(card.outstanding_balance_cents)} outstanding across sent or partial invoices`}
          >
            <Chip
              size="small"
              icon={<ReceiptLongOutlinedIcon sx={{ fontSize: 14 }} />}
              label={formatUSD(card.outstanding_balance_cents)}
              sx={{
                fontSize: 11,
                height: 22,
                bgcolor: 'warning.light',
                color: 'warning.dark',
                fontWeight: 600,
                '& .MuiChip-icon': { color: 'warning.dark' },
              }}
            />
          </Tooltip>
        )}
        {card.named_buyer_count > 0 && (
          <Tooltip
            title={`${card.named_buyer_count} named buyer${card.named_buyer_count === 1 ? '' : 's'} with their own appointment, quote, or invoice on this event`}
          >
            <Chip
              size="small"
              icon={<ShoppingBagOutlinedIcon sx={{ fontSize: 14 }} />}
              label={`${card.named_buyer_count} buyer${card.named_buyer_count === 1 ? '' : 's'}`}
              variant="outlined"
              sx={{ fontSize: 11, height: 22 }}
            />
          </Tooltip>
        )}
      </Stack>
    </Card>
  )
}

function DraggableCard({ card, onCardClick }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `card-${card.id}`,
    data: { card },
  })
  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0 : 1,
  }
  return (
    <Box
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      onClick={() => {
        if (!isDragging) onCardClick?.(card)
      }}
      sx={{ outline: 'none' }}
    >
      <CardBody card={card} />
    </Box>
  )
}

function DroppableColumn({ column, onCardClick }) {
  const { setNodeRef, isOver } = useDroppable({
    id: `column-${column.code}`,
    data: { columnCode: column.code },
  })
  const isTerminal = column.is_terminal
  return (
    <Box
      sx={{
        minWidth: 300,
        maxWidth: 300,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ px: 1, mb: 1 }}
      >
        <Typography
          variant="overline"
          sx={{
            color: isTerminal ? 'text.disabled' : 'text.primary',
            fontWeight: 600,
            letterSpacing: 0.4,
          }}
        >
          {column.label}
        </Typography>
        <Chip
          size="small"
          label={column.cards.length}
          sx={{ height: 20, fontSize: 11, bgcolor: 'action.hover' }}
        />
      </Stack>
      <Box
        ref={setNodeRef}
        sx={{
          flex: 1,
          minHeight: 200,
          p: 1,
          borderRadius: 2,
          bgcolor: isOver ? 'action.selected' : 'action.hover',
          transition: 'background-color 120ms ease',
          overflowY: 'auto',
        }}
      >
        <Stack spacing={1}>
          {column.cards.map((card) => (
            <DraggableCard
              key={card.id}
              card={card}
              onCardClick={onCardClick}
            />
          ))}
          {column.cards.length === 0 && (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{ textAlign: 'center', py: 4, display: 'block' }}
            >
              No events
            </Typography>
          )}
        </Stack>
      </Box>
    </Box>
  )
}

export default function Pipeline({
  eventType = 'quinceanera',
  title = 'Pipeline',
  subtitleNoun = 'Quinceañera events',
}) {
  const queryClient = useQueryClient()
  const queryKey = ['events', 'board', eventType]
  const [selectedCard, setSelectedCard] = useState(null)
  const [activeDrag, setActiveDrag] = useState(null)

  const { data: board, isLoading, isFetching, error, refetch } = useQuery({
    queryKey,
    queryFn: () => getEventBoard(eventType),
  })

  const changeStatus = useMutation({
    mutationFn: ({ eventId, newStatus }) => patchEventStatus(eventId, newStatus),
    onError: (_err, vars) => {
      if (vars?.previous) queryClient.setQueryData(queryKey, vars.previous)
    },
    onSettled: (_data, _err, vars) => {
      queryClient.invalidateQueries({ queryKey })
      // Phase 9: kanban drag-drop also emits event.status_changed. Bust
      // the per-event activity cache so opening the event detail right
      // after a drag shows the new row.
      if (vars?.eventId != null) {
        queryClient.invalidateQueries({
          queryKey: ['event', vars.eventId, 'activity'],
        })
      }
    },
  })

  async function commitStatusChange(eventId, newStatus) {
    await queryClient.cancelQueries({ queryKey })
    const previous = queryClient.getQueryData(queryKey)
    if (previous) {
      queryClient.setQueryData(queryKey, moveCardOptimistic(previous, eventId, newStatus))
    }
    changeStatus.mutate({ eventId, newStatus, previous })
  }

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  function handleDragStart(e) {
    setActiveDrag(e.active.data.current?.card || null)
  }
  async function handleDragEnd(e) {
    const { active, over } = e
    if (!over) {
      setActiveDrag(null)
      return
    }
    const card = active.data.current?.card
    const toStatus = over.data.current?.columnCode
    if (!card || !toStatus || card.status === toStatus) {
      setActiveDrag(null)
      return
    }
    await commitStatusChange(card.id, toStatus)
    setActiveDrag(null)
  }
  function handleDragCancel() {
    setActiveDrag(null)
  }

  const totalCards = useMemo(
    () => (board?.columns || []).reduce((acc, c) => acc + c.cards.length, 0),
    [board],
  )

  return (
    <Box
      sx={{
        p: 3,
        height: 'calc(100vh - 64px)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
        <Box>
          <Typography variant="h4">{title}</Typography>
          <Typography variant="body2" color="text.secondary">
            {subtitleNoun} · {totalCards} active
          </Typography>
        </Box>
        <IconButton onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? <CircularProgress size={20} /> : <RefreshIcon />}
        </IconButton>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error?.response?.data?.detail || error.message || 'Failed to load board'}
        </Alert>
      )}

      {isLoading && !board && (
        <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <CircularProgress />
        </Box>
      )}

      {board && (
        <DndContext
          sensors={sensors}
          collisionDetection={columnCollisionDetection}
          autoScroll={{ threshold: { x: 0.1, y: 0 }, acceleration: 5 }}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={handleDragCancel}
        >
          <Box
            sx={{
              flex: 1,
              display: 'flex',
              gap: 2,
              overflowX: 'auto',
              pb: 1,
            }}
          >
            {board.columns.map((column) => (
              <DroppableColumn
                key={column.code}
                column={column}
                onCardClick={setSelectedCard}
              />
            ))}
          </Box>
          <DragOverlay dropAnimation={null}>
            {activeDrag ? (
              <Box sx={{ width: 284 }}>
                <CardBody card={activeDrag} dragging />
              </Box>
            ) : null}
          </DragOverlay>
        </DndContext>
      )}

      <EventQuickViewDrawer
        card={selectedCard}
        onClose={() => setSelectedCard(null)}
        onStatusChange={commitStatusChange}
      />
    </Box>
  )
}
