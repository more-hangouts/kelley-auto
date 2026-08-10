import api from './client'

// Browser softphone + inbound call routing.
//
// Split out of contacts.js once inbound landed: these endpoints are about the
// phone system itself (tokens, presence, hold, routing config) rather than
// about a contact, and there are now enough of them to earn their own module.

// Whether the in-browser call control should render at all.
export async function getVoiceStatus() {
  const { data } = await api.get('/voice/status')
  return data
}

// Short-lived Twilio Voice AccessToken for THIS user's browser client.
// `can_receive` reports whether the token carries an incoming grant, which is
// what decides if the SDK should register to take inbound calls.
export async function getVoiceAccessToken() {
  const { data } = await api.post('/voice/token')
  return data
}

// Heartbeat: "this dashboard is registered and taking calls." Inbound routing
// rings only reps who have heartbeated recently, so an empty office falls
// straight through to the fallback number instead of ringing nobody.
export async function sendVoicePresence(available = true) {
  const { data } = await api.post('/voice/presence', { available })
  return data
}

// Clean sign-off. Staleness covers closed laptops; this makes the tidy case
// instant.
export async function clearVoicePresence() {
  const { data } = await api.delete('/voice/presence')
  return data
}

// Context for the call currently ringing this dashboard. The Voice SDK hands
// the browser a leg whose `From` is our own Twilio number, so the real caller
// and their matched contact come from here.
export async function getVoiceRingingCall() {
  const { data } = await api.get('/voice/ringing')
  return data
}

// Put the CALLER on hold (or take them off), leaving the rep's leg up. Only
// valid for calls answered in the browser — those route via a conference, and
// holding is a conference-participant operation.
export async function setVoiceCallHold(callSid, on) {
  const { data } = await api.post(`/voice/calls/${callSid}/hold`, { on })
  return data
}

// Inbound routing config: where calls go and how long each stage rings.
export async function getVoiceSettings() {
  const { data } = await api.get('/voice/settings')
  return data
}

// Admin-only. Pass fallback_number: null to clear it (callers then hear the
// unavailable message rather than reaching a number nobody maintains).
export async function updateVoiceSettings(patch) {
  const { data } = await api.put('/voice/settings', patch)
  return data
}
