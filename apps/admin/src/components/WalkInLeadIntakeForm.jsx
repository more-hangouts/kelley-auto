import { useMemo, useState } from 'react'
import {
  Autocomplete,
  Box,
  Checkbox,
  Collapse,
  Divider,
  FormControlLabel,
  InputAdornment,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import { useQuery } from '@tanstack/react-query'

import { useSearch } from '../hooks/useSearch'
import { salesSearchLeads } from '../services/api'
import {
  LEAD_CONTEXT_OPTIONS,
  SOURCE_DETAIL_LABEL,
  SOURCE_DETAIL_PLACEHOLDER,
  WALK_IN_SOURCE_OPTIONS,
} from '../utils/leadOrigin'
import {
  BUDGET_OPTIONS,
  FINANCING_OPTIONS,
  VEHICLE_TYPE_OPTIONS,
} from '../utils/walkInLeadIntake'

/**
 * The walk-in intake sheet, on screen.
 *
 * Kelley's reps used a printed form for years and it worked, because it was
 * linear and conversational: you read it top to bottom while the customer
 * talked. The CRM replaced it with a two-step wizard that asked for a
 * "display name (optional override)" and re-asked for the buyer's name on
 * step 2, and staff quietly went back to paper.
 *
 * So this is the paper form, in the same order, with the same numbering. The
 * rules it follows:
 *
 *   - **One screen.** No wizard, no Next button, no state that only exists
 *     between steps. Staff scroll the way they used to scan down the page.
 *   - **Nothing technical is asked.** Display-name overrides and deal names
 *     are the server's job; dedupe is a thing the server does, not a thing a
 *     rep is told about.
 *   - **Only name and phone are required.** Every numbered question can be
 *     skipped — a rep pulled away mid-conversation still saves the lead.
 *
 * Shared by the admin New lead dialog and the rep Add walk-in dialog. The
 * only difference between the two surfaces is which search endpoint they can
 * reach (`searchScope`) and who the lead gets assigned to
 * (`assigneeControl`), so the questions themselves can never drift apart.
 */

const MIN_SEARCH_LENGTH = 2

function splitName(label) {
  const parts = (label || '').trim().split(/\s+/).filter(Boolean)
  return {
    first_name: parts[0] || '',
    last_name: parts.length > 1 ? parts.slice(1).join(' ') : '',
  }
}

/**
 * Admin and sales reach different search endpoints — the rep portal has no
 * admin scope. Both hooks are always called (rules of hooks); the inactive
 * one is handed an empty query so it never fires a request.
 */
function useIntakeSearch(query, scope) {
  const adminSearch = useSearch(scope === 'admin' ? query : '')
  const trimmed = query.trim()
  const salesSearch = useQuery({
    queryKey: ['sales', 'walk-in-intake-search', trimmed],
    queryFn: ({ signal }) => salesSearchLeads({ q: trimmed, limit: 5, signal }),
    enabled: scope === 'sales' && trimmed.length >= MIN_SEARCH_LENGTH,
    staleTime: 30_000,
    retry: false,
  })
  return scope === 'sales'
    ? { isFetching: salesSearch.isFetching, data: salesSearch.data }
    : adminSearch
}

/** A numbered question, matching the printed sheet's layout. */
function Question({ number, prompt, children }) {
  return (
    <Box>
      <Stack direction="row" spacing={1.25} sx={{ mb: 1, alignItems: 'center' }}>
        <Box
          sx={{
            width: 22,
            height: 22,
            borderRadius: '50%',
            flexShrink: 0,
            display: 'grid',
            placeItems: 'center',
            bgcolor: 'action.selected',
            color: 'text.secondary',
            fontSize: 12,
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          {number}
        </Box>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {prompt}
        </Typography>
      </Stack>
      {children}
    </Box>
  )
}

/** Section heading — "The basics" / "While you're talking". */
function SectionLabel({ children }) {
  return (
    <Typography
      variant="overline"
      color="text.secondary"
      sx={{ letterSpacing: '0.08em' }}
    >
      {children}
    </Typography>
  )
}

/**
 * Pill row for a small, fixed answer set. A dropdown would open a menu over
 * the customer's name at exactly the moment the rep is looking at them; a row
 * of pills is one tap and never covers anything.
 */
function PillChoice({ value, options, onChange, ariaLabel, columns = 4 }) {
  return (
    <ToggleButtonGroup
      exclusive
      size="small"
      color="primary"
      value={value || null}
      onChange={(_e, next) => onChange(next || '')}
      aria-label={ariaLabel}
      sx={{
        display: 'grid',
        gridTemplateColumns: {
          xs: 'repeat(2, minmax(0, 1fr))',
          sm: `repeat(${columns}, minmax(0, 1fr))`,
        },
        gap: 1,
        width: '100%',
        '& .MuiToggleButtonGroup-grouped': {
          border: 1,
          borderColor: 'divider',
          borderRadius: 1.5,
          margin: 0,
          minHeight: 44,
          whiteSpace: 'normal',
          lineHeight: 1.2,
          textTransform: 'none',
          fontWeight: 600,
        },
      }}
    >
      {options.map((opt) => (
        <ToggleButton key={opt.value} value={opt.value}>
          {opt.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  )
}

export default function WalkInLeadIntakeForm({
  value,
  onChange,
  searchScope = 'admin',
  assigneeControl = null,
}) {
  const [search, setSearch] = useState('')
  const { isFetching, data } = useIntakeSearch(search, searchScope)

  const options = useMemo(
    () => (data?.results || []).filter((r) => r.type === 'contact'),
    [data],
  )

  function patch(updates) {
    onChange((prev) => ({ ...prev, ...updates }))
  }

  function pickContact(picked) {
    if (!picked || typeof picked === 'string') {
      patch({ pickedContactId: null, pickedDisplayName: '' })
      return
    }
    // The search response carries a label but no phone, so picking someone
    // fills in the name and leaves the rep to read the number back. That is
    // the confirmation step anyway — the server dedupes on phone.
    const guessed = splitName(picked.label)
    patch({
      pickedContactId: picked.contact_id ?? picked.id,
      pickedDisplayName: picked.label || '',
      first_name: value.first_name || guessed.first_name,
      last_name: value.last_name || guessed.last_name,
    })
  }

  const source = value.walk_in_source || ''
  const detailPlaceholder =
    SOURCE_DETAIL_PLACEHOLDER[source] || 'Anything worth remembering'

  return (
    <Stack spacing={2.5}>
      {/* ---- The basics ------------------------------------------------ */}
      <Stack spacing={2}>
        <SectionLabel>The basics</SectionLabel>

        {/* Not a question for the customer — it decides whether the system
            records an arrival, so it sits above the sheet rather than in it. */}
        <PillChoice
          value={value.booking_context || 'walk_in'}
          options={LEAD_CONTEXT_OPTIONS}
          onChange={(next) => {
            // Never let this land on empty: every lead is one or the other.
            if (next) patch({ booking_context: next })
          }}
          ariaLabel="Walk-in or phone call"
          columns={2}
        />

        {assigneeControl}

        <Autocomplete
          freeSolo
          options={options}
          loading={isFetching}
          getOptionLabel={(opt) => (typeof opt === 'string' ? opt : opt.label || '')}
          filterOptions={(x) => x}
          onInputChange={(_, next) => setSearch(next || '')}
          onChange={(_, picked) => pickContact(picked)}
          renderOption={(props, opt) => (
            <Box component="li" {...props} key={`${opt.type}:${opt.id}`}>
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 650 }}>
                  {opt.label}
                </Typography>
                {opt.sublabel && (
                  <Typography variant="caption" color="text.secondary">
                    {opt.sublabel}
                  </Typography>
                )}
              </Box>
            </Box>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              size="small"
              label="Have they been in before?"
              placeholder="Search by name or phone"
              InputProps={{
                ...params.InputProps,
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />
          )}
        />

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
          <TextField
            fullWidth
            required
            size="small"
            label="First name"
            value={value.first_name}
            onChange={(e) => patch({ first_name: e.target.value })}
          />
          <TextField
            fullWidth
            size="small"
            label="Last name"
            value={value.last_name}
            onChange={(e) => patch({ last_name: e.target.value })}
          />
        </Stack>

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
          <TextField
            fullWidth
            required
            size="small"
            label="Phone"
            placeholder="(210) 555-0142"
            value={value.phone}
            onChange={(e) => patch({ phone: e.target.value })}
          />
          <TextField
            fullWidth
            size="small"
            type="email"
            label="Email (optional)"
            value={value.email}
            onChange={(e) => patch({ email: e.target.value })}
          />
        </Stack>

        <Box>
          <FormControlLabel
            control={
              <Checkbox
                size="small"
                checked={value.buyer_is_different}
                onChange={(e) => {
                  // Clear the buyer fields on the way out so an unticked box
                  // can never leave a stale name behind in the payload.
                  patch(
                    e.target.checked
                      ? { buyer_is_different: true }
                      : {
                          buyer_is_different: false,
                          buyer_first_name: '',
                          buyer_last_name: '',
                        },
                  )
                }}
              />
            }
            label={
              <Typography variant="body2">
                Someone else is buying the vehicle
              </Typography>
            }
          />
          <Collapse in={value.buyer_is_different} unmountOnExit>
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              sx={{ mt: 1 }}
            >
              <TextField
                fullWidth
                size="small"
                required
                label="Buyer first name"
                value={value.buyer_first_name}
                onChange={(e) => patch({ buyer_first_name: e.target.value })}
              />
              <TextField
                fullWidth
                size="small"
                label="Buyer last name"
                value={value.buyer_last_name}
                onChange={(e) => patch({ buyer_last_name: e.target.value })}
              />
            </Stack>
          </Collapse>
        </Box>
      </Stack>

      <Divider />

      {/* ---- The questionnaire, in the printed sheet's order ------------ */}
      <Stack spacing={2.75}>
        <SectionLabel>While you’re talking</SectionLabel>

        <Question number={1} prompt="How did you hear about us?">
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
            <TextField
              select
              fullWidth
              size="small"
              value={source}
              onChange={(e) => {
                const next = e.target.value
                // Clearing the bucket clears its detail too — the server
                // drops a detail with no bucket, and leaving stale text in
                // the box would imply it was saved.
                patch(
                  next
                    ? { walk_in_source: next }
                    : { walk_in_source: '', walk_in_source_detail: '' },
                )
              }}
            >
              <MenuItem value="">
                <em>They didn’t say</em>
              </MenuItem>
              {WALK_IN_SOURCE_OPTIONS.map((opt) => (
                <MenuItem key={opt.value} value={opt.value}>
                  {opt.label}
                </MenuItem>
              ))}
            </TextField>

            {source ? (
              <TextField
                fullWidth
                size="small"
                label={SOURCE_DETAIL_LABEL}
                value={value.walk_in_source_detail || ''}
                onChange={(e) =>
                  patch({ walk_in_source_detail: e.target.value })
                }
                placeholder={detailPlaceholder}
                inputProps={{ maxLength: 200 }}
              />
            ) : null}
          </Stack>
        </Question>

        <Question number={2} prompt="What are you currently driving?">
          <TextField
            fullWidth
            size="small"
            placeholder="2014 Nissan Altima, 180k miles"
            value={value.current_vehicle}
            onChange={(e) => patch({ current_vehicle: e.target.value })}
            inputProps={{ maxLength: 120 }}
          />
        </Question>

        <Question number={3} prompt="What are you looking to buy?">
          <PillChoice
            value={value.desired_vehicle_type}
            options={VEHICLE_TYPE_OPTIONS}
            onChange={(next) => patch({ desired_vehicle_type: next })}
            ariaLabel="Vehicle type they want"
          />
        </Question>

        <Question number={4} prompt="How much are you planning to put down today?">
          <TextField
            select
            fullWidth
            size="small"
            value={value.budget_range}
            onChange={(e) => patch({ budget_range: e.target.value })}
          >
            <MenuItem value="">
              <em>Not sure yet</em>
            </MenuItem>
            {BUDGET_OPTIONS.map((opt) => (
              <MenuItem key={opt} value={opt}>
                {opt}
              </MenuItem>
            ))}
          </TextField>
        </Question>

        <Question number={5} prompt="How are you looking to pay for it?">
          <PillChoice
            value={value.financing_preference}
            options={FINANCING_OPTIONS}
            onChange={(next) => patch({ financing_preference: next })}
            ariaLabel="Financing preference"
            columns={3}
          />
        </Question>

        <TextField
          fullWidth
          multiline
          minRows={2}
          size="small"
          label="Anything else worth remembering"
          placeholder="Coming back Saturday with his wife…"
          value={value.notes}
          onChange={(e) => patch({ notes: e.target.value })}
        />
      </Stack>
    </Stack>
  )
}
