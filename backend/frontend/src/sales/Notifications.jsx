import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  Snackbar,
  Stack,
  Switch,
  Typography,
} from '@mui/material'

import {
  salesListNotificationPreferences,
  salesUpdateNotificationPreferences,
} from '../services/api'

// /notifications — B2.5. Lists the event kinds the current sales user
// can toggle, grouped by category. Saves on each toggle (no draft
// state) since the surface is tiny and immediate feedback is more
// important than transactional bulk-edit.
//
// Intrinsic-only kinds (your shift was edited, your PIN was reset…)
// are deliberately NOT shown — the API never returns them, so we don't
// need to render an "unconfigurable" section. If that changes, the
// service layer (services/notification_preferences_service.py) is the
// single place to flip the policy.

export default function Notifications() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(null)
  const [error, setError] = useState(null)
  const [snack, setSnack] = useState(null)
  const [preferences, setPreferences] = useState([])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    salesListNotificationPreferences()
      .then((data) => {
        if (cancelled) return
        setPreferences(data?.preferences || [])
      })
      .catch((err) => {
        if (cancelled) return
        setError(
          err?.response?.data?.detail?.message ||
            err?.message ||
            'Could not load notification preferences.',
        )
      })
      .finally(() => {
        if (cancelled) return
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const grouped = useMemo(() => {
    const byCategory = new Map()
    for (const pref of preferences) {
      if (!byCategory.has(pref.category)) {
        byCategory.set(pref.category, [])
      }
      byCategory.get(pref.category).push(pref)
    }
    return Array.from(byCategory.entries())
  }, [preferences])

  async function handleToggle(kind, nextEnabled) {
    setSaving(kind)
    setError(null)
    try {
      const data = await salesUpdateNotificationPreferences([
        { event_kind: kind, enabled: nextEnabled },
      ])
      setPreferences(data?.preferences || [])
      setSnack({
        message: nextEnabled
          ? 'Turned on — you will start receiving these.'
          : 'Turned off — you will stop receiving these.',
        severity: 'success',
      })
    } catch (err) {
      setError(
        err?.response?.data?.detail?.message ||
          err?.message ||
          'Could not save that change. Try again.',
      )
    } finally {
      setSaving(null)
    }
  }

  if (loading) {
    return (
      <Stack alignItems="center" sx={{ py: 6 }}>
        <CircularProgress />
      </Stack>
    )
  }

  return (
    <Box>
      <Stack spacing={0.5} sx={{ mb: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>
          Notifications
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Choose which Bella&apos;s XV emails you receive. Safety
          notifications (your shift was edited, your PIN was reset, etc.)
          always go through and aren&apos;t listed here.
        </Typography>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {grouped.length === 0 && (
        <Card variant="outlined">
          <CardContent>
            <Typography color="text.secondary">
              There are no notification toggles available for your account.
            </Typography>
          </CardContent>
        </Card>
      )}

      <Stack spacing={2}>
        {grouped.map(([category, prefs]) => (
          <Card key={category} variant="outlined">
            <CardContent>
              <Typography
                variant="overline"
                color="text.secondary"
                sx={{ letterSpacing: 1 }}
              >
                {category}
              </Typography>
              <Stack divider={<Divider flexItem />} sx={{ mt: 1 }}>
                {prefs.map((pref) => (
                  <Stack
                    key={pref.event_kind}
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    sx={{ py: 1.5 }}
                  >
                    <Box sx={{ pr: 2, flex: 1 }}>
                      <Stack
                        direction="row"
                        spacing={1}
                        alignItems="center"
                        sx={{ mb: 0.25 }}
                      >
                        <Typography sx={{ fontWeight: 500 }}>
                          {pref.label}
                        </Typography>
                        {pref.source === 'override' && (
                          <Chip
                            size="small"
                            variant="outlined"
                            label="Customized"
                            sx={{ height: 20 }}
                          />
                        )}
                      </Stack>
                      <Typography variant="body2" color="text.secondary">
                        {pref.description}
                      </Typography>
                    </Box>
                    <FormControlLabel
                      control={
                        <Switch
                          checked={pref.enabled}
                          disabled={saving === pref.event_kind}
                          onChange={(e) =>
                            handleToggle(pref.event_kind, e.target.checked)
                          }
                        />
                      }
                      label=""
                      sx={{ m: 0 }}
                    />
                  </Stack>
                ))}
              </Stack>
            </CardContent>
          </Card>
        ))}
      </Stack>

      <Snackbar
        open={Boolean(snack)}
        autoHideDuration={2500}
        onClose={() => setSnack(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {snack && (
          <Alert
            severity={snack.severity}
            onClose={() => setSnack(null)}
            sx={{ width: '100%' }}
          >
            {snack.message}
          </Alert>
        )}
      </Snackbar>
    </Box>
  )
}
