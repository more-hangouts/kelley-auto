// Browser softphone runtime (Twilio Voice JS SDK).
//
// Owns the ONE `Device` for the whole dashboard. A Device holds a websocket
// registration and a media stack; creating one per contact row would open a
// socket per row and race on the microphone, so it lives here at app level and
// every call control talks to it through context.
//
// Division of labour, deliberately:
//   * This provider owns the Device, the AccessToken lifecycle, and the ACTIVE
//     call (so the in-call bar survives navigating between pages mid-call).
//   * The caller (CallContact) owns what happens AFTER a call ends — it passes
//     `onEnded` so its existing outcome sheet is reused rather than duplicated.
//
// The destination number is never sent from here. `placeCall` receives a signed
// dial token minted server-side; Twilio's TwiML route resolves the number from
// that token. See modules/messaging/routers/webhooks_twilio.py.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  clearVoicePresence,
  getVoiceAccessToken,
  getVoiceRingingCall,
  getVoiceStatus,
  sendVoicePresence,
  setVoiceCallHold,
} from '../../services/api'

// How often a registered dashboard tells the server it is still there. The
// server treats presence as stale after 75s, so one missed beat (a hiccup, a
// briefly backgrounded tab) does not drop a rep out of the call rotation.
const HEARTBEAT_MS = 30_000

const SoftphoneContext = createContext(null)

// Call lifecycle as the UI cares about it. These constants and the hook ship
// alongside the provider (same trade-off CommandPaletteContext makes) — the
// consumers are few and keeping them together avoids a two-file indirection.
/* eslint-disable react-refresh/only-export-components */

export const CALL_IDLE = 'idle'
export const CALL_CONNECTING = 'connecting'
export const CALL_RINGING = 'ringing'
export const CALL_ACTIVE = 'active'
// An inbound call is ringing THIS browser and awaiting answer/decline.
export const CALL_INCOMING = 'incoming'

export function useSoftphone() {
  const ctx = useContext(SoftphoneContext)
  // Null outside the provider (e.g. the login screen). Callers treat a missing
  // context the same as an unavailable softphone, so nothing has to special-case
  // being rendered outside the dashboard shell.
  return ctx
}

