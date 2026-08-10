// Decision table for the dashboard's new-message toast.
//
// Run: node scripts/check-inbox-arrival.mjs
//
// The admin app has no test runner (the repo's convention is standalone
// script smokes on the Python side and containerized Playwright for E2E), so
// this follows check-api-exports.mjs: a plain node script, no dependencies.
//
// Every case here is a real failure mode, and the first one listed is a bug
// this file was written in response to.

import assert from 'node:assert/strict'

import {
  classifyPoll,
  SEED,
  IGNORE,
  TOAST,
  MAX_TOAST_AGE_MS,
} from '../src/utils/inboxArrival.js'

const NOW = 1_760_000_000_000
const iso = (msAgo) => new Date(NOW - msAgo).toISOString()

const chat = (msAgo, over = {}) => ({
  conversation_id: 175,
  channel: 'web_chat',
  display_name: 'Jessica',
  preview: 'Looking for a van',
  last_inbound_at: iso(msAgo),
  ...over,
})

let passed = 0
function check(name, got, want) {
  assert.equal(got.action, want, `${name}: expected ${want}, got ${got.action}`)
  passed += 1
  console.log(`  ✓ ${name}`)
}

console.log('inbox arrival decision table\n')

// The regression this file exists for.
{
  const first = classifyPoll({ seeded: false, seenAt: 0, latest: null, now: NOW })
  check('empty inbox at sign-in seeds without toasting', first, SEED)
  assert.equal(first.seenAt, 0, 'an empty seed must leave the watermark at 0')

  const then = classifyPoll({
    seeded: true,
    seenAt: first.seenAt,
    latest: chat(1000),
    now: NOW,
  })
  check('...and the FIRST message after an empty seed still toasts', then, TOAST)
}

check(
  'signing in to a backlog seeds silently',
  classifyPoll({ seeded: false, seenAt: 0, latest: chat(60_000), now: NOW }),
  SEED,
)

check(
  'a genuinely new message toasts',
  classifyPoll({ seeded: true, seenAt: NOW - 90_000, latest: chat(1000), now: NOW }),
  TOAST,
)

check(
  'the same message on the next poll does not toast twice',
  classifyPoll({ seeded: true, seenAt: NOW - 1000, latest: chat(1000), now: NOW }),
  IGNORE,
)

check(
  'reading the newest thread surfaces an older one — not an arrival',
  classifyPoll({ seeded: true, seenAt: NOW - 1000, latest: chat(400_000), now: NOW }),
  IGNORE,
)

check(
  'inbox emptied to zero unread does not toast',
  classifyPoll({ seeded: true, seenAt: NOW - 1000, latest: null, now: NOW }),
  IGNORE,
)

check(
  'a stale message found on returning to a long-open tab does not toast',
  classifyPoll({
    seeded: true,
    seenAt: 0,
    latest: chat(MAX_TOAST_AGE_MS + 60_000),
    now: NOW,
  }),
  IGNORE,
)

check(
  'no toast for the thread already open on screen',
  classifyPoll({
    seeded: true,
    seenAt: NOW - 90_000,
    latest: chat(1000),
    now: NOW,
    activeId: 175,
  }),
  IGNORE,
)

check(
  'a different thread still toasts while one is open',
  classifyPoll({
    seeded: true,
    seenAt: NOW - 90_000,
    latest: chat(1000),
    now: NOW,
    activeId: 999,
  }),
  TOAST,
)

check(
  'channel filter suppresses when narrowed',
  classifyPoll({
    seeded: true,
    seenAt: NOW - 90_000,
    latest: chat(1000, { channel: 'sms' }),
    now: NOW,
    toastChannels: ['web_chat'],
  }),
  IGNORE,
)

check(
  'null channel filter lets every channel through',
  classifyPoll({
    seeded: true,
    seenAt: NOW - 90_000,
    latest: chat(1000, { channel: 'sms' }),
    now: NOW,
    toastChannels: null,
  }),
  TOAST,
)

check(
  'an unparseable timestamp is ignored, not treated as 1970',
  classifyPoll({
    seeded: true,
    seenAt: NOW - 90_000,
    latest: chat(1000, { last_inbound_at: 'not-a-date' }),
    now: NOW,
  }),
  IGNORE,
)

// A suppressed message must not be re-offered on the next poll.
{
  const suppressed = classifyPoll({
    seeded: true,
    seenAt: NOW - 90_000,
    latest: chat(1000),
    now: NOW,
    activeId: 175,
  })
  const next = classifyPoll({
    seeded: true,
    seenAt: suppressed.seenAt,
    latest: chat(1000),
    now: NOW,
    activeId: null,
  })
  check('a suppressed message is not re-offered after closing the thread', next, IGNORE)
}

console.log(`\n✓ ${passed} arrival cases hold`)
