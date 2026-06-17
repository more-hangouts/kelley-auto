import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import CameraAltIcon from '@mui/icons-material/CameraAlt'
import ReplayIcon from '@mui/icons-material/Replay'
import WifiIcon from '@mui/icons-material/Wifi'

import { salesPunchIn, salesPunchOut } from '../services/api'
import { useClockStatus, useInvalidateClockStatus } from './useClockStatus'

const SELFIE_TARGET_QUALITY = 0.85
const SELFIE_TARGET_TYPE = 'image/jpeg'

function formatTime(iso, tz) {
  if (!iso) return ''
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleTimeString()
  }
}

// Slice B: collect several readings over a short window and keep the
// best (smallest reported accuracy_m). Phones routinely return a coarse
// network-based fix as their FIRST reading and tighten to a real GPS
// fix over the next few seconds. Trusting the first reading is what
// produced the "I'm standing inside the boutique but it says I'm 230m
// away" support tickets.
const SAMPLING_WINDOW_MS = 12_000
const SAMPLING_EARLY_EXIT_M = 20

function sampleBestPosition({ onProgress } = {}) {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('geolocation_unsupported'))
      return
    }
    let best = null
    let watchId = null
    let timer = null
    let settled = false

    const finish = (err) => {
      if (settled) return
      settled = true
      if (watchId != null) navigator.geolocation.clearWatch(watchId)
      if (timer) clearTimeout(timer)
      if (best) {
        resolve(best)
        return
      }
      reject(err || new Error('no_position'))
    }

    watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const sample = {
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy_m: pos.coords.accuracy,
        }
        if (!best || sample.accuracy_m < best.accuracy_m) {
          best = sample
          if (onProgress) onProgress(sample)
        }
        // Once the phone reports a real GPS-tight fix there is no point
        // spending the rest of the window draining the battery for a
        // marginal improvement.
        if (sample.accuracy_m <= SAMPLING_EARLY_EXIT_M) finish()
      },
      (err) => {
        // Permission denied / unsupported are terminal. TIMEOUT and
        // POSITION_UNAVAILABLE are intermittent — keep the watch alive
        // until the outer timer expires so a transient failure does not
        // wipe out an earlier good sample.
        if (err && err.code === 1) finish(err)
      },
      {
        enableHighAccuracy: true,
        timeout: SAMPLING_WINDOW_MS,
        maximumAge: 0,
      },
    )

    timer = setTimeout(() => finish(), SAMPLING_WINDOW_MS)
  })
}

function geolocationErrorMessage(err) {
  if (!err) return 'Could not get your location.'
  if (err.message === 'geolocation_unsupported') {
    return "This device doesn't support location services."
  }
  switch (err.code) {
    case 1:
      return "Location permission was denied. Open Safari/Chrome settings, allow location for this site, and tap Retry."
    case 2:
      return 'Your device could not determine its location. Try moving away from walls or windows and tap Retry.'
    case 3:
      return 'Location lookup timed out. Tap Retry.'
    default:
      return 'Could not get your location.'
  }
}

function describeGateError(detail, action, status) {
  if (!detail) return 'That action failed. Try again.'
  const code = detail.code
  if (code === 'outside_geofence') {
    const dist = detail.distance_m
    const closest = detail.closest_location_name
    const buffer = detail.accuracy_buffer_m
    let core = ''
    if (dist != null && closest) {
      core = `You're about ${Math.round(dist)}m from ${closest}.`
    } else if (dist != null) {
      core = `You're about ${Math.round(dist)}m from the nearest boutique.`
    } else {
      core = "You're outside the boutique geofence."
    }
    if (buffer != null && buffer > 0) {
      core += ` We already allowed ±${Math.round(buffer)}m of GPS slack.`
    }
    // When the trusted-network bypass is on and the request did NOT
    // come from a trusted IP, the user has a second path: connect to
    // the boutique Wi-Fi. Don't surface this option when the bypass
    // is off; that would just confuse them.
    if (
      status?.trusted_network_enabled &&
      !status?.trusted_network_detected
    ) {
      return `${core} Connect to the boutique Wi-Fi or move closer, then tap Retry.`
    }
    return `${core} Move closer to the boutique to clock ${action}.`
  }
  if (code === 'already_punched_in') return "You're already clocked in."
  if (code === 'not_punched_in') return "You're not clocked in yet."
  if (code === 'selfie_required') return 'A selfie is required to clock in. Tap the camera button.'
  if (code === 'selfie_disabled') return 'Selfies are disabled for this boutique. Try again without one.'
  if (code === 'selfie_too_large') return 'That photo is too large. Take a new one.'
  if (code === 'selfie_unsupported_type') return "That file type isn't supported."
  if (code === 'selfie_invalid') return "We couldn't read that photo. Try again."
  if (code === 'selfie_storage_unavailable') {
    return "We can't reach photo storage right now. Tell the owner the selfie service is down. Your time is not yet recorded."
  }
  if (code === 'too_early_for_shift') {
    const earliest = detail.earliest_allowed_at
    if (earliest) {
      try {
        const when = new Date(earliest).toLocaleTimeString(undefined, {
          hour: 'numeric',
          minute: '2-digit',
        })
        return `Too early to clock in. You can clock in starting at ${when}.`
      } catch {
        // Fall through to generic copy if the timestamp didn't parse.
      }
    }
    return 'Too early to clock in for your shift. Try again closer to your start time.'
  }
  return detail.message || 'That action failed. Try again.'
}

