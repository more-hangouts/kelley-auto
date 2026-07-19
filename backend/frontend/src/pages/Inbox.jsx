import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import SearchIcon from '@mui/icons-material/Search'
import SendIcon from '@mui/icons-material/Send'
import SmsOutlinedIcon from '@mui/icons-material/SmsOutlined'
import LanguageIcon from '@mui/icons-material/Language'
import { Link as RouterLink } from 'react-router-dom'

import {
  getInboxConversation,
  listInboxConversations,
  patchInboxConversation,
  sendInboxMessage,
} from '../services/api'

// Omnichannel CRM Inbox. Inbound SMS/Meta land here with the composer
// disabled until carrier approval; web-chat threads reply LIVE (no transport
// needed — the visitor's widget polls the row). The channel column is the
// only branch point: one list, one thread view, per-channel reply rules.

const POLL_MS = 20000
// Open threads refresh faster so a web-chat visitor's reply appears quickly.
const THREAD_POLL_MS = 5000

const CHANNEL_META = {
  sms: { label: 'SMS', icon: SmsOutlinedIcon, color: '#2563eb' },
  facebook: { label: 'Facebook', icon: SmsOutlinedIcon, color: '#1877f2' },
  instagram: { label: 'Instagram', icon: SmsOutlinedIcon, color: '#c13584' },
  web_chat: { label: 'Web', icon: LanguageIcon, color: '#157A33' },
}

function timeAgo(iso) {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (s < 60) return 'now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function ChannelChip({ channel }) {
  const meta = CHANNEL_META[channel] || { label: channel, color: '#64748b' }
  return (
    <Chip
      size="small"
      label={meta.label}
      sx={{ height: 18, fontSize: 10, fontWeight: 600, bgcolor: `${meta.color}18`, color: meta.color }}
    />
  )
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'unread', label: 'Unread' },
  { key: 'mine', label: 'Mine' },
  { key: 'unlinked', label: 'Unlinked' },
]

const CHANNELS = [
  { key: '', label: 'All' },
  { key: 'sms', label: 'SMS' },
  { key: 'web_chat', label: 'Web' },
  { key: 'facebook', label: 'FB' },
  { key: 'instagram', label: 'IG' },
]

const displayTitle = (c) =>
  c?.contact?.display_name || c?.display_name || c?.external_id || 'Unknown'

