import { useNavigate } from 'react-router-dom'
import { Box, Button, Paper, Snackbar, Typography } from '@mui/material'

import { channelMeta, conversationSender } from '../../utils/channels'

// The arrival toast. Bottom-LEFT on purpose: the softphone bar and the
// incoming-call card both occupy bottom-right, and a ringing call must never
// be covered by a text message.
export default function NewMessageToast({ toast, onClose }) {
  const navigate = useNavigate()

  const meta = channelMeta(toast?.channel)
  const Icon = meta.icon

  const open = () => {
    onClose()
    navigate('/inbox')
  }

  return (
    <Snackbar
      open={Boolean(toast)}
      autoHideDuration={6000}
      onClose={(_e, reason) => {
        // Clicking elsewhere should not dismiss it — the whole point is that
        // you were mid-task and looking somewhere else.
        if (reason === 'clickaway') return
        onClose()
      }}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      sx={{ zIndex: (t) => t.zIndex.snackbar }}
    >
      <Paper
        elevation={8}
        sx={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 1.5,
          p: 1.5,
          pr: 1,
          width: 340,
          maxWidth: '90vw',
          border: '1px solid',
          borderColor: 'divider',
          borderLeft: '3px solid',
          borderLeftColor: meta.color,
        }}
      >
        <Box
          sx={{
            width: 26,
            height: 26,
            borderRadius: 1.5,
            display: 'grid',
            placeItems: 'center',
            flex: 'none',
            bgcolor: `${meta.color}2A`,
            color: meta.color,
          }}
        >
          <Icon sx={{ fontSize: 15 }} />
        </Box>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ fontSize: 13.5, fontWeight: 650, lineHeight: 1.3 }}>
            {conversationSender(toast?.display_name, toast?.channel)}
          </Typography>
          <Typography
            sx={{
              fontSize: 12.5,
              color: 'text.secondary',
              lineHeight: 1.4,
              mt: 0.25,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {toast?.preview || `New ${meta.label.toLowerCase()} message`}
          </Typography>
        </Box>

        <Button size="small" onClick={open} sx={{ flex: 'none', fontSize: 12 }}>
          Open
        </Button>
      </Paper>
    </Snackbar>
  )
}
