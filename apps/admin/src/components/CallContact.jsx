import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Snackbar,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown'
import BusinessOutlinedIcon from '@mui/icons-material/BusinessOutlined'
import SmartphoneOutlinedIcon from '@mui/icons-material/SmartphoneOutlined'

import { logCallAttempt, startBridgeCall, updateCallAttempt } from '../services/api'

// Per-device storage of the rep's callback number for the business-number
// bridge (Twilio rings THIS number first, then bridges to the contact). Kept
// in localStorage so a rep enters it once per device; the server also has a
// TWILIO_VOICE_REP_FALLBACK_NUMBER default when this is absent.
const REP_PHONE_KEY = 'kelley.callback_phone'

function readRepPhone() {
  try {
    return window.localStorage.getItem(REP_PHONE_KEY) || ''
  } catch {
    return ''
  }
}

function writeRepPhone(value) {
  try {
    if (value) window.localStorage.setItem(REP_PHONE_KEY, value)
  } catch {
    /* ignore storage failures (private mode) */
  }
}

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
// The native path is NOT Twilio Voice: the device places the call; we only
// record intent and a salesperson-reported outcome. We NEVER infer "connected"
// from the user returning to the app.
//
// OPTIONAL business-number path (Twilio Voice click-to-call bridge): the caret
// menu offers "Business number call". Instead of the device dialing the
// contact (which exposes the rep's personal cell), the server rings the rep
// first, then bridges to the contact so the contact sees the BUSINESS number.
// It reuses the same call-attempt logging. If Twilio voice isn't configured the
// server returns 503 and the UI cleanly points the rep back at the native path
// — the native tel: dialer is never removed or degraded.

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

  // Business-number bridge (Twilio Voice) UI state.
  const [menuAnchor, setMenuAnchor] = useState(null)
  const [bridgePending, setBridgePending] = useState(false)
  const [repPhoneDialogOpen, setRepPhoneDialogOpen] = useState(false)
  const [repPhoneInput, setRepPhoneInput] = useState('')
  const [toast, setToast] = useState(null) // { severity, message }
  const bridgeInFlightRef = useRef(false)

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

  // --- Business-number bridge (Twilio Voice) --------------------------------

  const placeBridgeCall = useCallback(
    async (repPhone) => {
      if (bridgeInFlightRef.current) return
      bridgeInFlightRef.current = true
      setBridgePending(true)
      setError(null)
      try {
        await startBridgeCall(contactId, {
          rep_phone: repPhone || undefined,
          event_id: eventId,
        })
        setToast({
          severity: 'success',
          message:
            'Calling your phone now — answer it and we’ll connect you. The contact sees the business number.',
        })
      } catch (err) {
        const code = err?.response?.data?.detail?.code || err?.response?.data?.detail
        const status = err?.response?.status
        if (status === 503 || code === 'voice_not_configured') {
          // Voice isn't set up — fall back to the native dialer, clearly.
          setToast({
            severity: 'info',
            message:
              'Business-number calling isn’t set up yet — using this device’s dialer instead.',
          })
        } else if (code === 'rep_phone_missing') {
          setRepPhoneInput(readRepPhone())
          setRepPhoneDialogOpen(true)
        } else {
          setToast({
            severity: 'error',
            message:
              err?.response?.data?.detail?.provider_error ||
              (typeof code === 'string' ? code : 'Could not start the call.'),
          })
        }
      } finally {
        bridgeInFlightRef.current = false
        setBridgePending(false)
      }
    },
    [contactId, eventId],
  )

  function onBusinessCallClick() {
    setMenuAnchor(null)
    const saved = readRepPhone()
    if (!saved) {
      // First business call on this device — ask which number to ring.
      setRepPhoneInput('')
      setRepPhoneDialogOpen(true)
      return
    }
    placeBridgeCall(saved)
  }

  function confirmRepPhone() {
    const val = repPhoneInput.trim()
    if (!val) return
    writeRepPhone(val)
    setRepPhoneDialogOpen(false)
    placeBridgeCall(val)
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

  const caret = (
    <Tooltip title="Call options" arrow>
      <span>
        <IconButton
          size="small"
          onClick={(e) => setMenuAnchor(e.currentTarget)}
          disabled={bridgePending}
          aria-label="Call options"
          sx={{ p: 0.25 }}
        >
          {bridgePending ? (
            <CircularProgress size={14} />
          ) : (
            <ArrowDropDownIcon fontSize="small" />
          )}
        </IconButton>
      </span>
    </Tooltip>
  )

  return (
    <>
      <Box
        component="span"
        sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.25 }}
      >
        {trigger}
        {caret}
      </Box>

      <Menu
        anchorEl={menuAnchor}
        open={Boolean(menuAnchor)}
        onClose={() => setMenuAnchor(null)}
      >
        <MenuItem
          onClick={() => {
            setMenuAnchor(null)
            handleCall()
          }}
        >
          <ListItemIcon>
            <SmartphoneOutlinedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText
            primary="Native call"
            secondary="Uses this device’s dialer"
          />
        </MenuItem>
        <MenuItem onClick={onBusinessCallClick}>
          <ListItemIcon>
            <BusinessOutlinedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText
            primary="Business number call"
            secondary="Customer sees the business number"
          />
        </MenuItem>
      </Menu>

      <Dialog
        open={repPhoneDialogOpen}
        onClose={() => setRepPhoneDialogOpen(false)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Which number should we ring?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            We’ll call this phone first, then connect you to the contact. The
            contact sees the business number — not this one. Saved on this
            device for next time.
          </DialogContentText>
          <TextField
            autoFocus
            fullWidth
            label="Your phone number"
            placeholder="+1 210 555 0134"
            value={repPhoneInput}
            onChange={(e) => setRepPhoneInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') confirmRepPhone()
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRepPhoneDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={confirmRepPhone}
            disabled={!repPhoneInput.trim()}
          >
            Call me
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={6000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {toast ? (
          <Alert
            severity={toast.severity}
            onClose={() => setToast(null)}
            sx={{ width: '100%' }}
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>

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
