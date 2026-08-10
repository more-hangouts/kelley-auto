// Floating in-call bar. Rendered once at app level so it survives navigating
// between pages mid-call — the rep can open the contact's deal, look at the
// vehicle, or check the board while still talking.

import { useEffect, useState } from 'react'

import CallEndIcon from '@mui/icons-material/CallEnd'
import DialpadIcon from '@mui/icons-material/Dialpad'
import MicOffOutlinedIcon from '@mui/icons-material/MicOffOutlined'
import MicNoneOutlinedIcon from '@mui/icons-material/MicNoneOutlined'
import PauseCircleOutlineIcon from '@mui/icons-material/PauseCircleOutline'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'

import {
  CALL_ACTIVE,
  CALL_CONNECTING,
  CALL_RINGING,
  useSoftphone,
} from './SoftphoneProvider'

const DIGITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '0', '#']

function formatElapsed(ms) {
  const total = Math.floor(ms / 1000)
  const m = String(Math.floor(total / 60)).padStart(2, '0')
  const s = String(total % 60).padStart(2, '0')
  return `${m}:${s}`
}

function CallTimer({ startedAt }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!startedAt) return undefined
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [startedAt])
  if (!startedAt) return null
  return (
    <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums' }}>
      {formatElapsed(Math.max(0, now - startedAt))}
    </Typography>
  )
}

export default function SoftphoneBar() {
  const phone = useSoftphone()
  const [padOpen, setPadOpen] = useState(false)

  // Collapse the keypad when the call ends so the next call starts clean.
  useEffect(() => {
    if (!phone?.inCall) setPadOpen(false)
  }, [phone?.inCall])

  if (!phone?.inCall) return null

  const {
    callState,
    peer,
    muted,
    startedAt,
    hangUp,
    toggleMute,
    sendDigit,
    onHold,
    holdPending,
    toggleHold,
  } = phone

  const statusLabel =
    callState === CALL_CONNECTING
      ? 'Connecting…'
      : callState === CALL_RINGING
        ? 'Ringing…'
        : onHold
          ? 'On hold'
          : 'Connected'

  return (
    <Paper
      elevation={8}
      role="region"
      aria-label="Active call"
      sx={{
        position: 'fixed',
        // Clear of the mobile bottom nav while staying reachable one-handed.
        bottom: { xs: 72, sm: 24 },
        right: { xs: 12, sm: 24 },
        left: { xs: 12, sm: 'auto' },
        zIndex: (t) => t.zIndex.snackbar + 1,
        p: 1.5,
        borderRadius: 2,
        minWidth: { sm: 320 },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="subtitle2" noWrap>
            {peer?.label || 'Contact'}
          </Typography>
          <Stack direction="row" alignItems="center" spacing={1}>
            {callState === CALL_ACTIVE ? (
              <CallTimer startedAt={startedAt} />
            ) : (
              <CircularProgress size={12} />
            )}
            <Typography variant="caption" color="text.secondary" noWrap>
              {statusLabel}
            </Typography>
            {muted && <Chip size="small" color="warning" label="Muted" />}
            {onHold && <Chip size="small" color="warning" label="Hold" />}
          </Stack>
        </Box>

        {/* Hold only exists for calls routed through a conference — inbound
            browser calls. Outbound legs have no participant to hold. */}
        {phone.canHold && (
          <Tooltip title={onHold ? 'Take off hold' : 'Put on hold'} arrow>
            <span>
              <IconButton
                size="small"
                color={onHold ? 'warning' : 'default'}
                onClick={toggleHold}
                disabled={callState !== CALL_ACTIVE || holdPending}
                aria-label={onHold ? 'Take caller off hold' : 'Put caller on hold'}
                aria-pressed={onHold}
              >
                {holdPending ? (
                  <CircularProgress size={16} />
                ) : (
                  <PauseCircleOutlineIcon fontSize="small" />
                )}
              </IconButton>
            </span>
          </Tooltip>
        )}

        <Tooltip title={muted ? 'Unmute' : 'Mute'} arrow>
          <span>
            <IconButton
              size="small"
              onClick={toggleMute}
              disabled={callState !== CALL_ACTIVE}
              aria-label={muted ? 'Unmute call' : 'Mute call'}
            >
              {muted ? <MicOffOutlinedIcon fontSize="small" /> : <MicNoneOutlinedIcon fontSize="small" />}
            </IconButton>
          </span>
        </Tooltip>

        <Tooltip title="Keypad" arrow>
          <span>
            <IconButton
              size="small"
              onClick={() => setPadOpen((v) => !v)}
              disabled={callState !== CALL_ACTIVE}
              aria-label="Show keypad"
              aria-pressed={padOpen}
            >
              <DialpadIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>

        <Button
          size="small"
          color="error"
          variant="contained"
          startIcon={<CallEndIcon fontSize="small" />}
          onClick={hangUp}
        >
          End
        </Button>
      </Stack>

      {padOpen && (
        <Box
          sx={{
            mt: 1.5,
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 0.5,
          }}
        >
          {DIGITS.map((d) => (
            <Button key={d} size="small" variant="outlined" onClick={() => sendDigit(d)}>
              {d}
            </Button>
          ))}
        </Box>
      )}
    </Paper>
  )
}