export default function SoftphoneProvider({ children }) {
  const [available, setAvailable] = useState(false)
  const [fromNumber, setFromNumber] = useState(null)
  const [callState, setCallState] = useState(CALL_IDLE)
  const [muted, setMuted] = useState(false)
  const [peer, setPeer] = useState(null) // { label, phone }
  const [error, setError] = useState(null)
  const [startedAt, setStartedAt] = useState(null)
  // Inbound: can this browser receive calls, is it registered, is the rep
  // accepting, and is the caller currently on hold.
  const [canReceive, setCanReceive] = useState(false)
  const [registered, setRegistered] = useState(false)
  const [availableForCalls, setAvailableForCalls] = useState(true)
  const [onHold, setOnHold] = useState(false)
  const [holdPending, setHoldPending] = useState(false)
  const [incoming, setIncoming] = useState(null) // { from, contactName, callSid }

  const deviceRef = useRef(null)
  const callRef = useRef(null)
  const onEndedRef = useRef(null)
  // The inbound call's server-side record, needed to hold the CALLER's leg
  // (the sid the browser sees is the rep's leg, not the caller's).
  const inboundSidRef = useRef(null)
  const incomingCallRef = useRef(null)
  // Guards a second click while the first call is still setting up — Device
  // rejects a concurrent connect, and the async state flag lands too late.
  const connectingRef = useRef(false)

  // Ask the server whether the feature is on before loading anything. Keeps the
  // call button hidden (rather than failing on click) when Twilio isn't set up.
  useEffect(() => {
    let cancelled = false
    getVoiceStatus()
      .then((s) => {
        if (!cancelled) setAvailable(Boolean(s?.enabled))
      })
      .catch(() => {
        // 401/403/network — treat as unavailable and stay silent; this runs on
        // every dashboard mount and must never surface an error to the rep.
        if (!cancelled) setAvailable(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const teardownCall = useCallback((endReason) => {
    const ended = callRef.current
    callRef.current = null
    incomingCallRef.current = null
    inboundSidRef.current = null
    connectingRef.current = false
    setCallState(CALL_IDLE)
    setMuted(false)
    setOnHold(false)
    setIncoming(null)
    setStartedAt(null)
    const cb = onEndedRef.current
    onEndedRef.current = null
    setPeer(null)
    if (cb) {
      try {
        cb({ reason: endReason, call: ended })
      } catch {
        /* a consumer's handler must never break call teardown */
      }
    }
  }, [])

  // Destroy the Device on unmount so a logout/navigation doesn't leave a live
  // websocket and an open microphone behind.
  useEffect(
    () => () => {
      try {
        callRef.current?.disconnect()
        deviceRef.current?.destroy()
      } catch {
        /* best effort */
      }
      deviceRef.current = null
      callRef.current = null
    },
    [],
  )

  // An inbound leg arrived. Hold it in `incoming` state — never auto-answer —
  // and enrich the card from the server, because the SDK's `From` on this leg
  // is our own Twilio number rather than the customer's.
  const handleIncoming = useCallback((call) => {
    incomingCallRef.current = call
    setError(null)
    setCallState(CALL_INCOMING)
    setIncoming({ from: null, contactName: null, callSid: null })

    getVoiceRingingCall()
      .then((res) => {
        const c = res?.call
        if (!c) return
        inboundSidRef.current = c.call_sid
        setIncoming({
          from: c.from_number,
          contactName: c.contact_name,
          contactId: c.contact_id,
          city: c.caller_city,
          state: c.caller_state,
          callSid: c.call_sid,
        })
      })
      .catch(() => {
        /* the card still renders; it just says "Unknown caller" */
      })

    // The caller hung up (or another rep answered) before this rep acted.
    call.on('cancel', () => {
      incomingCallRef.current = null
      setIncoming(null)
      setCallState(CALL_IDLE)
    })
    call.on('disconnect', () => teardownCall('disconnect'))
  }, [teardownCall])

  // Lazily create the Device on first use. The SDK is a heavy dependency, so
  // it is dynamically imported — reps who never place a call never download it,
  // and it stays out of the initial bundle.
  const ensureDevice = useCallback(async () => {
    if (deviceRef.current) return deviceRef.current

    const { Device } = await import('@twilio/voice-sdk')
    const {
      token,
      from_number: from,
      can_receive: receive,
    } = await getVoiceAccessToken()
    setFromNumber(from || null)
    setCanReceive(Boolean(receive))

    const device = new Device(token, {
      // opus first for quality; pcmu is the fallback every carrier accepts.
      codecPreferences: ['opus', 'pcmu'],
      logLevel: 'error',
    })

    // AccessTokens are short-lived. Refresh in place so a long call (or a long
    // idle tab) never drops on expiry.
    device.on('tokenWillExpire', async () => {
      try {
        const next = await getVoiceAccessToken()
        device.updateToken(next.token)
      } catch {
        /* the next placeCall rebuilds the Device from scratch */
      }
    })

    device.on('error', (e) => {
      setError(e?.message || 'Phone error')
    })

    // Inbound. Registering is what makes this browser reachable as
    // `client:userN`; without it the server's ring leg has nowhere to land.
    // Only meaningful when the token carries an incoming grant.
    if (receive) {
      device.on('registered', () => setRegistered(true))
      device.on('unregistered', () => setRegistered(false))
      device.on('incoming', handleIncoming)
      try {
        await device.register()
      } catch {
        // Outbound still works unregistered; surface nothing, the presence
        // indicator simply stays offline.
        setRegistered(false)
      }
    }

    deviceRef.current = device
    return device
  }, [handleIncoming])

  /**
   * Place a call. `dialToken` is the signed authorization from
   * POST /contacts/{id}/call-attempts/browser — it, not this browser, decides
   * which number is dialed.
   */
  const placeCall = useCallback(
    async ({ dialToken, label, phone, onEnded }) => {
      if (connectingRef.current || callRef.current) {
        throw new Error('A call is already in progress.')
      }
      connectingRef.current = true
      setError(null)
      setPeer({ label: label || phone || 'Contact', phone: phone || null })
      setCallState(CALL_CONNECTING)
      onEndedRef.current = onEnded || null

      try {
        const device = await ensureDevice()
        // Custom params surface as form fields on the TwiML request.
        const call = await device.connect({ params: { DialToken: dialToken } })
        callRef.current = call
        setCallState(CALL_RINGING)

        call.on('accept', () => {
          setCallState(CALL_ACTIVE)
          setStartedAt(Date.now())
        })
        call.on('disconnect', () => teardownCall('disconnect'))
        call.on('cancel', () => teardownCall('cancel'))
        call.on('reject', () => teardownCall('reject'))
        call.on('error', (e) => {
          setError(e?.message || 'Call failed')
          teardownCall('error')
        })
        connectingRef.current = false
        return call
      } catch (err) {
        connectingRef.current = false
        onEndedRef.current = null
        setPeer(null)
        setCallState(CALL_IDLE)
        // A denied/absent microphone is the single most common failure here and
        // the SDK's own message ("NotAllowedError") means nothing to a rep.
        const name = err?.name || ''
        if (name === 'NotAllowedError' || /permission|denied/i.test(err?.message || '')) {
          throw new Error(
            'Microphone access is blocked. Allow the microphone for this site, then try again.',
          )
        }
        if (name === 'NotFoundError') {
          throw new Error('No microphone found on this computer.')
        }
        throw err
      }
    },
    [ensureDevice, teardownCall],
  )

  // --- inbound -------------------------------------------------------------

  const answerIncoming = useCallback(() => {
    const call = incomingCallRef.current
    if (!call) return
    incomingCallRef.current = null
    callRef.current = call

    call.on('disconnect', () => teardownCall('disconnect'))
    call.on('error', (e) => {
      setError(e?.message || 'Call failed')
      teardownCall('error')
    })

    call.accept()
    setIncoming(null)
    setCallState(CALL_ACTIVE)
    setStartedAt(Date.now())
  }, [teardownCall])

  const declineIncoming = useCallback(() => {
    const call = incomingCallRef.current
    incomingCallRef.current = null
    setIncoming(null)
    setCallState(CALL_IDLE)
    try {
      // `reject` is what tells Twilio this leg failed, which is the signal the
      // server counts toward "everyone declined → ring the fallback number".
      call?.reject()
    } catch {
      /* already gone */
    }
  }, [])

  // Hold acts on the CALLER's leg via the conference, not on this browser's
  // leg — that is the whole reason inbound calls route through a conference.
  const toggleHold = useCallback(async () => {
    const sid = inboundSidRef.current
    if (!sid || holdPending) return
    const next = !onHold
    setHoldPending(true)
    try {
      await setVoiceCallHold(sid, next)
      setOnHold(next)
    } catch (err) {
      setError(
        err?.response?.data?.detail === 'call_not_held_in_conference'
          ? 'Hold is only available for calls answered in the browser.'
          : 'Could not put the caller on hold.',
      )
    } finally {
      setHoldPending(false)
    }
  }, [onHold, holdPending])

  const hangUp = useCallback(() => {
    try {
      callRef.current?.disconnect()
    } catch {
      /* disconnect races a natural hangup; teardown still runs via the event */
    }
  }, [])

  const toggleMute = useCallback(() => {
    const call = callRef.current
    if (!call) return
    const next = !call.isMuted()
    call.mute(next)
    setMuted(next)
  }, [])

  // DTMF for phone trees ("press 1 for sales").
  const sendDigit = useCallback((digit) => {
    try {
      callRef.current?.sendDigits(String(digit))
    } catch {
      /* ignore — nothing sensible to show if a keypress fails */
    }
  }, [])

  // Bring the Device up as soon as the softphone is available, rather than
  // waiting for the rep's first outbound call — an unregistered browser cannot
  // RECEIVE anything, so lazy creation would mean inbound never rings until the
  // rep happened to dial out.
  useEffect(() => {
    if (!available) return
    ensureDevice().catch(() => {
      /* registration is best-effort; outbound still works on demand */
    })
  }, [available, ensureDevice])

  // Heartbeat while registered and accepting calls, so routing knows this
  // dashboard is really there. Stopping the beat is what takes a closed laptop
  // out of the rotation.
  useEffect(() => {
    if (!registered || !availableForCalls) return undefined
    let cancelled = false
    const beat = () => {
      if (!cancelled) sendVoicePresence(true).catch(() => {})
    }
    beat()
    const id = setInterval(beat, HEARTBEAT_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [registered, availableForCalls])

  // Drop out of the rotation promptly on an explicit "stop taking calls" and
  // on unmount, instead of making the next caller wait out the staleness window.
  useEffect(() => {
    if (registered && !availableForCalls) clearVoicePresence().catch(() => {})
  }, [registered, availableForCalls])

  useEffect(
    () => () => {
      clearVoicePresence().catch(() => {})
    },
    [],
  )

  const value = useMemo(
    () => ({
      available,
      fromNumber,
      callState,
      inCall: callState !== CALL_IDLE && callState !== CALL_INCOMING,
      muted,
      peer,
      error,
      startedAt,
      clearError: () => setError(null),
      placeCall,
      hangUp,
      toggleMute,
      sendDigit,
      // inbound
      canReceive,
      registered,
      availableForCalls,
      setAvailableForCalls,
      incoming,
      answerIncoming,
      declineIncoming,
      onHold,
      holdPending,
      toggleHold,
      // Hold needs the conference, which only inbound browser calls have.
      canHold: Boolean(inboundSidRef.current),
    }),
    [
      available,
      fromNumber,
      callState,
      muted,
      peer,
      error,
      startedAt,
      placeCall,
      hangUp,
      toggleMute,
      sendDigit,
      canReceive,
      registered,
      availableForCalls,
      incoming,
      answerIncoming,
      declineIncoming,
      onHold,
      holdPending,
      toggleHold,
    ],
  )

  return (
    <SoftphoneContext.Provider value={value}>{children}</SoftphoneContext.Provider>
  )
}
