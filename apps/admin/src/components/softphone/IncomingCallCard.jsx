// Incoming-call card. Rendered at app level beside the in-call bar so a call
// can arrive on any screen.
//
// Deliberately never auto-answers: a call is only connected by an explicit
// Answer, so a rep is never dropped into a live conversation by surprise.

import CallEndIcon from '@mui/icons-material/CallEnd'
import CallIcon from '@mui/icons-material/Call'
import PersonOutlineIcon from '@mui/icons-material/PersonOutline'
import {
  Avatar,
  Box,
  Button,
  Paper,
  Stack,
  Typography,
  keyframes,
} from '@mui/material'

import { useSoftphone } from './SoftphoneProvider'

// A quiet pulse — enough to catch the eye on a busy board without the whole
// dashboard flashing.
const pulse = keyframes`
  0%   { box-shadow: 0 0 0 0 rgba(21, 122, 51, 0.45); }
  70%  { box-shadow: 0 0 0 14px rgba(21, 122, 51, 0); }
  100% { box-shadow: 0 0 0 0 rgba(21, 122, 51, 0); }
`

function formatPhone(e164) {
  if (!e164) return 'Unknown caller'
  const digits = String(e164).replace(/\D/g, '')
  const ten = digits.length === 11 && digits.startsWith('1') ? digits.slice(1) : digits
  if (ten.length !== 10) return e164
  return `(${ten.slice(0, 3)}) ${ten.slice(3, 6)}-${ten.slice(6)}`
}

export default function IncomingCallCard() {
  const phone = useSoftphone()
  const incoming = phone?.incoming
  if (!incoming) return null

  const { contactName, from, city, state } = incoming
  const place = [city, state].filter(Boolean).join(', ')

  return (
    <Paper
      elevation={12}
      role="alertdialog"
      aria-label="Incoming call"
      sx={{
        position: 'fixed',
        bottom: { xs: 72, sm: 24 },
        right: { xs: 12, sm: 24 },
        left: { xs: 12, sm: 'auto' },
        zIndex: (t) => t.zIndex.snackbar + 2,
        p: 2,
        borderRadius: 2,
        minWidth: { sm: 340 },
        animation: `${pulse} 1.8s ease-out infinite`,
      }}
    >
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 1.5 }}>
        <Avatar sx={{ bgcolor: 'primary.main' }}>
          <PersonOutlineIcon />
        </Avatar>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Incoming call
          </Typography>
          <Typography variant="subtitle1" noWrap sx={{ fontWeight: 600 }}>
            {contactName || formatPhone(from)}
          </Typography>
          {/* Show the number under a matched name, and the city otherwise —
              never repeat the same string twice. */}
          <Typography variant="body2" color="text.secondary" noWrap>
            {contactName ? formatPhone(from) : place || 'Unknown caller'}
          </Typography>
        </Box>
      </Stack>

      <Stack direction="row" spacing={1}>
        <Button
          fullWidth
          variant="contained"
          color="success"
          startIcon={<CallIcon />}
          onClick={phone.answerIncoming}
        >
          Answer
        </Button>
        <Button
          fullWidth
          variant="outlined"
          color="error"
          startIcon={<CallEndIcon />}
          onClick={phone.declineIncoming}
        >
          Decline
        </Button>
      </Stack>
    </Paper>
  )
}
