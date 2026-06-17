import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Box,
  CircularProgress,
  Dialog,
  InputAdornment,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  TextField,
  Typography,
} from '@mui/material'
import AddCircleOutlineIcon from '@mui/icons-material/AddCircleOutline'
import CalendarMonthOutlinedIcon from '@mui/icons-material/CalendarMonthOutlined'
import EventOutlinedIcon from '@mui/icons-material/EventOutlined'
import PersonAddOutlinedIcon from '@mui/icons-material/PersonAddOutlined'
import PersonOutlineIcon from '@mui/icons-material/PersonOutline'
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined'
import RequestQuoteOutlinedIcon from '@mui/icons-material/RequestQuoteOutlined'
import SearchIcon from '@mui/icons-material/Search'
import ViewKanbanOutlinedIcon from '@mui/icons-material/ViewKanbanOutlined'

import { useSearch } from '../hooks/useSearch'
import ContactEditDialog from './ContactEditDialog'

// Global Search Phase 2 palette.
//
// Rendering contract: the server returns results pre-ranked and
// pre-formatted (label, sublabel, route). The palette never
// assembles labels or decides routes; that lets future phases ship
// new entity types (invoice, quote, special_order) by extending the
// backend alone — the palette itself sees the new rows as just more
// rows in the same response.
//
// Keyboard model: ArrowUp/ArrowDown move through the visual list
// regardless of section boundaries, Enter activates, Tab jumps to
// the first item of the next group, Esc closes.

const TYPE_LABELS = {
  action: 'Actions',
  contact: 'Contacts',
  event: 'Events',
  invoice: 'Invoices',
  quote: 'Quotes',
}

const TYPE_ICONS = {
  contact: PersonOutlineIcon,
  event: EventOutlinedIcon,
  invoice: ReceiptLongOutlinedIcon,
  quote: RequestQuoteOutlinedIcon,
}

// Locked frontend display order. Server response order is preserved
// inside each group; this map only decides which section appears
// first when both are present. 'action' is locally generated and
// always renders first.
const TYPE_ORDER = ['action', 'event', 'contact', 'invoice', 'quote']

// Static actions shown when the query is empty. Most rows navigate;
// lead capture opens the same dashboard dialog used by QuickActionsBar.
const STATIC_ACTIONS = [
  { id: 'pipeline', label: 'Open pipeline', icon: ViewKanbanOutlinedIcon, route: '/pipeline' },
  { id: 'calendar', label: 'Open calendar', icon: CalendarMonthOutlinedIcon, route: '/calendar' },
  { id: 'invoices', label: 'Browse invoices', icon: ReceiptLongOutlinedIcon, route: '/invoices' },
  { id: 'new-lead', label: 'New walk-in lead', icon: AddCircleOutlineIcon, kind: 'open_new_lead' },
]

function groupResults(results) {
  if (!results) return []
  const byType = new Map()
  for (const r of results) {
    if (!byType.has(r.type)) byType.set(r.type, [])
    byType.get(r.type).push(r)
  }
  // Render in TYPE_ORDER first; any unknown future type falls to the
  // end so a frontend behind on a backend deploy still shows new
  // results.
  const ordered = []
  for (const t of TYPE_ORDER) {
    if (byType.has(t)) {
      ordered.push({ type: t, items: byType.get(t) })
      byType.delete(t)
    }
  }
  for (const [type, items] of byType) {
    ordered.push({ type, items })
  }
  return ordered
}

