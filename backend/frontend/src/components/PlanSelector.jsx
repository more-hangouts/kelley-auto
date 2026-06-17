import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Box,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import dayjs from 'dayjs'

import CurrencyInput from './CurrencyInput'
import { formatUSD } from '../utils/money'

// Phase 5 of the discount/payment-term refactor. Replaces the free-form
// installment editor on both QuoteEditor and InvoiceEditor with a
// constrained 1/2/3-payment selector that auto-generates amounts and
// anchors due dates around the issue and event dates. The "Custom
// amounts" toggle is the escape hatch when the standard plan does not
// fit a customer.

function bankRound(value) {
  // Banker's rounding to integer. Mirrors the backend's
  // `Decimal.ROUND_HALF_EVEN` and the existing helpers in
  // InvoiceEditor / QuoteEditor.
  const sign = value < 0 ? -1 : 1
  const abs = Math.abs(value)
  const floor = Math.floor(abs)
  const diff = abs - floor
  if (diff > 0.5 || (diff === 0.5 && floor % 2 === 1)) {
    return sign * (floor + 1)
  }
  return sign * floor
}

// Anchored dates per the Phase 5 spec:
// - deposit due  = issue + 14d
// - final due    = max(event - 60d, issue + 28d) when event date exists,
//                  otherwise issue + 60d
// - 3-payment middle due = midpoint between deposit and final
function anchorDates(planCount, issueDate, eventDate) {
  const issue = dayjs(issueDate || dayjs().format('YYYY-MM-DD'))
  const depositDue = issue.add(14, 'day')
  const minFinalDue = depositDue.add(14, 'day')
  let finalDue
  if (eventDate) {
    const eventMinus60 = dayjs(eventDate).subtract(60, 'day')
    finalDue = eventMinus60.isBefore(minFinalDue)
      ? issue.add(60, 'day')
      : eventMinus60
  } else {
    finalDue = issue.add(60, 'day')
  }
  if (planCount <= 1) return [depositDue.format('YYYY-MM-DD')]
  if (planCount === 2) {
    return [depositDue.format('YYYY-MM-DD'), finalDue.format('YYYY-MM-DD')]
  }
  const midDay = depositDue.add(
    Math.round(finalDue.diff(depositDue, 'day') / 2),
    'day',
  )
  return [
    depositDue.format('YYYY-MM-DD'),
    midDay.format('YYYY-MM-DD'),
    finalDue.format('YYYY-MM-DD'),
  ]
}

function planLabels(count) {
  if (count === 1) return ['Payment']
  if (count === 2) return ['Deposit', 'Balance']
  return ['Deposit', 'Payment 2', 'Final']
}

// Generate the schedule rows from plan inputs. Amounts always sum to
// `totalCents` exactly — rounding crumbs land on the trailing row.
// eslint-disable-next-line react-refresh/only-export-components
export function generatePlan({
  count,
  depositPercent,
  totalCents,
  issueDate,
  eventDate,
  existingDates = [],
}) {
  if (totalCents <= 0 || count < 1 || count > 3) return []
  const dates = anchorDates(count, issueDate, eventDate)
  const labels = planLabels(count)
  const safePct = Math.max(50, Math.min(100, Number(depositPercent) || 50))
  if (count === 1) {
    return [
      {
        label: labels[0],
        amount_cents: totalCents,
        due_date: existingDates[0] || dates[0],
      },
    ]
  }
  const deposit = bankRound((totalCents * safePct) / 100)
  if (count === 2) {
    const balance = totalCents - deposit
    return [
      {
        label: labels[0],
        amount_cents: deposit,
        due_date: existingDates[0] || dates[0],
      },
      {
        label: labels[1],
        amount_cents: balance,
        due_date: existingDates[1] || dates[1],
      },
    ]
  }
  const remaining = totalCents - deposit
  const middle = bankRound(remaining / 2)
  const final = remaining - middle
  return [
    {
      label: labels[0],
      amount_cents: deposit,
      due_date: existingDates[0] || dates[0],
    },
    {
      label: labels[1],
      amount_cents: middle,
      due_date: existingDates[1] || dates[1],
    },
    {
      label: labels[2],
      amount_cents: final,
      due_date: existingDates[2] || dates[2],
    },
  ]
}

