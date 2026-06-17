import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from '@mui/material'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import RestoreOutlinedIcon from '@mui/icons-material/RestoreOutlined'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import RecordDependenciesDialog from '../components/RecordDependenciesDialog'
import {
  listRecycleBin,
  restoreContact,
  restoreEvent,
  restoreEventParticipant,
  restoreSpecialOrder,
} from '../services/api'

dayjs.extend(relativeTime)

// D3-D2 of the CRM record deletion plan
// (docs/CRM_RECORD_DELETION_PLAN.md). Read-only Recycle Bin for the
// four CRM-core entity types plus per-row restore. Mounted at
// /settings/recycle-bin. Restore reuses RecordDependenciesDialog in
// confirmMode="restore" so the operator sees the dependency snapshot
// (and any can_restore=false blocker) before committing.

const TABS = [
  { value: 'contact', label: 'Contacts' },
  { value: 'event', label: 'Events' },
  { value: 'event_participant', label: 'Participants' },
  { value: 'special_order', label: 'Special orders' },
]

const REASON_LABEL = {
  duplicate: 'Duplicate',
  test_record: 'Test record',
  created_by_mistake: 'Created by mistake',
  customer_requested: 'Customer requested removal',
  other: 'Other',
}

function describeRestoreError(err) {
  const detail = err?.response?.data?.detail
  const code = detail?.code
  if (code === 'parent_archived') {
    return 'Restore the parent (contact or event) before this row.'
  }
  if (code === 'restore_phone_collision') {
    return 'Another live contact already has this phone. Archive that contact or change its phone first.'
  }
  if (code === 'quinceanera_slot_taken') {
    return 'Another active quinceañera already occupies this event.'
  }
  if (code === 'contact_not_found' || code === 'event_not_found'
      || code === 'participant_not_found' || code === 'special_order_not_found') {
    return 'This record no longer exists. Reload the bin.'
  }
  return detail?.message || err?.message || 'Could not restore this record.'
}

function restoreFn(item) {
  switch (item.entity_type) {
    case 'contact':
      return () => restoreContact(item.entity_id)
    case 'event':
      return () => restoreEvent(item.entity_id)
    case 'event_participant':
      return () => restoreEventParticipant(item.parent_event_id, item.entity_id)
    case 'special_order':
      return () => restoreSpecialOrder(item.parent_event_id, item.entity_id)
    default:
      return () => Promise.reject(new Error('unknown entity_type'))
  }
}

