import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
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

import { useSalesAuth } from '../contexts/SalesAuthContext'
import { salesChangePin } from '../services/api'

const PIN_LENGTH = 6

export default function ChangePin() {
  const navigate = useNavigate()
  const { forcePinChange, refreshMe } = useSalesAuth()
  const [currentPin, setCurrentPin] = useState('')
  const [newPin, setNewPin] = useState('')
  const [confirmPin, setConfirmPin] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  function handleField(setter) {
    return (e) => setter(e.target.value.replace(/\D/g, '').slice(0, PIN_LENGTH))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (submitting) return
    if (newPin.length !== PIN_LENGTH) {
      setError(`New PIN must be ${PIN_LENGTH} digits.`)
      return
    }
    if (newPin !== confirmPin) {
      setError("New PINs don't match.")
      return
    }
    if (newPin === currentPin) {
      setError('New PIN must be different from the current PIN.')
      return
    }
    setError(null)
    setSubmitting(true)
    try {
      await salesChangePin(currentPin, newPin)
      await refreshMe()
      navigate('/', { replace: true })
    } catch (err) {
      const status = err?.response?.status
      if (status === 423) {
        setError('This account is temporarily locked. Try again later.')
      } else if (status === 400) {
        setError(
          err?.response?.data?.detail === 'pin_must_be_6_digits'
            ? 'New PIN must be 6 digits.'
            : 'New PIN must be different from the current PIN.',
        )
      } else if (status === 401) {
        setError('Current PIN was incorrect.')
        setCurrentPin('')
      } else {
        setError('Could not update your PIN. Try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

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
      <Card sx={{ width: '100%', maxWidth: 420, boxShadow: 6 }}>
        <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
          <Stack spacing={3} component="form" onSubmit={handleSubmit}>
            <Box>
              <Typography variant="h5" sx={{ fontWeight: 600 }}>
                Choose your PIN
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {forcePinChange
                  ? 'Pick a 6-digit PIN you can remember. The owner-issued PIN can no longer be used after this.'
                  : 'Pick a new 6-digit PIN.'}
              </Typography>
            </Box>

            {error && <Alert severity="error">{error}</Alert>}

            <TextField
              label="Current PIN"
              type="tel"
              inputMode="numeric"
              value={currentPin}
              onChange={handleField(setCurrentPin)}
              inputProps={{
                pattern: '\\d{6}',
                maxLength: PIN_LENGTH,
                style: { letterSpacing: '0.4em', textAlign: 'center' },
              }}
              required
              fullWidth
            />
            <TextField
              label="New PIN"
              type="tel"
              inputMode="numeric"
              value={newPin}
              onChange={handleField(setNewPin)}
              inputProps={{
                pattern: '\\d{6}',
                maxLength: PIN_LENGTH,
                style: { letterSpacing: '0.4em', textAlign: 'center' },
              }}
              required
              fullWidth
            />
            <TextField
              label="Confirm new PIN"
              type="tel"
              inputMode="numeric"
              value={confirmPin}
              onChange={handleField(setConfirmPin)}
              inputProps={{
                pattern: '\\d{6}',
                maxLength: PIN_LENGTH,
                style: { letterSpacing: '0.4em', textAlign: 'center' },
              }}
              required
              fullWidth
            />

            <Button
              type="submit"
              variant="contained"
              size="large"
              disabled={
                submitting ||
                currentPin.length !== PIN_LENGTH ||
                newPin.length !== PIN_LENGTH ||
                confirmPin.length !== PIN_LENGTH
              }
              sx={{ py: 1.25 }}
            >
              {submitting ? (
                <CircularProgress size={22} sx={{ color: 'common.white' }} />
              ) : (
                'Update PIN'
              )}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  )
}