export default function CommandPalette({ open, onClose, onNewLead }) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [createDialog, setCreateDialog] = useState({ open: false, initialName: '' })
  const inputRef = useRef(null)
  const listRef = useRef(null)

  const { debouncedQuery, isFetching, isError, data } = useSearch(query)

  const searchGroups = useMemo(() => groupResults(data?.results), [data])

  // Action rows are computed entirely on the frontend. They're merged
  // into the same "groups" structure as search results so keyboard
  // navigation and group-tab work uniformly across actions + entities.
  const actionItems = useMemo(() => {
    const trimmed = query.trim()
    if (trimmed.length < 2) {
      // Empty / too-short query: show the standard quick-action list.
      return STATIC_ACTIONS.map((a) => ({
        type: 'action',
        id: a.id,
        kind: a.kind || 'navigate',
        label: a.label,
        icon: a.icon,
        route: a.route,
      }))
    }
    // Wait for the debounce + fetch to settle before judging "no
    // contact match" — otherwise the row flickers in while previous
    // results are still on screen via keepPreviousData.
    if (isFetching || debouncedQuery !== trimmed) return []
    const results = data?.results || []
    const hasContact = results.some((r) => r.type === 'contact')
    if (!hasContact) {
      return [
        {
          type: 'action',
          id: 'create-contact',
          kind: 'create_contact',
          label: `Create contact "${trimmed}"`,
          icon: PersonAddOutlinedIcon,
          initialName: trimmed,
        },
      ]
    }
    return []
  }, [query, debouncedQuery, isFetching, data])

  const groups = useMemo(() => {
    if (actionItems.length === 0) return searchGroups
    return [{ type: 'action', items: actionItems }, ...searchGroups]
  }, [actionItems, searchGroups])

  const flatResults = useMemo(
    () => groups.flatMap((g) => g.items),
    [groups],
  )

  // Reset state on open/close. We do it on transition rather than on
  // unmount so the same Dialog instance is reused; that keeps the
  // input focus reliable across rapid open/close.
  useEffect(() => {
    if (open) {
      setQuery('')
      setActiveIndex(0)
    }
  }, [open])

  // Keep the active index inside the result list as it shrinks /
  // grows during typing.
  useEffect(() => {
    if (activeIndex >= flatResults.length) {
      setActiveIndex(flatResults.length === 0 ? 0 : flatResults.length - 1)
    }
  }, [flatResults.length, activeIndex])

  // Auto-scroll the active row into view so keyboard nav past the
  // viewport edge keeps the selection visible.
  useEffect(() => {
    if (!listRef.current) return
    const el = listRef.current.querySelector(
      `[data-result-index="${activeIndex}"]`,
    )
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' })
    }
  }, [activeIndex, flatResults])

  function handleSelect(result) {
    if (!result) return
    if (result.type === 'action') {
      if (result.kind === 'create_contact') {
        // Hold the palette state open underneath, but hide it visually
        // so the create dialog is the focused surface. Closing the
        // dialog returns the user to the palette with the same query.
        onClose?.()
        setCreateDialog({ open: true, initialName: result.initialName })
        return
      }
      if (result.kind === 'open_new_lead') {
        onClose?.()
        onNewLead?.()
        return
      }
      onClose?.()
      navigate(result.route)
      return
    }
    onClose?.()
    navigate(result.route)
  }

  function indexOfNextGroupStart(currentIndex) {
    let acc = 0
    let currentGroupIdx = -1
    for (let gi = 0; gi < groups.length; gi += 1) {
      const size = groups[gi].items.length
      if (currentIndex < acc + size) {
        currentGroupIdx = gi
        break
      }
      acc += size
    }
    if (currentGroupIdx === -1 || currentGroupIdx === groups.length - 1) {
      return null
    }
    let start = 0
    for (let gi = 0; gi <= currentGroupIdx; gi += 1) {
      start += groups[gi].items.length
    }
    return start
  }

  function handleKeyDown(e) {
    if (flatResults.length === 0) {
      // Esc still propagates to Dialog's onClose; nothing else does
      // anything useful when there are no results.
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (i + 1) % flatResults.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(
        (i) => (i - 1 + flatResults.length) % flatResults.length,
      )
    } else if (e.key === 'Enter') {
      e.preventDefault()
      handleSelect(flatResults[activeIndex])
    } else if (e.key === 'Tab') {
      const nextStart = indexOfNextGroupStart(activeIndex)
      if (nextStart !== null) {
        e.preventDefault()
        setActiveIndex(nextStart)
      }
      // If there's no next group, fall through to default Tab so
      // focus leaves the palette via natural tab order.
    }
  }

  let bodyState = null
  if (isError) {
    bodyState = 'error'
  } else if (flatResults.length === 0 && !isFetching) {
    // Only the rare "query >= 2, settled, no entity matches AND we did
    // not surface a create-contact row" branch — currently unreachable
    // because the create-contact row always fills the empty contact
    // case. Kept as a guard against future refactors.
    bodyState = 'empty'
  }

  return (
    <>
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      slotProps={{
        paper: {
          sx: {
            position: 'absolute',
            top: 80,
            m: 0,
            borderRadius: 2,
          },
        },
      }}
    >
      <Box sx={{ p: 1.5, borderBottom: '1px solid', borderColor: 'divider' }}>
        <TextField
          inputRef={inputRef}
          fullWidth
          size="medium"
          placeholder="Search events, contacts, invoices…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          autoFocus
          autoComplete="off"
          variant="standard"
          InputProps={{
            disableUnderline: true,
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon
                  fontSize="small"
                  sx={{ color: 'text.secondary' }}
                />
              </InputAdornment>
            ),
            endAdornment: isFetching ? (
              <InputAdornment position="end">
                <CircularProgress size={16} thickness={5} />
              </InputAdornment>
            ) : null,
            sx: { fontSize: 16 },
          }}
        />
      </Box>

      <Box
        ref={listRef}
        sx={{
          maxHeight: '60vh',
          overflowY: 'auto',
          minHeight: 80,
        }}
      >
        {bodyState === 'error' && (
          <Typography
            variant="body2"
            color="error"
            sx={{ px: 2, py: 3, textAlign: 'center' }}
          >
            Search failed. Try again.
          </Typography>
        )}
        {bodyState === 'empty' && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ px: 2, py: 3, textAlign: 'center' }}
          >
            No matches for &ldquo;{debouncedQuery}&rdquo;.
          </Typography>
        )}

        {bodyState === null && (
          <List dense disablePadding>
            {(() => {
              let flatIdx = 0
              return groups.map((group) => (
                <Box key={group.type}>
                  <Typography
                    variant="overline"
                    sx={{
                      display: 'block',
                      px: 2,
                      pt: 1.5,
                      pb: 0.5,
                      color: 'text.secondary',
                      letterSpacing: 0.6,
                      fontSize: 11,
                    }}
                  >
                    {TYPE_LABELS[group.type] || group.type}
                  </Typography>
                  {group.items.map((r) => {
                    const idx = flatIdx
                    flatIdx += 1
                    const selected = idx === activeIndex
                    // Action rows carry their own icon component; entity
                    // rows look it up by `type` from TYPE_ICONS.
                    const ResultIcon = r.icon || TYPE_ICONS[r.type]
                    return (
                      <ListItem
                        key={`${r.type}-${r.id}`}
                        disablePadding
                        data-result-index={idx}
                      >
                        <ListItemButton
                          selected={selected}
                          onMouseEnter={() => setActiveIndex(idx)}
                          onClick={() => handleSelect(r)}
                          sx={{
                            px: 2,
                            py: 1,
                            '&.Mui-selected': {
                              bgcolor: 'action.selected',
                            },
                          }}
                        >
                          {ResultIcon && (
                            <ListItemIcon
                              sx={{
                                color: selected ? 'primary.main' : 'text.secondary',
                                minWidth: 34,
                              }}
                            >
                              <ResultIcon fontSize="small" />
                            </ListItemIcon>
                          )}
                          <ListItemText
                            primary={r.label}
                            secondary={r.sublabel || null}
                            primaryTypographyProps={{
                              fontSize: 14,
                              fontWeight: 500,
                              noWrap: true,
                            }}
                            secondaryTypographyProps={{
                              fontSize: 12,
                              noWrap: true,
                            }}
                          />
                        </ListItemButton>
                      </ListItem>
                    )
                  })}
                </Box>
              ))
            })()}
          </List>
        )}
      </Box>
      </Dialog>
      <ContactEditDialog
        open={createDialog.open}
        mode="create"
        initialName={createDialog.initialName}
        onClose={() => setCreateDialog({ open: false, initialName: '' })}
        onSaved={(saved) => {
          // POST response shape is { contact, was_new }. Navigate to
          // the (possibly pre-existing) contact in either case so the
          // user lands somewhere useful.
          if (saved?.contact?.id) {
            navigate(`/contacts/${saved.contact.id}`)
          }
        }}
      />
    </>
  )
}