// Best-effort hydration: figure out whether existing installments fit a
// "regular" 1/2/3 plan with a clean deposit percent, or whether they
// should be surfaced in custom-amounts mode. Returns the inferred plan
// state plus a `customAmounts` flag.
function inferPlan(installments, totalCents, defaults) {
  if (!installments || installments.length === 0) {
    return {
      count: defaults.count,
      depositPercent: defaults.depositPercent,
      customAmounts: false,
    }
  }
  if (installments.length > 3) {
    return {
      count: defaults.count,
      depositPercent: defaults.depositPercent,
      customAmounts: true,
    }
  }
  if (installments.length === 1) {
    return { count: 1, depositPercent: 100, customAmounts: false }
  }
  if (totalCents <= 0) {
    return {
      count: installments.length,
      depositPercent: defaults.depositPercent,
      customAmounts: true,
    }
  }
  const first = Number(installments[0].amount_cents || 0)
  const pctRaw = (first * 100) / totalCents
  const pctRounded = Math.round(pctRaw)
  const fits =
    pctRounded >= 50 &&
    pctRounded <= 100 &&
    Math.abs(pctRaw - pctRounded) < 0.5
  if (!fits) {
    return {
      count: installments.length,
      depositPercent: defaults.depositPercent,
      customAmounts: true,
    }
  }
  // Plan-mode hydration is only safe when the stored amounts match the
  // formula at the inferred percent. Otherwise the rows will jump on
  // first render — better to stay in custom mode and let staff opt in.
  const candidate = generatePlan({
    count: installments.length,
    depositPercent: pctRounded,
    totalCents,
    issueDate: dayjs().format('YYYY-MM-DD'),
    eventDate: null,
  })
  const matches = candidate.every(
    (row, idx) =>
      Number(installments[idx].amount_cents || 0) === Number(row.amount_cents),
  )
  return {
    count: installments.length,
    depositPercent: pctRounded,
    customAmounts: !matches,
  }
}

