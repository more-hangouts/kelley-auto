// Inbound call routing settings.
//
// The point of this screen: where the shop's phone rings should be something an
// owner can change in ten seconds, not an env var edit and a restart. The
// fallback can be any number — a manager's cell, an answering service — not
// just the office line published on the website.

import { useEffect, useState } from 'react'

import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import PhoneForwardedOutlinedIcon from '@mui/icons-material/PhoneForwardedOutlined'

import { getVoiceSettings, updateVoiceSettings } from '../services/api'

const MODES = [
  {
    value: 'browser_then_fallback',
    label: 'Ring the dashboard, then the fallback number',
    help: 'Everyone signed in with the softphone on rings first. If nobody answers, the call goes to the fallback number below.',
  },
  {
    value: 'browser_only',
    label: 'Ring the dashboard only',
    help: 'No phone fallback. Callers who reach nobody hear the unavailable message.',
  },
  {
    value: 'fallback_only',
    label: 'Always ring the fallback number',
    help: 'Skip the dashboard entirely. Use this to send every call straight to a phone.',
  },
]

export default function PhoneSettings() {
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let cancelled = false
    getVoiceSettings()
      .then((data) => {
        if (!cancelled) setCfg(data)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load phone settings.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function patch(fields) {
    setCfg((prev) => ({ ...prev, ...fields }))
    setSaved(false)
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const next = await updateVoiceSettings({
        inbound_mode: cfg.inbound_mode,
        fallback_number: cfg.fallback_number?.trim() ? cfg.fallback_number.trim() : null,
        ring_timeout_seconds: Number(cfg.ring_timeout_seconds),
        fallback_timeout_seconds: Number(cfg.fallback_timeout_seconds),
      })
      setCfg((prev) => ({ ...prev, ...next }))
      setSaved(true)
    } catch (err) {
      const detail = err?.response?.data?.detail
      setError(
        detail === 'invalid_fallback_number'
          ? "That doesn't look like a valid phone number."
          : detail === 'fallback_number_required'
            ? 'Add a fallback number before choosing "always ring the fallback".'
            : 'Could not save. Please try again.',
      )
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}>
        <CircularProgress />
      </Box>
    )
  }
  if (!cfg) {
    return <Alert severity="error">{error || 'Phone settings unavailable.'}</Alert>
  }

  const needsFallback = cfg.inbound_mode !== 'browser_only'

  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 0.5 }}>
          <PhoneForwardedOutlinedIcon color="primary" />
          <Typography variant="h4">Phone routing</Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          What happens when someone calls the business number.
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        {saved && (
          <Alert severity="success" sx={{ mb: 2 }}>
            Saved. New calls use these settings immediately.
          </Alert>
        )}

        <Alert severity="info" icon={false} sx={{ mb: 3 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="body2">Signed in and taking calls right now:</Typography>
            <Chip
              size="small"
              color={cfg.online_reps > 0 ? 'success' : 'default'}
              label={cfg.online_reps === 1 ? '1 person' : `${cfg.online_reps ?? 0} people`}
            />
          </Stack>
          {cfg.online_reps === 0 && cfg.inbound_mode === 'browser_then_fallback' && (
            <Typography variant="caption" color="text.secondary">
              With nobody signed in, calls go straight to the fallback number.
            </Typography>
          )}
        </Alert>

        <FormControl sx={{ mb: 3 }}>
          <FormLabel sx={{ mb: 1 }}>Where should calls go?</FormLabel>
          <RadioGroup
            value={cfg.inbound_mode}
            onChange={(e) => patch({ inbound_mode: e.target.value })}
          >
            {MODES.map((m) => (
              <FormControlLabel
                key={m.value}
                value={m.value}
                control={<Radio />}
                sx={{ alignItems: 'flex-start', mb: 1.5 }}
                label={
                  <Box sx={{ pt: 0.75 }}>
                    <Typography variant="body1">{m.label}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {m.help}
                    </Typography>
                  </Box>
                }
              />
            ))}
          </RadioGroup>
        </FormControl>

        <Divider sx={{ mb: 3 }} />

        <Stack spacing={2.5} sx={{ maxWidth: 460 }}>
          <TextField
            label="Fallback number"
            value={cfg.fallback_number || ''}
            onChange={(e) => patch({ fallback_number: e.target.value })}
            disabled={!needsFallback}
            placeholder="(210) 251-3644"
            helperText={
              needsFallback
                ? 'Any number you like — the office line, a manager’s cell, an answering service. Leave blank to send unanswered callers to the unavailable message instead.'
                : 'Not used while calls ring the dashboard only.'
            }
          />

          <TextField
            label="Ring the dashboard for"
            type="number"
            value={cfg.ring_timeout_seconds}
            onChange={(e) => patch({ ring_timeout_seconds: e.target.value })}
            inputProps={{ min: 5, max: 120 }}
            helperText="Seconds before giving up on the dashboard and trying the fallback."
          />

          <TextField
            label="Ring the fallback for"
            type="number"
            value={cfg.fallback_timeout_seconds}
            onChange={(e) => patch({ fallback_timeout_seconds: e.target.value })}
            inputProps={{ min: 5, max: 120 }}
            disabled={!needsFallback}
            helperText="Seconds before giving up entirely."
          />
        </Stack>

        <Box sx={{ mt: 4 }}>
          <Button variant="contained" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        </Box>
      </CardContent>
    </Card>
  )
}
