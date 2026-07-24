// Smoke test for the Phase 2b per-line discount slider math.
//
// The slider in `frontend/src/components/LineDiscountControl.jsx` writes
// `discount_cents = bankRound(qty * unit_price_cents * percent / 100)`
// every time it moves. This script asserts that formula against a few
// representative inputs documented in the plan.
//
// No frontend test runner is configured in this repo, so the smoke
// runs as plain Node:
//   node tests/test_line_discount_slider_math.mjs

function bankRound(value) {
  const sign = value < 0 ? -1 : 1
  const abs = Math.abs(value)
  const floor = Math.floor(abs)
  const diff = abs - floor
  if (diff > 0.5 || (diff === 0.5 && floor % 2 === 1)) {
    return sign * (floor + 1)
  }
  return sign * floor
}

function sliderCents({ qty, unitCents, percent }) {
  return bankRound((qty * unitCents * percent) / 100)
}

function deriveInitialPercent({ qty, unitCents, cents }) {
  const gross = qty * unitCents
  if (gross <= 0 || cents <= 0) return 0
  return Math.min(50, Math.max(0, Math.round((cents / gross) * 100)))
}

let failures = 0
function check(label, actual, expected) {
  const ok = actual === expected
  if (!ok) {
    failures += 1
    console.error(`FAIL ${label}: expected ${expected}, got ${actual}`)
  } else {
    console.log(`ok   ${label}`)
  }
}

// Plan example: $1,000 line at 10% → $100 off.
check(
  '10% off $1000',
  sliderCents({ qty: 1, unitCents: 100000, percent: 10 }),
  10000,
)

// 0% writes zero.
check(
  '0% writes zero',
  sliderCents({ qty: 1, unitCents: 100000, percent: 0 }),
  0,
)

// 50% upper bound on $200 → $100.
check(
  '50% off $200',
  sliderCents({ qty: 1, unitCents: 20000, percent: 50 }),
  10000,
)

// Banker's rounding: $1.25 line at 10% would be 12.5 cents → 12 (even).
check(
  "banker's rounding 0.5 toward even",
  sliderCents({ qty: 1, unitCents: 125, percent: 10 }),
  12,
)

// Quantity > 1: 3 units * $250 * 15% = $112.50 → 11250.
check(
  'qty 3 at $250 * 15%',
  sliderCents({ qty: 3, unitCents: 25000, percent: 15 }),
  11250,
)

// Fractional qty: 1.5 * $400 * 8% = 4800.
check(
  'qty 1.5 at $400 * 8%',
  sliderCents({ qty: 1.5, unitCents: 40000, percent: 8 }),
  4800,
)

// Reverse derivation: a line with $100 off out of $1000 should report
// 10% on re-open so the slider thumb lands where the user left it.
check(
  'derive 10% from $100 / $1000',
  deriveInitialPercent({ qty: 1, unitCents: 100000, cents: 10000 }),
  10,
)

// Zero gross: avoid divide-by-zero, fall back to 0%.
check(
  'derive on zero qty -> 0',
  deriveInitialPercent({ qty: 0, unitCents: 100000, cents: 5000 }),
  0,
)

// Manually entered cents that don't land on a clean percent round to
// nearest integer.
check(
  'derive 7% from $73 / $1000 (rounds 7.3 → 7)',
  deriveInitialPercent({ qty: 1, unitCents: 100000, cents: 7300 }),
  7,
)

// Out-of-range cents (e.g., legacy $700 on $1000) clamp to 50% so the
// slider does not jump past its max.
check(
  'derive clamps above 50%',
  deriveInitialPercent({ qty: 1, unitCents: 100000, cents: 70000 }),
  50,
)

if (failures > 0) {
  console.error(`\n${failures} failure(s)`)
  process.exit(1)
}
console.log('\nphase 2b slider math smoke ok')
