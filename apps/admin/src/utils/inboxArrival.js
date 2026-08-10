// Decides what a poll of /inbox/unread-count means: seed the watermark,
// ignore it, or toast. Pure so it can be checked without a browser — see
// scripts/check-inbox-arrival.mjs. The provider owns the refs; this owns the
// rules, because the rules are where the edge cases live.

export const SEED = 'seed'
export const IGNORE = 'ignore'
export const TOAST = 'toast'

// Only toast for genuinely fresh arrivals. Without this, returning to a tab
// left open overnight pops a toast for something that landed twelve hours
// ago — the badge is the honest carrier for that.
export const MAX_TOAST_AGE_MS = 5 * 60 * 1000

/**
 * @param {object}  p
 * @param {boolean} p.seeded          has any poll completed this session
 * @param {number}  p.seenAt          newest inbound ms already accounted for
 * @param {object?} p.latest          the `latest` block from the endpoint
 * @param {number}  p.now             Date.now(), injected for testability
 * @param {number?} p.activeId        conversation currently open on screen
 * @param {string[]?} p.toastChannels null = every channel may toast
 * @returns {{action: string, seenAt: number}}
 */
export function classifyPoll({
  seeded,
  seenAt,
  latest,
  now,
  activeId = null,
  toastChannels = null,
}) {
  const parsed = latest?.last_inbound_at
    ? new Date(latest.last_inbound_at).getTime()
    : NaN
  const arrivedAt = Number.isNaN(parsed) ? 0 : parsed

  // First poll of the session establishes the watermark silently, so signing
  // in with a backlog does not fire a toast per unread thread. An empty inbox
  // seeds 0 — otherwise "no watermark yet" would be indistinguishable from
  // "not seeded", and the next arrival would be swallowed as its own seed.
  if (!seeded) return { action: SEED, seenAt: arrivedAt }

  if (!arrivedAt) return { action: IGNORE, seenAt }

  // Strictly newer, not merely different: reading the newest thread makes an
  // older one become `latest`, and that is not an arrival.
  if (arrivedAt <= seenAt) return { action: IGNORE, seenAt }

  // The watermark advances even when the toast is suppressed below, so a
  // message you chose not to be interrupted by is not re-offered next poll.
  if (now - arrivedAt > MAX_TOAST_AGE_MS) return { action: IGNORE, seenAt: arrivedAt }
  if (activeId !== null && activeId === latest.conversation_id) {
    return { action: IGNORE, seenAt: arrivedAt }
  }
  if (toastChannels && !toastChannels.includes(latest.channel)) {
    return { action: IGNORE, seenAt: arrivedAt }
  }

  return { action: TOAST, seenAt: arrivedAt }
}