export default function RecycleBin() {
  const [tab, setTab] = useState('contact')
  const [restoreTarget, setRestoreTarget] = useState(null) // RecycleBinItem
  const [toast, setToast] = useState(null)
  const queryClient = useQueryClient()

  const listQuery = useInfiniteQuery({
    queryKey: ['recycle-bin', tab],
    queryFn: ({ pageParam = null }) =>
      listRecycleBin({ entityType: tab, beforeId: pageParam, pageSize: 25 }),
    getNextPageParam: (lastPage) => lastPage.next_before_id ?? undefined,
    initialPageParam: null,
  })

  const restoreMutation = useMutation({
    mutationFn: () => {
      if (!restoreTarget) return Promise.reject(new Error('no target'))
      return restoreFn(restoreTarget)()
    },
    onSuccess: () => {
      const label = restoreTarget?.display_name || 'Record'
      setRestoreTarget(null)
      setToast({
        severity: 'success',
        message: `${label} restored.`,
      })
      // The restored row leaves this entity's bin and re-enters the
      // active world; invalidate enough to refresh both surfaces.
      queryClient.invalidateQueries({ queryKey: ['recycle-bin', tab] })
      queryClient.invalidateQueries({ queryKey: ['record-dependencies'] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      if (restoreTarget?.entity_type === 'contact') {
        queryClient.invalidateQueries({ queryKey: ['contact'] })
      }
      if (restoreTarget?.entity_type === 'event') {
        queryClient.invalidateQueries({ queryKey: ['event'] })
      }
      if (
        restoreTarget?.entity_type === 'event_participant'
        || restoreTarget?.entity_type === 'special_order'
      ) {
        queryClient.invalidateQueries({ queryKey: ['event'] })
      }
    },
  })

  const pages = listQuery.data?.pages || []
  const items = pages.flatMap((p) => p.items || [])

  return (
    <Box sx={{ maxWidth: 920, mx: 'auto' }}>
      <Stack direction="row" spacing={1} alignItems="center" mb={2}>
        <IconButton
          component={RouterLink}
          to="/settings"
          aria-label="back to settings"
        >
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="overline" color="text.secondary">
          Settings · Recycle Bin
        </Typography>
      </Stack>

      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography variant="h5" gutterBottom>
            Recycle Bin
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Archived CRM records stay here until you restore them. Hard purge
            is not enabled yet.
          </Typography>

          <Tabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            variant="scrollable"
            scrollButtons="auto"
            sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}
          >
            {TABS.map((t) => (
              <Tab key={t.value} value={t.value} label={t.label} />
            ))}
          </Tabs>

          {listQuery.isLoading && (
            <Stack alignItems="center" sx={{ py: 4 }}>
              <CircularProgress size={28} />
            </Stack>
          )}

          {listQuery.isError && (
            <Alert severity="error">
              {listQuery.error?.response?.data?.detail?.message
                || listQuery.error?.message
                || 'Could not load the Recycle Bin.'}
            </Alert>
          )}

          {!listQuery.isLoading && !listQuery.isError && items.length === 0 && (
            <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
              Nothing here. Archived records show up here automatically.
            </Typography>
          )}

          {items.length > 0 && (
            <Stack spacing={1.5} divider={<Divider flexItem />}>
              {items.map((item) => (
                <RecycleRow
                  key={`${item.entity_type}-${item.entity_id}`}
                  item={item}
                  onRestoreClick={() => setRestoreTarget(item)}
                />
              ))}
            </Stack>
          )}

          {listQuery.hasNextPage && (
            <Stack alignItems="center" sx={{ pt: 2 }}>
              <Button
                onClick={() => listQuery.fetchNextPage()}
                disabled={listQuery.isFetchingNextPage}
                size="small"
                variant="outlined"
              >
                {listQuery.isFetchingNextPage ? 'Loading…' : 'Load more'}
              </Button>
            </Stack>
          )}
        </CardContent>
      </Card>

      <RecordDependenciesDialog
        entityType={restoreTarget?.entity_type}
        entityId={restoreTarget?.entity_id}
        open={Boolean(restoreTarget)}
        onClose={() => {
          if (!restoreMutation.isPending) {
            setRestoreTarget(null)
            restoreMutation.reset()
          }
        }}
        title={
          restoreTarget
            ? `Restore ${restoreTarget.display_name}?`
            : 'Restore record?'
        }
        confirmLabel="Restore"
        confirmMode="restore"
        isSubmitting={restoreMutation.isPending}
        submitError={
          restoreMutation.isError
            ? describeRestoreError(restoreMutation.error)
            : null
        }
        onConfirm={() => restoreMutation.mutate()}
      />

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {toast ? (
          <Alert
            severity={toast.severity}
            onClose={() => setToast(null)}
            variant="filled"
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </Box>
  )
}

function RecycleRow({ item, onRestoreClick }) {
  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      alignItems={{ xs: 'flex-start', sm: 'center' }}
      spacing={2}
      sx={{ py: 1.25 }}
    >
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
          {item.display_name}
        </Typography>
        {item.secondary_label && (
          <Typography variant="caption" color="text.secondary" noWrap>
            {item.secondary_label}
          </Typography>
        )}
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mt: 0.5, flexWrap: 'wrap' }}
        >
          <Tooltip title={dayjs(item.deleted_at).format('MMM D, YYYY h:mm A')}>
            <Typography variant="caption" color="text.secondary">
              Archived {dayjs(item.deleted_at).fromNow()}
            </Typography>
          </Tooltip>
          {item.deleted_by_display_name && (
            <Typography variant="caption" color="text.secondary">
              · by {item.deleted_by_display_name}
            </Typography>
          )}
          {item.reason && (
            <Chip
              size="small"
              label={REASON_LABEL[item.reason] || item.reason}
              variant="outlined"
              sx={{ fontSize: 11, height: 22 }}
            />
          )}
        </Stack>
      </Box>
      <Button
        size="small"
        variant="outlined"
        startIcon={<RestoreOutlinedIcon />}
        onClick={onRestoreClick}
      >
        Restore
      </Button>
    </Stack>
  )
}
