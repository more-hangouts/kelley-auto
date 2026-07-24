import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'

import { logCallAttempt, updateCallAttempt } from '../services/api'

// Shared tap-to-call control (Phase 7). Replaces raw `tel:` links so a
// salesperson's call attempt is LOGGED before the native dialer opens, and an
// outcome can be captured when they return to the app.
//
// Flow:
//   1. Tap → POST /call-attempts (with a client idempotency key) FIRST.
//   2. Only on success → open tel:<number> (native dialer).
//   3. If logging fails → explain + offer "Call without logging" (plain tel:).
//   4. When the page becomes visible again after a logged call → outcome sheet.
//   5. Dismissing the sheet keeps the row as call_initiated (outcome_pending).
//
// This is NOT Twilio Voice: the device places the call; we only record intent
// and a salesperson-reported outcome. We NEVER infer "connected" from the user
// returning to the app.

const OUTCOMES = [
  { value: 'connected', label: 'Connected' },
  { value: 'left_voicemail', label: 'Left voicemail' },
  { value: 'no_answer', label: 'No answer' },
  { value: 'busy', label: 'Busy' },
  { value: 'wrong_number', label: 'Wrong number' },
  { value: 'cancelled', label: 'Cancelled before calling' },
]

function newIdempotencyKey() {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID()
    }
  } catch {
    /* fall through */
  }
  // Fallback for older mobile browsers without crypto.randomUUID.
  return `call-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export default function CallContact({
  contactId,
  phone,
  phoneE164,
  eventId = null,
  source,
  children,
  variant = 'link',
}) {
  const dialNumber = phoneE164 || phone
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [savingOutcome, setSavingOutcome] = useState(false)
  const [outcome, setOutcome] = useState(null)
  const [notes, setNotes] = useState('')
  const [outcomeError, setOutcomeError] = useState(null)

  // The attempt we're waiting to collect an outcome for once the page returns
  // to the foreground. Held in a ref so the visibilitychange handler always
  // reads the latest value without re-subscribing.
  const pendingAttemptRef = useRef(null)
  // Synchronous in-flight guard: React's `pending` state updates async, so two
  // taps in the same tick both see pending===false. This ref flips
  // synchronously, closing the double-tap window.
  const inFlightRef = useRef(false)
  // One idempotency key per logical call attempt. Reused across retries of the
  // SAME tap so the server dedups; regenerated only for a genuinely new call.
  const idempotencyKeyRef = useRef(null)
  // Set true when the app actually backgrounds (the dialer took over) after a
  // call was armed. Only then do we treat a later "visible" as a real return
  // from the dialer — this prevents the sheet from firing on unrelated tab
  // switches and on desktop, where tel: never backgrounds the app.
  const wentHiddenRef = useRef(false)

  const openDialer = useCallback(() => {
    if (!dialNumber) return
    // Open the native dialer by clicking a real tel: anchor. On desktop /
    // unsupported devices this is a graceful no-op (nothing handles tel:).
    // Using an anchor (rather than window.location) keeps the "logged before
    // dialing" ordering observable in tests, which cannot patch window.location.
    const a = document.createElement('a')
    a.href = `tel:${dialNumber}`
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
  }, [dialNumber])

  const armOutcomeSheet = useCallback((attempt) => {
    if (!attempt || !attempt.id) return
    pendingAttemptRef.current = attempt
    wentHiddenRef.current = false // must observe a real background before prompting
  }, [])

  // Prompt for the outcome only when the app returns to the foreground AFTER
  // having actually backgrounded to the dialer. We never assume "connected" —
  // the sheet starts blank and the row stays call_initiated until the rep
  // explicitly picks an outcome.
  useEffect(() => {
    function onVisibilityChange() {
      if (document.visibilityState === 'hidden') {
        // The app backgrounded — if a call is armed, the dialer likely took
        // over. Record that so the next "visible" is a real return.
        if (pendingAttemptRef.current) wentHiddenRef.current = true
        return
      }
      // visible:
      if (pendingAttemptRef.current && wentHiddenRef.current) {
        wentHiddenRef.current = false
        setOutcome(null)
        setNotes('')
        setOutcomeError(null)
        setSheetOpen(true)
      }
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () =>
      document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [])

  async function handleCall() {
    // Synchronous guard — closes the double-tap window that the async `pending`
    // state cannot.
    if (inFlightRef.current || !dialNumber) return
    inFlightRef.current = true
    setError(null)
    setPending(true)
    // One key per tap; reused if this same call retries.
    if (!idempotencyKeyRef.current) idempotencyKeyRef.current = newIdempotencyKey()
    try {
      const attempt = await logCallAttempt(contactId, {
        phone: dialNumber,
        event_id: eventId,
        source,
        idempotency_key: idempotencyKeyRef.current,
      })
      // Log succeeded → arm the outcome sheet, THEN open the dialer. Clear the
      // key so the next distinct call gets a fresh one.
      idempotencyKeyRef.current = null
      armOutcomeSheet(attempt)
      openDialer()
    } catch (err) {
      // Logging failed — surface it and let the rep call anyway (unlogged).
      // Keep the idempotency key so a retry of THIS call reuses it (no dup).
      setError(err?.response?.data?.detail || 'Could not log the call.')
    } finally {
      inFlightRef.current = false
      setPending(false)
    }
  }

  function callWithoutLogging() {
    setError(null)
    openDialer()
  }

  async function saveOutcome() {
    const attempt = pendingAttemptRef.current
    if (!attempt) {
      setSheetOpen(false)
      return
    }
    if (!outcome && !notes.trim()) {
      // Nothing to record — treat as dismiss (keeps call_initiated/pending).
      dismissSheet()
      return
    }
    setSavingOutcome(true)
    setOutcomeError(null)
    try {
      await updateCallAttempt(contactId, attempt.id, {
        ...(outcome ? { outcome } : {}),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      })
      pendingAttemptRef.current = null
      setSheetOpen(false)
    } catch (err) {
      setOutcomeError(
        err?.response?.data?.detail || 'Could not save the outcome.',
      )
    } finally {
      setSavingOutcome(false)
    }
  }

  // Dismiss: keep the record as call_initiated with outcome_pending=true (the
  // server default), so managers still see the attempt. We just stop prompting
  // for this one until the contact is opened again.
  function dismissSheet() {
    pendingAttemptRef.current = null
    setSheetOpen(false)
  }

  if (!dialNumber) return children || <span>—</span>

  const trigger =
    variant === 'icon' ? (
      <Tooltip title="Call contact" arrow>
        <span>
          <IconButton
            size="small"
            color="primary"
            onClick={handleCall}
            disabled={pending}
            aria-label="Call contact"
          >
            {pending ? <CircularProgress size={16} /> : <PhoneOutlinedIcon fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>
    ) : variant === 'button' ? (
      <Button
        size="small"
        variant="outlined"
        startIcon={
          pending ? <CircularProgress size={14} /> : <PhoneOutlinedIcon fontSize="small" />
        }
        onClick={handleCall}
        disabled={pending}
      >
        {children || phone || dialNumber}
      </Button>
    ) : (
      <Link
        component="button"
        type="button"
        underline="hover"
        onClick={handleCall}
        aria-disabled={pending}
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 0.5,
          ...(pending && { pointerEvents: 'none', opacity: 0.6 }),
        }}
      >
        {pending && <CircularProgress size={12} />}
        {children || phone || dialNumber}
      </Link>
    )

  return (
    <>
      {trigger}

      {error && (
        <Box sx={{ mt: 0.5 }}>
          <Alert
            severity="warning"
            sx={{ py: 0, alignItems: 'center' }}
            action={
              <Button color="inherit" size="small" onClick={callWithoutLogging}>
                Call without logging
              </Button>
            }
          >
            {typeof error === 'string' ? error : 'Could not log the call.'}
          </Alert>
        </Box>
      )}

      <Dialog open={sheetOpen} onClose={dismissSheet} fullWidth maxWidth="xs">
        <DialogTitle>How did the call go?</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              Recorded as “Call initiated.” Pick an outcome when you know it —
              nothing is assumed.
            </Typography>
            <ToggleButtonGroup
              orientation="vertical"
              exclusive
              value={outcome}
              onChange={(_e, val) => val && setOutcome(val)}
              fullWidth
            >
              {OUTCOMES.map((o) => (
                <ToggleButton key={o.value} value={o.value} sx={{ justifyContent: 'flex-start' }}>
                  {o.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            <TextField
              label="Notes (optional)"
              multiline
              minRows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              fullWidth
            />
            {outcomeError && (
              <Alert severity="error" sx={{ py: 0 }}>
                {typeof outcomeError === 'string' ? outcomeError : 'Could not save.'}
              </Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={dismissSheet} disabled={savingOutcome}>
            Not now
          </Button>
          <Button
            variant="contained"
            onClick={saveOutcome}
            disabled={savingOutcome || (!outcome && !notes.trim())}
            startIcon={savingOutcome ? <CircularProgress size={14} /> : null}
          >
            Save outcome
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}
