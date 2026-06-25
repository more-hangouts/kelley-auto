import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import { salesGetStaffPicker } from '../services/api'

const PIN_LENGTH = 6

// Two-step PIN login (Phase 1 design lock-in: kiosk-style picker, not
// username field). Step 1 lists active sales staff with PINs minted;
// salesperson taps their tile. Step 2 prompts for the 6-digit PIN. The
// keypad submission posts `{identifier: picked.username, pin}` to
// `/api/sales/auth/pin` — `users.id` is never exposed by the picker.
//
// Fallback path: if the picker endpoint fails (network glitch, no
// staff configured yet, etc.) we degrade to a typed-username field
// so login is always possible.

export default function PinLogin() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { login } = useSalesAuth()
  const lockedReason = searchParams.get('locked')
  const lockedBanner = useMemo(() => {
    if (lockedReason === 'idle') {
      return 'This tablet locked itself after a few minutes of inactivity. Sign in to pick up where you left off.'
    }
    if (lockedReason) {
      return 'Tablet locked. Sign in to continue.'
    }
    return null
  }, [lockedReason])
  const [staff, setStaff] = useState(null)
  const [pickerError, setPickerError] = useState(null)
  const [pickedUser, setPickedUser] = useState(null)
  const [typedIdentifier, setTypedIdentifier] = useState('')
  const [pin, setPin] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const pinRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    salesGetStaffPicker()
      .then((rows) => {
        if (!cancelled) setStaff(rows || [])
      })
      .catch(() => {
        if (!cancelled) {
          setStaff([])
          setPickerError(
            "Couldn't load the salesperson list. Type your username instead.",
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  function handlePinChange(value) {
    const digits = value.replace(/\D/g, '').slice(0, PIN_LENGTH)
    setPin(digits)
  }

  function pickStylist(row) {
    setPickedUser(row)
    setPin('')
    setError(null)
    // Defer focus so the keypad input is mounted by the time we focus.
    setTimeout(() => pinRef.current?.focus(), 0)
  }

  function backToPicker() {
    setPickedUser(null)
    setPin('')
    setError(null)
  }

  async function handleSubmit(e) {
    e?.preventDefault?.()
    if (submitting) return
    if (pin.length !== PIN_LENGTH) {
      setError(`PIN must be ${PIN_LENGTH} digits.`)
      return
    }
    const identifier = pickedUser
      ? pickedUser.username
      : typedIdentifier.trim()
    if (!identifier) {
      setError('Pick your name or type your username.')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      const data = await login(identifier, pin)
      if (data.force_pin_change) {
        navigate('/change-pin', { replace: true })
      } else {
        navigate('/', { replace: true })
      }
    } catch (err) {
      const status = err?.response?.status
      if (status === 423) {
        const retryAfter = err?.response?.headers?.['retry-after']
        const minutes = retryAfter ? Math.ceil(Number(retryAfter) / 60) : null
        setError(
          minutes
            ? `Too many attempts. Try again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`
            : 'This account is temporarily locked. Try again later.',
        )
      } else {
        setError('That PIN did not match. Try again.')
      }
      setPin('')
      pinRef.current?.focus()
    } finally {
      setSubmitting(false)
    }
  }

  // ---- Render ------------------------------------------------------

  // Loading the staff list
  if (staff === null) {
    return (
      <Box
        sx={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          bgcolor: 'background.default',
        }}
      >
        <CircularProgress />
      </Box>
    )
  }

  const showFallbackTyping = pickerError !== null && !pickedUser
  const showPicker = !pickedUser && !showFallbackTyping && staff.length > 0
  const showEmptyState =
    !pickedUser && !showFallbackTyping && staff.length === 0

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        bgcolor: 'background.default',
        p: 2,
      }}
    >
      <Card sx={{ width: '100%', maxWidth: 480, boxShadow: 6 }}>
        <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
          {lockedBanner && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {lockedBanner}
            </Alert>
          )}
          <Box sx={{ textAlign: 'center', mb: 3 }}>
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ display: 'block', letterSpacing: 1.5 }}
            >
              Sales
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 600 }}>
              Kelley Autoplex
            </Typography>
            {showPicker && (
              <Typography variant="body2" color="text.secondary">
                Tap your name to sign in
              </Typography>
            )}
            {pickedUser && (
              <Typography variant="body2" color="text.secondary">
                Hi {pickedUser.full_name}, enter your PIN
              </Typography>
            )}
          </Box>

          {showPicker && (
            <Stack spacing={1.5}>
              {staff.map((row) => (
                <Button
                  key={row.username}
                  variant="outlined"
                  size="large"
                  onClick={() => pickStylist(row)}
                  sx={{
                    py: 2,
                    fontSize: '1.1rem',
                    justifyContent: 'flex-start',
                    px: 3,
                  }}
                  fullWidth
                >
                  {row.full_name}
                </Button>
              ))}
            </Stack>
          )}

          {showEmptyState && (
            <Alert severity="info">
              No salespeople are set up yet. Ask the owner to add one in
              admin settings.
            </Alert>
          )}

          {showFallbackTyping && (
            <Stack spacing={2} component="form" onSubmit={handleSubmit}>
              <Alert severity="warning">{pickerError}</Alert>
              <TextField
                label="Username"
                value={typedIdentifier}
                onChange={(e) => setTypedIdentifier(e.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                autoFocus
                required
                fullWidth
              />
              <TextField
                label="PIN"
                type="tel"
                inputMode="numeric"
                value={pin}
                onChange={(e) => handlePinChange(e.target.value)}
                inputRef={pinRef}
                inputProps={{
                  pattern: '\\d{6}',
                  maxLength: PIN_LENGTH,
                  inputMode: 'numeric',
                  autoComplete: 'one-time-code',
                  style: {
                    letterSpacing: '0.4em',
                    fontSize: '1.4rem',
                    textAlign: 'center',
                  },
                }}
                required
                fullWidth
              />
              {error && <Alert severity="error">{error}</Alert>}
              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={
                  submitting ||
                  pin.length !== PIN_LENGTH ||
                  !typedIdentifier.trim()
                }
                sx={{ py: 1.25 }}
              >
                {submitting ? (
                  <CircularProgress size={22} sx={{ color: 'common.white' }} />
                ) : (
                  'Sign in'
                )}
              </Button>
            </Stack>
          )}

          {pickedUser && (
            <Stack spacing={2} component="form" onSubmit={handleSubmit}>
              <TextField
                label="PIN"
                type="tel"
                inputMode="numeric"
                value={pin}
                onChange={(e) => handlePinChange(e.target.value)}
                inputRef={pinRef}
                autoFocus
                inputProps={{
                  pattern: '\\d{6}',
                  maxLength: PIN_LENGTH,
                  inputMode: 'numeric',
                  autoComplete: 'one-time-code',
                  style: {
                    letterSpacing: '0.5em',
                    fontSize: '1.6rem',
                    textAlign: 'center',
                  },
                }}
                required
                fullWidth
              />
              {error && <Alert severity="error">{error}</Alert>}
              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={submitting || pin.length !== PIN_LENGTH}
                sx={{ py: 1.5 }}
                fullWidth
              >
                {submitting ? (
                  <CircularProgress size={22} sx={{ color: 'common.white' }} />
                ) : (
                  'Sign in'
                )}
              </Button>
              <Button
                onClick={backToPicker}
                startIcon={<ArrowBackIcon fontSize="small" />}
                size="small"
                sx={{ alignSelf: 'center' }}
              >
                Not {pickedUser.full_name}? Pick again
              </Button>
            </Stack>
          )}
        </CardContent>
      </Card>
    </Box>
  )
}