export default function PlanSelector({
  installments,
  onInstallmentsChange,
  customAmounts,
  onCustomAmountsChange,
  totalCents,
  issueDate,
  eventDate,
  defaultPlanCount,
  defaultDepositPercent,
  disabled = false,
}) {
  const seedDefaults = useMemo(
    () => ({
      count: defaultPlanCount || 2,
      depositPercent: defaultDepositPercent || 50,
    }),
    [defaultPlanCount, defaultDepositPercent],
  )
  const inferred = useMemo(
    () => inferPlan(installments, totalCents, seedDefaults),
    // Only re-infer when the installments array reference changes (parent
    // hydrates) or when the seed defaults change. We deliberately do not
    // depend on totalCents — re-inferring on every cart edit would force
    // depositPercent back to the inferred value, throwing away staff's
    // manual changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [installments, seedDefaults],
  )

  const [planCount, setPlanCountState] = useState(inferred.count)
  const [depositPercent, setDepositPercent] = useState(
    String(inferred.depositPercent),
  )
  const lastHydratedInstallmentsRef = useRef(null)

  // Sync local UI when the parent re-hydrates a different record.
  useEffect(() => {
    setPlanCountState(inferred.count)
    setDepositPercent(String(inferred.depositPercent))
  }, [inferred.count, inferred.depositPercent])

  // When a loaded schedule does not fit the standard 1/2/3 generated
  // shape, surface it in custom-amounts mode and send the matching
  // request flag on save. Gate this by installments reference so a user
  // can intentionally toggle custom mode off for the current draft.
  useEffect(() => {
    if (lastHydratedInstallmentsRef.current === installments) return
    lastHydratedInstallmentsRef.current = installments
    if (inferred.customAmounts && !customAmounts) {
      onCustomAmountsChange(true)
    }
  }, [installments, inferred.customAmounts, customAmounts, onCustomAmountsChange])

  // Paid rows freeze plan-mode editing — auto-generation would overwrite
  // a row the customer has already paid.
  const hasPaidRow = (installments || []).some((i) => i?.paid_at)
  const lockToCustom = hasPaidRow
  const effectiveCustom = customAmounts || lockToCustom

  // Auto-regenerate amounts when totalCents / planCount / depositPct
  // change and we are in plan mode. Existing dates are preserved so a
  // staff-nudged due date does not snap back when the cart total moves.
  useEffect(() => {
    if (effectiveCustom) return
    if (totalCents <= 0) return
    const existingDates = (installments || [])
      .map((i) => i.due_date)
      .filter(Boolean)
    const next = generatePlan({
      count: planCount,
      depositPercent,
      totalCents,
      issueDate,
      eventDate,
      existingDates,
    })
    const same =
      installments &&
      next.length === installments.length &&
      next.every(
        (row, idx) =>
          Number(installments[idx].amount_cents) === Number(row.amount_cents) &&
          installments[idx].due_date === row.due_date,
      )
    if (same) return
    onInstallmentsChange(
      next.map((row, idx) => {
        const prev = installments?.[idx] || {}
        return { ...prev, ...row }
      }),
    )
    // installments / onInstallmentsChange are intentionally excluded:
    // re-running on every emit creates an infinite loop. The `same`
    // guard above handles the no-op case.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    effectiveCustom,
    planCount,
    depositPercent,
    totalCents,
    issueDate,
    eventDate,
  ])

  const onCountChange = (value) => {
    const next = Number(value)
    if (next === planCount) return
    setPlanCountState(next)
    if (effectiveCustom) {
      // In custom mode the count change has to be applied directly —
      // the auto-regenerate effect is gated off. Generate a fresh plan
      // at the current total and emit so the row count matches.
      const generated = generatePlan({
        count: next,
        depositPercent,
        totalCents,
        issueDate,
        eventDate,
      })
      onInstallmentsChange(generated)
    }
  }

  const updateRow = (idx, patch) => {
    onInstallmentsChange(
      (installments || []).map((row, i) =>
        i === idx ? { ...row, ...patch } : row,
      ),
    )
  }

  const totalAssigned = (installments || []).reduce(
    (sum, row) => sum + Number(row.amount_cents || 0),
    0,
  )
  const balanced = totalAssigned === totalCents

  return (
    <Stack spacing={1.25}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={1.5}
        alignItems={{ xs: 'stretch', sm: 'center' }}
      >
        <TextField
          select
          size="small"
          label="Payments"
          value={String(planCount)}
          onChange={(e) => onCountChange(e.target.value)}
          disabled={disabled || lockToCustom}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value="1">1 payment</MenuItem>
          <MenuItem value="2">2 payments</MenuItem>
          <MenuItem value="3">3 payments</MenuItem>
        </TextField>
        <TextField
          size="small"
          label="Deposit %"
          value={depositPercent}
          onChange={(e) => setDepositPercent(e.target.value)}
          disabled={disabled || effectiveCustom || planCount === 1}
          sx={{ width: 130 }}
          inputProps={{ inputMode: 'decimal' }}
          InputProps={{
            endAdornment: <Typography variant="body2">%</Typography>,
          }}
          helperText={planCount === 1 ? 'Single payment' : null}
        />
        <FormControlLabel
          control={
            <Switch
              size="small"
              checked={effectiveCustom}
              onChange={(e) => onCustomAmountsChange(e.target.checked)}
              disabled={disabled || lockToCustom}
            />
          }
          label="Custom amounts"
        />
      </Stack>

      {(installments || []).length > 0 && (
        <Box>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: '1fr 160px 160px',
              gap: 1,
              px: 1,
              mb: 0.5,
            }}
          >
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
              Payment
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
              Amount
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
              Due date
            </Typography>
          </Box>
          <Stack spacing={1}>
            {(installments || []).map((row, idx) => {
              const rowLocked = disabled || !!row.paid_at
              return (
                <Box
                  key={row.id ?? `tmp-${idx}`}
                  sx={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 160px 160px',
                    gap: 1,
                    alignItems: 'center',
                  }}
                >
                  <TextField
                    size="small"
                    value={row.label || ''}
                    onChange={(e) => updateRow(idx, { label: e.target.value })}
                    disabled={rowLocked}
                    placeholder="Deposit, Balance, …"
                    inputProps={{ 'aria-label': 'Payment label' }}
                  />
                  <CurrencyInput
                    value={row.amount_cents}
                    onChange={(v) => updateRow(idx, { amount_cents: v ?? 0 })}
                    disabled={rowLocked || !effectiveCustom}
                    inputProps={{ 'aria-label': 'Payment amount' }}
                  />
                  <TextField
                    size="small"
                    type="date"
                    value={row.due_date || ''}
                    onChange={(e) =>
                      updateRow(idx, { due_date: e.target.value })
                    }
                    disabled={rowLocked}
                    inputProps={{ 'aria-label': 'Payment due date' }}
                  />
                </Box>
              )
            })}
          </Stack>
          <Box
            sx={{
              mt: 1.5,
              pt: 1,
              borderTop: '1px dashed',
              borderColor: 'divider',
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 1,
            }}
          >
            <Typography
              variant="body2"
              color={balanced ? 'text.secondary' : 'warning.main'}
            >
              {formatUSD(totalAssigned)} / {formatUSD(totalCents)}
            </Typography>
          </Box>
        </Box>
      )}
    </Stack>
  )
}
