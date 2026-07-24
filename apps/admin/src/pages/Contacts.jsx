import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  InputAdornment,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import ContactActions from '../components/ContactActions'
import EmailOutlinedIcon from '@mui/icons-material/EmailOutlined'
import PersonAddAltOutlinedIcon from '@mui/icons-material/PersonAddAltOutlined'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'
import SearchIcon from '@mui/icons-material/Search'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'

import ContactEditDialog from '../components/ContactEditDialog'
import { listContacts } from '../services/api'

// The Contacts tab — a browsable rolodex over the whole customer base.
// Website leads and Deals already flow into `contacts` server-side via
// find_or_create_contact, so this page is a pure read/search surface;
// creating here reuses the same ContactEditDialog the detail page owns.

const PAGE_SIZE = 50

const SORTS = [
  { value: 'name', label: 'Name A–Z' },
  { value: 'recent', label: 'Recently updated' },
  { value: 'created', label: 'Newest added' },
]

function initials(name) {
  if (!name) return '?'
  const parts = name.trim().split(/\s+/).slice(0, 2)
  return parts.map((p) => p[0]?.toUpperCase() ?? '').join('') || '?'
}

function ContactRow({ contact, onOpen }) {
  const subtitleParts = []
  if (contact.phone) subtitleParts.push({ icon: PhoneOutlinedIcon, text: contact.phone })
  if (contact.email) subtitleParts.push({ icon: EmailOutlinedIcon, text: contact.email })

  return (
    <Paper
      variant="outlined"
      onClick={() => onOpen(contact.id)}
      sx={{
        px: 2,
        py: 1.5,
        cursor: 'pointer',
        transition: 'border-color 120ms, background-color 120ms',
        '&:hover': { borderColor: 'primary.main', bgcolor: 'action.hover' },
      }}
    >
      <Stack direction="row" spacing={2} alignItems="center">
        <Avatar sx={{ bgcolor: 'action.selected', color: 'primary.main', fontWeight: 600 }}>
          {initials(contact.display_name)}
        </Avatar>

        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography noWrap sx={{ fontWeight: 600 }}>
            {contact.display_name || 'Unknown'}
          </Typography>
          <Stack
            direction="row"
            spacing={1.5}
            sx={{ mt: 0.25, flexWrap: 'wrap', color: 'text.secondary' }}
          >
            {subtitleParts.length === 0 && (
              <Typography variant="body2" color="text.disabled">
                No phone or email
              </Typography>
            )}
            {subtitleParts.map(({ icon: Icon, text }) => (
              <Stack key={text} direction="row" spacing={0.5} alignItems="center" sx={{ minWidth: 0 }}>
                <Icon sx={{ fontSize: 15 }} />
                <Typography variant="body2" noWrap>
                  {text}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>

        <Stack
          direction="row"
          spacing={0.5}
          sx={{ display: { xs: 'none', md: 'flex' }, flexWrap: 'wrap', justifyContent: 'flex-end', maxWidth: 260 }}
        >
          {(contact.tags || []).slice(0, 3).map((t) => (
            <Chip key={t} size="small" label={t} variant="outlined" />
          ))}
        </Stack>

        {contact.event_count > 0 && (
          <Chip
            size="small"
            label={`${contact.event_count} deal${contact.event_count === 1 ? '' : 's'}`}
            sx={{ bgcolor: 'action.selected', fontWeight: 500 }}
          />
        )}

        {/* Call/Message actions — stop propagation so they don't open the row. */}
        <Box onClick={(e) => e.stopPropagation()}>
          <ContactActions contact={contact} source="contacts_list" />
        </Box>

        <ChevronRightIcon sx={{ color: 'text.disabled' }} />
      </Stack>
    </Paper>
  )
}

export default function Contacts() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [searchInput, setSearchInput] = useState('')
  const [query, setQuery] = useState('')
  const [tag, setTag] = useState(null)
  const [sort, setSort] = useState('name')
  const [offset, setOffset] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)

  // Debounce the search box so we don't fire a request per keystroke.
  useEffect(() => {
    const id = setTimeout(() => setQuery(searchInput.trim()), 300)
    return () => clearTimeout(id)
  }, [searchInput])

  // Any filter change resets pagination to the first page.
  useEffect(() => {
    setOffset(0)
  }, [query, tag, sort])

  const { data, isLoading, isError, error, isFetching } = useQuery({
    queryKey: ['contacts', 'list', { query, tag, sort, offset }],
    queryFn: () =>
      listContacts({
        query: query || undefined,
        tag: tag || undefined,
        sort,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: keepPreviousData,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const facets = data?.tags ?? []

  const rangeLabel = useMemo(() => {
    if (total === 0) return '0'
    const from = offset + 1
    const to = Math.min(offset + PAGE_SIZE, total)
    return `${from}–${to} of ${total}`
  }, [offset, total])

  function openContact(id) {
    navigate(`/contacts/${id}`)
  }

  function handleCreated(saved) {
    // POST response is { contact, was_new }. Refresh the list and jump
    // straight to whoever we just created (or matched on dedup).
    queryClient.invalidateQueries({ queryKey: ['contacts', 'list'] })
    const id = saved?.contact?.id
    if (id) navigate(`/contacts/${id}`)
  }

  return (
    <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1100, mx: 'auto' }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={2}
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Contacts
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Every customer and lead — past buyers, website enquiries, and walk-ins.
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<PersonAddAltOutlinedIcon />}
          onClick={() => setCreateOpen(true)}
        >
          Add contact
        </Button>
      </Stack>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 2 }}>
        <TextField
          fullWidth
          size="small"
          placeholder="Search name, phone, or email…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />
        <TextField
          select
          size="small"
          label="Sort"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          sx={{ minWidth: 200 }}
        >
          {SORTS.map((s) => (
            <MenuItem key={s.value} value={s.value}>
              {s.label}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      {facets.length > 0 && (
        <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap', gap: 1 }}>
          <Chip
            label="All"
            size="small"
            color={tag === null ? 'primary' : 'default'}
            variant={tag === null ? 'filled' : 'outlined'}
            onClick={() => setTag(null)}
          />
          {facets.map((f) => (
            <Chip
              key={f.tag}
              size="small"
              label={`${f.tag} · ${f.count}`}
              color={tag === f.tag ? 'primary' : 'default'}
              variant={tag === f.tag ? 'filled' : 'outlined'}
              onClick={() => setTag(tag === f.tag ? null : f.tag)}
            />
          ))}
        </Stack>
      )}

      <Divider sx={{ mb: 2 }} />

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress size={28} />
        </Box>
      ) : isError ? (
        <Typography color="error" sx={{ py: 4 }}>
          {error?.response?.data?.detail || error?.message || 'Failed to load contacts.'}
        </Typography>
      ) : items.length === 0 ? (
        <Box sx={{ textAlign: 'center', py: 6, color: 'text.secondary' }}>
          <Typography>
            {query || tag ? 'No contacts match your filters.' : 'No contacts yet.'}
          </Typography>
        </Box>
      ) : (
        <Stack spacing={1} sx={{ opacity: isFetching ? 0.7 : 1, transition: 'opacity 120ms' }}>
          {items.map((c) => (
            <ContactRow key={c.id} contact={c} onOpen={openContact} />
          ))}
        </Stack>
      )}

      {total > 0 && (
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mt: 2 }}
        >
          <Typography variant="body2" color="text.secondary">
            {rangeLabel}
          </Typography>
          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              disabled={offset === 0 || isFetching}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              size="small"
              disabled={offset + PAGE_SIZE >= total || isFetching}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              Next
            </Button>
          </Stack>
        </Stack>
      )}

      <ContactEditDialog
        open={createOpen}
        mode="create"
        onClose={() => setCreateOpen(false)}
        onSaved={handleCreated}
      />
    </Box>
  )
}
