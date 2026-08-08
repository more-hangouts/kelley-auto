import { MenuItem, Stack, TextField, ToggleButton, ToggleButtonGroup } from '@mui/material'

import {
  LEAD_CONTEXT_OPTIONS,
  SOURCE_DETAIL_LABEL,
  SOURCE_DETAIL_PLACEHOLDER,
  WALK_IN_SOURCE_OPTIONS,
} from '../utils/leadOrigin'

/**
 * "How did they reach us, and where did they come from?" — the two origin
 * questions asked at lead capture. Shared by the admin New lead dialog and
 * the rep Add walk-in dialog so the vocabulary can never drift between the
 * two surfaces staff actually use.
 *
 * Neither field blocks submission. The picker is strongly encouraged (it is
 * the first thing in the group, and the detail field opens up as soon as a
 * bucket is chosen) but a rep mid-conversation who does not yet know should
 * be able to file the lead anyway — a guessed bucket is worse for reporting
 * than an empty one.
 *
 * The in-person / phone toggle is not cosmetic: only the in-person path
 * records an arrival, so getting it wrong logs a caller as having walked
 * through the door.
 */
export default function LeadOriginFields({ value, onPatch }) {
  const source = value.walk_in_source || ''
  const detailPlaceholder =
    SOURCE_DETAIL_PLACEHOLDER[source] || 'Anything worth remembering'

  return (
    <Stack spacing={2}>
      <ToggleButtonGroup
        exclusive
        size="small"
        color="primary"
        value={value.booking_context || 'walk_in'}
        onChange={(_e, next) => {
          if (next) onPatch({ booking_context: next })
        }}
        aria-label="How did they reach us?"
      >
        {LEAD_CONTEXT_OPTIONS.map((opt) => (
          <ToggleButton key={opt.value} value={opt.value} sx={{ px: 2 }}>
            {opt.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          select
          fullWidth
          size="small"
          label="Where did they come from?"
          value={source}
          onChange={(e) => {
            const next = e.target.value
            onPatch(
              // Clearing the bucket clears its detail too — the server
              // drops a detail with no bucket, and leaving stale text in
              // the box would imply it was saved.
              next
                ? { walk_in_source: next }
                : { walk_in_source: '', walk_in_source_detail: '' },
            )
          }}
          helperText="Ask them — this is how we know which ads are working."
        >
          <MenuItem value="">
            <em>Not sure yet</em>
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
            onChange={(e) => onPatch({ walk_in_source_detail: e.target.value })}
            placeholder={detailPlaceholder}
            inputProps={{ maxLength: 200 }}
            helperText={
              source === 'social_media'
                ? 'Which platform or post? This is the part that tells us what to run again.'
                : 'Optional.'
            }
          />
        ) : null}
      </Stack>
    </Stack>
  )
}