export default function ClockScreen() {
  const navigate = useNavigate()
  const location = useLocation()
  const { status, isLoading, refetch } = useClockStatus()
  const invalidate = useInvalidateClockStatus()

  const [coords, setCoords] = useState(null)
  const [coordsError, setCoordsError] = useState(null)
  const [coordsBusy, setCoordsBusy] = useState(false)
  // Slice B: the live "best so far" while sampling, so the UI can show
  // "Improving location · ±Xm" with a real number instead of a generic
  // spinner. Cleared on the next retry.
  const [coordsProgress, setCoordsProgress] = useState(null)

  const [selfieBlob, setSelfieBlob] = useState(null)
  const [selfiePreviewUrl, setSelfiePreviewUrl] = useState(null)
  const [cameraOpen, setCameraOpen] = useState(false)
  const [cameraError, setCameraError] = useState(null)
  const videoRef = useRef(null)
  const streamRef = useRef(null)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(null)

  const action = status?.state === 'in' ? 'out' : 'in'
  const policy = status?.selfie_policy || 'optional'
  const selfieAllowed = policy !== 'disabled'
  const selfieRequired = policy === 'required'

  // Acquire coords as soon as the screen mounts. Geolocation prompt
  // can take a beat; getting it kicked off first means by the time
  // the user has decided whether to take a selfie, we already have
  // their location.
  useEffect(() => {
    captureCoords()
  }, [])

  // Tear down camera stream on unmount or when the user closes it.
  useEffect(() => {
    return () => stopCamera()
  }, [])

  // Revoke object URLs we created for the selfie preview.
  useEffect(() => {
    return () => {
      if (selfiePreviewUrl) URL.revokeObjectURL(selfiePreviewUrl)
    }
  }, [selfiePreviewUrl])

  async function captureCoords() {
    setCoordsError(null)
    setCoordsBusy(true)
    setCoordsProgress(null)
    setCoords(null)
    try {
      const c = await sampleBestPosition({
        onProgress: (sample) => setCoordsProgress(sample),
      })
      setCoords(c)
    } catch (err) {
      setCoords(null)
      setCoordsError(geolocationErrorMessage(err))
    } finally {
      setCoordsBusy(false)
      setCoordsProgress(null)
    }
  }

  async function openCamera() {
    setCameraError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user' },
        audio: false,
      })
      streamRef.current = stream
      setCameraOpen(true)
      // Defer attaching the stream until the <video> element exists.
      requestAnimationFrame(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream
        }
      })
    } catch (err) {
      setCameraError(
        err && err.name === 'NotAllowedError'
          ? 'Camera permission was denied. Allow camera in your browser settings and try again.'
          : 'Could not start the camera.',
      )
    }
  }

  function stopCamera() {
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) track.stop()
      streamRef.current = null
    }
    setCameraOpen(false)
  }

  async function captureSelfie() {
    const video = videoRef.current
    if (!video) return
    const canvas = document.createElement('canvas')
    // Cap canvas to 1024 on the long edge — backend caps to 1024 too,
    // sending less than that to the wire saves transfer time.
    const maxEdge = 1024
    const scale = Math.min(
      1,
      maxEdge / Math.max(video.videoWidth, video.videoHeight),
    )
    canvas.width = Math.round(video.videoWidth * scale)
    canvas.height = Math.round(video.videoHeight * scale)
    const ctx = canvas.getContext('2d')
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setCameraError('Could not capture the photo. Try again.')
          return
        }
        if (selfiePreviewUrl) URL.revokeObjectURL(selfiePreviewUrl)
        setSelfieBlob(blob)
        setSelfiePreviewUrl(URL.createObjectURL(blob))
        stopCamera()
      },
      SELFIE_TARGET_TYPE,
      SELFIE_TARGET_QUALITY,
    )
  }

  function clearSelfie() {
    if (selfiePreviewUrl) URL.revokeObjectURL(selfiePreviewUrl)
    setSelfieBlob(null)
    setSelfiePreviewUrl(null)
  }

  async function handleSubmit() {
    if (submitting) return
    setSubmitError(null)
    if (!coords && !onTrustedNetwork) {
      setSubmitError('We need your location before clocking ' + action + '.')
      return
    }
    if (selfieRequired && !selfieBlob) {
      setSubmitError(
        'A selfie is required to clock ' + action + '. Tap the camera button.',
      )
      return
    }
    setSubmitting(true)
    try {
      const fn = action === 'in' ? salesPunchIn : salesPunchOut
      await fn({
        // Coords may be absent on the boutique WiFi fast-path; the API
        // helper omits them and the server accepts via trusted network.
        latitude: coords?.latitude,
        longitude: coords?.longitude,
        accuracy_m: coords?.accuracy_m,
        selfieBlob,
      })
      // Clear local capture and refresh shared status.
      clearSelfie()
      invalidate()
      // After clocking in, route home. After clocking out, stay on
      // /clock so the stylist can confirm the out punch landed.
      const dest = action === 'in' ? '/' : null
      if (dest) {
        // Honor a `?next=` redirect if SalesProtectedRoute sent us here.
        const next = new URLSearchParams(location.search).get('next')
        navigate(next || dest, { replace: true })
      } else {
        await refetch()
      }
    } catch (err) {
      setSubmitError(
        describeGateError(err?.response?.data?.detail, action, status),
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    )
  }

  // Phase 13: kiosk-card layout. One centered card consolidates the
  // attendance header, optional selfie capture, optional trusted-network
  // indicator, an inline GPS readiness row, and the primary punch
  // button. No semantic changes — the geofence, selfie, and punch
  // behavior all live in the same handlers as before.
  const onClock = status?.state === 'in'
  const gpsReady = Boolean(coords)
  // On the boutique WiFi the punch goes through without a GPS fix, so
  // the button is live the moment the screen loads — the girls no
  // longer wait out the 30s location lock. GPS still runs in the
  // background and rides along for the audit trail if it resolves.
  const onTrustedNetwork = Boolean(
    status?.trusted_network_detected && status?.trusted_network_enabled,
  )
  const locationReady = gpsReady || onTrustedNetwork
  const buttonDisabled =
    submitting || !locationReady || (selfieRequired && !selfieBlob)
  // 13.1 spec: when the only thing blocking the button is GPS, the
  // label says so explicitly. Selfie-blocked / submitting cases keep
  // the action verb so the user knows what they're doing.
  const waitingForLocation = !submitting && !locationReady
  const buttonLabel = waitingForLocation
    ? 'Waiting for location…'
    : onClock
    ? 'Clock out'
    : 'Clock in'

  return (
    <Box sx={{ maxWidth: 460, mx: 'auto', width: '100%' }}>
      <Card>
        <CardContent>
          <Stack spacing={2}>
            {/* Header: action + always-filled status pill (visually
                static — never a button). */}
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
              spacing={1}
            >
              <Box>
                <Typography variant="overline" color="text.secondary">
                  Attendance
                </Typography>
                <Typography variant="h5" sx={{ fontWeight: 600 }}>
                  {onClock ? 'Clock out' : 'Clock in'}
                </Typography>
              </Box>
              <Chip
                label={onClock ? 'On the clock' : 'Off the clock'}
                color={onClock ? 'success' : 'default'}
                variant="filled"
                sx={{ fontWeight: 500 }}
              />
            </Stack>

            {status?.last_punch && (
              <Typography variant="body2" color="text.secondary">
                Last punch:{' '}
                {status.last_punch.direction === 'in' ? 'in' : 'out'} at{' '}
                {formatTime(status.last_punch.punched_at, status.timezone)}
              </Typography>
            )}

            {/* Selfie section. Same camera/preview/retake/permission-error
                behavior as before; visually the only changes are the
                full-width secondary button and living inside the
                consolidated card. */}
            {selfieAllowed && (
              <Box>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mb: 0.75 }}
                >
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ flex: 1, fontWeight: 600, letterSpacing: 0.4 }}
                  >
                    {selfieRequired ? 'SELFIE (REQUIRED)' : 'SELFIE (OPTIONAL)'}
                  </Typography>
                  {selfieBlob && (
                    <IconButton
                      size="small"
                      onClick={() => {
                        clearSelfie()
                        openCamera()
                      }}
                      aria-label="Retake selfie"
                    >
                      <ReplayIcon fontSize="small" />
                    </IconButton>
                  )}
                </Stack>

                {cameraError && (
                  <Alert severity="warning" sx={{ mb: 1 }}>
                    {cameraError}
                  </Alert>
                )}

                {!cameraOpen && !selfieBlob && (
                  <Button
                    fullWidth
                    variant="outlined"
                    startIcon={<CameraAltIcon />}
                    onClick={openCamera}
                  >
                    Take a selfie
                  </Button>
                )}

                {cameraOpen && (
                  <Stack spacing={1}>
                    <Box
                      sx={{
                        width: '100%',
                        aspectRatio: '3 / 4',
                        bgcolor: 'common.black',
                        borderRadius: 1,
                        overflow: 'hidden',
                        position: 'relative',
                      }}
                    >
                      <video
                        ref={videoRef}
                        autoPlay
                        playsInline
                        muted
                        style={{
                          width: '100%',
                          height: '100%',
                          objectFit: 'cover',
                          transform: 'scaleX(-1)',
                        }}
                      />
                    </Box>
                    <Stack direction="row" spacing={1} justifyContent="flex-end">
                      <Button onClick={stopCamera}>Cancel</Button>
                      <Button variant="contained" onClick={captureSelfie}>
                        Capture
                      </Button>
                    </Stack>
                  </Stack>
                )}

                {selfieBlob && !cameraOpen && (
                  <Box
                    component="img"
                    src={selfiePreviewUrl}
                    alt="Selfie preview"
                    sx={{
                      width: '100%',
                      maxWidth: 280,
                      aspectRatio: '3 / 4',
                      objectFit: 'cover',
                      borderRadius: 1,
                      display: 'block',
                      mt: 1,
                    }}
                  />
                )}
              </Box>
            )}

            {/* Trusted-network indicator. Only renders when the request
                came in from the boutique's public IP — exactly the same
                trigger as before; just inline inside the unified card
                instead of in its own outlined card. */}
            {status?.trusted_network_detected && (
              <Stack
                direction="row"
                spacing={1}
                alignItems="flex-start"
                sx={{
                  bgcolor: 'action.hover',
                  borderRadius: 1,
                  px: 1.25,
                  py: 1,
                }}
              >
                <WifiIcon
                  fontSize="small"
                  color={status.trusted_network_enabled ? 'success' : 'action'}
                  sx={{ mt: '2px' }}
                />
                <Typography variant="body2" sx={{ flex: 1 }}>
                  Connected through boutique network.
                  {!status.trusted_network_enabled
                    ? ' Not yet a backup path; for audit only.'
                    : ' Clock-in works here even without GPS.'}
                </Typography>
              </Stack>
            )}

            {/* Compact GPS readiness row directly above the primary
                button. Shows a tiny spinner while resolving and the
                'best so far' accuracy when available. The retry icon
                stays reachable per accessibility guardrail. */}
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                borderTop: '1px solid',
                borderColor: 'divider',
                pt: 1.5,
              }}
            >
              {coordsBusy && (
                <CircularProgress size={14} sx={{ color: 'text.secondary' }} />
              )}
              <Typography
                variant="body2"
                color={gpsReady ? 'text.primary' : 'text.secondary'}
                sx={{ flex: 1 }}
              >
                {coordsBusy && !coordsProgress && 'Asking your device for GPS…'}
                {coordsBusy &&
                  coordsProgress &&
                  `Improving location · ±${Math.round(
                    coordsProgress.accuracy_m,
                  )}m so far`}
                {!coordsBusy &&
                  gpsReady &&
                  `GPS locked · ±${Math.round(coords.accuracy_m)}m`}
                {!coordsBusy &&
                  !gpsReady &&
                  onTrustedNetwork &&
                  'On boutique WiFi · GPS optional'}
                {!coordsBusy &&
                  !gpsReady &&
                  !onTrustedNetwork &&
                  !coordsError &&
                  'Waiting for GPS…'}
                {!coordsBusy &&
                  !gpsReady &&
                  !onTrustedNetwork &&
                  coordsError &&
                  'GPS unavailable'}
              </Typography>
              <IconButton
                size="small"
                onClick={captureCoords}
                disabled={coordsBusy}
                aria-label="Retry location"
              >
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Stack>

            {coordsError && <Alert severity="warning">{coordsError}</Alert>}

            {submitError && <Alert severity="error">{submitError}</Alert>}

            <Button
              variant="contained"
              size="large"
              fullWidth
              onClick={handleSubmit}
              disabled={buttonDisabled}
              sx={{ py: 1.75, fontSize: '1.1rem', fontWeight: 600 }}
            >
              {submitting ? (
                <CircularProgress size={22} sx={{ color: 'common.white' }} />
              ) : (
                buttonLabel
              )}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  )
}