export default function Inbox() {
  const [conversations, setConversations] = useState(null)
  const [filter, setFilter] = useState('all')
  const [channel, setChannel] = useState('')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState(null)
  const searchRef = useRef(search)
  searchRef.current = search

  const loadList = useCallback(async () => {
    const params = {}
    if (filter === 'unread') params.unread = true
    if (filter === 'mine') params.mine = true
    if (filter === 'unlinked') params.unlinked = true
    if (channel) params.channel = channel
    if (searchRef.current.trim()) params.q = searchRef.current.trim()
    try {
      const data = await listInboxConversations(params)
      setConversations(data.conversations || [])
      setError(null)
    } catch {
      setError("Couldn't load the inbox.")
      setConversations([])
    }
  }, [filter, channel])

  useEffect(() => {
    loadList()
    const t = setInterval(loadList, POLL_MS)
    return () => clearInterval(t)
  }, [loadList])

  // Silent refresh of the open thread — a web-chat visitor's reply should
  // appear without the admin touching anything.
  useEffect(() => {
    if (!selectedId) return undefined
    const t = setInterval(async () => {
      try {
        const data = await getInboxConversation(selectedId)
        setDetail((d) => (d && d.id === selectedId ? data : d))
      } catch {
        /* transient; next tick retries */
      }
    }, THREAD_POLL_MS)
    return () => clearInterval(t)
  }, [selectedId])

  const openConversation = useCallback(async (id) => {
    setSelectedId(id)
    setDetailLoading(true)
    try {
      const data = await getInboxConversation(id)
      setDetail(data)
      // Reading clears unread locally without a full refetch.
      setConversations((prev) =>
        prev ? prev.map((c) => (c.id === id ? { ...c, unread: false } : c)) : prev,
      )
    } catch {
      setError("Couldn't open that conversation.")
    } finally {
      setDetailLoading(false)
    }
  }, [])

  async function changeStatus(nextStatus) {
    if (!detail) return
    try {
      const updated = await patchInboxConversation(detail.id, { status: nextStatus })
      setDetail((d) => ({ ...d, status: updated.status }))
      setConversations((prev) =>
        prev ? prev.map((c) => (c.id === detail.id ? { ...c, status: updated.status } : c)) : prev,
      )
    } catch {
      setError("Couldn't update the conversation.")
    }
  }

  return (
    <Box sx={{ display: 'flex', gap: 2, height: 'calc(100vh - 130px)' }}>
      {/* LEFT — conversation list */}
      <Paper
        variant="outlined"
        sx={{
          width: { xs: '100%', md: 340 },
          flexShrink: 0,
          display: { xs: selectedId ? 'none' : 'flex', md: 'flex' },
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <Box sx={{ p: 2, pb: 1 }}>
          <Typography variant="h6" fontWeight={700} gutterBottom>
            Inbox
          </Typography>
          <TextField
            size="small"
            fullWidth
            placeholder="Search name or number"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && loadList()}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
          <ToggleButtonGroup
            size="small"
            exclusive
            value={filter}
            onChange={(_e, v) => v && setFilter(v)}
            sx={{ mt: 1.5, flexWrap: 'wrap' }}
          >
            {FILTERS.map((f) => (
              <ToggleButton key={f.key} value={f.key} sx={{ textTransform: 'none', px: 1.5, py: 0.25 }}>
                {f.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={channel}
            onChange={(_e, v) => setChannel(v ?? '')}
            sx={{ mt: 1, flexWrap: 'wrap' }}
          >
            {CHANNELS.map((c) => (
              <ToggleButton key={c.key || 'all'} value={c.key} sx={{ textTransform: 'none', px: 1.5, py: 0.25 }}>
                {c.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>
        <Divider />
        <Box sx={{ overflowY: 'auto', flex: 1 }}>
          {conversations === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
              <CircularProgress size={22} />
            </Box>
          ) : conversations.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
              No conversations{filter !== 'all' ? ' for this filter' : ' yet'}.
            </Typography>
          ) : (
            conversations.map((c) => (
              <Box
                key={c.id}
                onClick={() => openConversation(c.id)}
                sx={{
                  px: 2,
                  py: 1.25,
                  cursor: 'pointer',
                  borderLeft: '3px solid',
                  borderColor: c.id === selectedId ? 'primary.main' : 'transparent',
                  bgcolor: c.id === selectedId ? 'rgba(93,58,107,0.06)' : 'transparent',
                  '&:hover': { bgcolor: 'rgba(93,58,107,0.04)' },
                }}
              >
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography
                    variant="body2"
                    noWrap
                    sx={{ fontWeight: c.unread ? 700 : 500, flex: 1, minWidth: 0 }}
                  >
                    {displayTitle(c)}
                  </Typography>
                  <ChannelChip channel={c.channel} />
                  <Typography variant="caption" color="text.secondary">
                    {timeAgo(c.last_message_at)}
                  </Typography>
                </Stack>
                <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 0.25 }}>
                  {c.unread && (
                    <Box sx={{ width: 7, height: 7, borderRadius: '50%', bgcolor: 'primary.main', flexShrink: 0 }} />
                  )}
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    noWrap
                    sx={{ flex: 1, minWidth: 0, fontWeight: c.unread ? 600 : 400 }}
                  >
                    {c.last_inbound_preview || '—'}
                  </Typography>
                  {!c.is_linked && <Chip size="small" label="Unlinked" color="warning" variant="outlined" sx={{ height: 18, fontSize: 10 }} />}
                  {c.opted_out && <Chip size="small" label="Opted out" color="error" variant="outlined" sx={{ height: 18, fontSize: 10 }} />}
                  {c.status === 'resolved' && <Chip size="small" label="Resolved" sx={{ height: 18, fontSize: 10 }} />}
                </Stack>
              </Box>
            ))
          )}
        </Box>
      </Paper>

      {/* RIGHT — thread + context */}
      <Paper
        variant="outlined"
        sx={{
          flex: 1,
          display: { xs: selectedId ? 'flex' : 'none', md: 'flex' },
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
        {!detail ? (
          <Box sx={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center' }}>
            <Typography color="text.secondary">Select a conversation to read it.</Typography>
          </Box>
        ) : (
          <ThreadView
            detail={detail}
            loading={detailLoading}
            onBack={() => { setSelectedId(null); setDetail(null) }}
            onStatus={changeStatus}
            onSend={async (text) => {
              const result = await sendInboxMessage(detail.id, text)
              setDetail((d) =>
                d && d.id === detail.id
                  ? {
                      ...d,
                      ...(result.conversation || {}),
                      messages: [...(d.messages || []), result.message],
                    }
                  : d,
              )
            }}
          />
        )}
      </Paper>
    </Box>
  )
}

function ThreadView({ detail, loading, onBack, onStatus, onSend }) {
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState(null)
  const isWebChat = detail.channel === 'web_chat'
  const canReply = detail.reply_enabled ?? isWebChat
  const composerReason =
    detail.contact?.sms_opted_out || detail.opted_out
      ? 'This customer opted out — messaging is disabled for them.'
      : detail.channel === 'sms'
        ? 'Replies turn on once the carrier (A2P) campaign is approved. Inbound messages are logged here now.'
        : 'Replies to Facebook/Instagram turn on once Meta approves messaging. Inbound messages are logged here now.'

  async function submit() {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    setSendError(null)
    try {
      await onSend(text)
      setDraft('')
    } catch {
      // Keep the draft so nothing typed is lost.
      setSendError("Couldn't send — try again.")
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      {/* header */}
      <Box sx={{ px: 2, py: 1.5, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <IconButton size="small" onClick={onBack} sx={{ display: { md: 'none' } }}>
            <ArrowBackIcon fontSize="small" />
          </IconButton>
          <Avatar sx={{ width: 32, height: 32, fontSize: 14 }}>
            {displayTitle(detail).slice(0, 1).toUpperCase()}
          </Avatar>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle2" noWrap fontWeight={700}>
              {displayTitle(detail)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {detail.external_id}
            </Typography>
          </Box>
          <ChannelChip channel={detail.channel} />
          <ToggleButtonGroup
            size="small"
            exclusive
            value={detail.status}
            onChange={(_e, v) => v && onStatus(v)}
          >
            <ToggleButton value="open" sx={{ textTransform: 'none', py: 0.2 }}>Open</ToggleButton>
            <ToggleButton value="pending" sx={{ textTransform: 'none', py: 0.2 }}>Pending</ToggleButton>
            <ToggleButton value="resolved" sx={{ textTransform: 'none', py: 0.2 }}>Resolved</ToggleButton>
          </ToggleButtonGroup>
        </Stack>
        {/* context row */}
        <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap">
          {detail.contact ? (
            <Chip
              size="small"
              component={RouterLink}
              to={`/contacts/${detail.contact.id}`}
              clickable
              label={`Contact: ${detail.contact.phone || detail.contact.email || detail.contact.display_name}`}
            />
          ) : (
            <Chip size="small" color="warning" variant="outlined" label="Unlinked — no CRM contact" />
          )}
          {detail.event && (
            <Chip
              size="small"
              component={RouterLink}
              to={`/sales`}
              clickable
              label={`Deal: ${detail.event.event_type} (${detail.event.status})`}
            />
          )}
          {(detail.contact?.sms_opted_out || detail.opted_out) && (
            <Chip size="small" color="error" variant="outlined" label="Opted out of SMS" />
          )}
          {isWebChat && detail.visitor_active && (
            <Chip
              size="small"
              color="success"
              variant="outlined"
              icon={
                <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: 'success.main', ml: 0.75 }} />
              }
              label="On the site now"
            />
          )}
          {isWebChat && detail.visitor_page_url && (
            <Chip
              size="small"
              variant="outlined"
              label={`Viewing: ${detail.visitor_page_url.replace(/^https?:\/\/[^/]+/, '') || '/'}`}
              sx={{ maxWidth: 260 }}
            />
          )}
        </Stack>
      </Box>

      {/* messages */}
      <Box sx={{ flex: 1, overflowY: 'auto', p: 2, bgcolor: 'rgba(0,0,0,0.015)' }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress size={22} />
          </Box>
        ) : (
          (detail.messages || []).map((m) => <Bubble key={m.id} m={m} />)
        )}
      </Box>

      {/* composer — live for web chat, locked for gated channels */}
      <Box sx={{ p: 1.5, borderTop: '1px solid', borderColor: 'divider' }}>
        {canReply ? (
          <>
            {sendError && (
              <Alert severity="error" onClose={() => setSendError(null)} sx={{ mb: 1 }}>
                {sendError}
              </Alert>
            )}
            <TextField
              fullWidth
              size="small"
              multiline
              maxRows={4}
              placeholder="Reply to the visitor…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      color="primary"
                      onClick={submit}
                      disabled={sending || !draft.trim()}
                    >
                      {sending ? <CircularProgress size={16} /> : <SendIcon fontSize="small" />}
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              {detail.visitor_active
                ? 'Visitor is on the site — they’ll see this instantly.'
                : 'Visitor left the page — they’ll see this if they come back.'}
            </Typography>
          </>
        ) : (
          <>
            <Tooltip title={composerReason} arrow>
              <Box>
                <TextField
                  fullWidth
                  size="small"
                  disabled
                  placeholder="Reply…"
                  InputProps={{
                    endAdornment: (
                      <InputAdornment position="end">
                        <LockOutlinedIcon fontSize="small" color="disabled" />
                      </InputAdornment>
                    ),
                  }}
                />
              </Box>
            </Tooltip>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.5 }}>
              <LockOutlinedIcon sx={{ fontSize: 13 }} /> {composerReason}
            </Typography>
          </>
        )}
      </Box>
    </>
  )
}

function Bubble({ m }) {
  const outbound = m.direction === 'outbound'
  return (
    <Box sx={{ display: 'flex', justifyContent: outbound ? 'flex-end' : 'flex-start', mb: 1 }}>
      <Box
        sx={{
          maxWidth: '72%',
          px: 1.5,
          py: 1,
          borderRadius: 2,
          bgcolor: outbound ? 'primary.main' : 'background.paper',
          color: outbound ? 'primary.contrastText' : 'text.primary',
          border: outbound ? 'none' : '1px solid',
          borderColor: 'divider',
        }}
      >
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {m.body || (m.media?.length ? '📎 media' : '—')}
        </Typography>
        <Typography variant="caption" sx={{ opacity: 0.7, display: 'block', mt: 0.25, textAlign: 'right' }}>
          {timeAgo(m.created_at)}{m.is_echo ? ' · sent elsewhere' : ''}
        </Typography>
      </Box>
    </Box>
  )
}
