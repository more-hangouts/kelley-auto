import { Box, Chip, Paper, Stack, Typography } from '@mui/material'
import DirectionsCarFilledOutlinedIcon from '@mui/icons-material/DirectionsCarFilledOutlined'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { getEventJourney } from '../../../services/api'

// Human labels for the raw storefront event names.
const EVENT_LABELS = {
  page_view: 'Viewed page',
  vehicle_view: 'Viewed vehicle',
  lead_form_opened: 'Opened lead form',
  lead_form_started: 'Started lead form',
  lead_submitted: 'Submitted lead',
}

function vehicleLabel(v) {
  const ymm = [v.vehicle_year, v.vehicle_make, v.vehicle_model]
    .filter(Boolean)
    .join(' ')
  return ymm || v.listing_code || `Vehicle #${v.vehicle_catalog_item_id ?? '?'}`
}

function SourceChips({ source }) {
  const utm = source?.utm || {}
  const chips = []
  if (utm.source) chips.push(`source: ${utm.source}`)
  if (utm.medium) chips.push(`medium: ${utm.medium}`)
  if (utm.campaign) chips.push(`campaign: ${utm.campaign}`)
  if (!chips.length && source?.referrer) chips.push('organic / referral')
  if (!chips.length) chips.push('direct / unknown')
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {chips.map((c) => (
        <Chip key={c} label={c} size="small" variant="outlined" />
      ))}
    </Stack>
  )
}

/**
 * Read-only first-party browsing journey for a deal: where the shopper came
 * from, which vehicles they viewed, and their path to conversion. Sourced from
 * GET /events/{id}/journey. Contains NO encrypted BHPH application data.
 *
 * Only meaningful for storefront (vehicle_sale) leads — the parent gates on
 * event_type, and the API returns has_attribution=false for anything else.
 */
export default function LeadJourneyPanel({ eventId }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['event', eventId, 'journey'],
    queryFn: () => getEventJourney(eventId),
    enabled: !!eventId,
  })

  const title = (
    <Typography
      variant="overline"
      color="text.secondary"
      sx={{ fontWeight: 600 }}
    >
      Lead Journey
    </Typography>
  )

  if (isLoading) {
    return (
      <Paper sx={{ p: 2.5, mb: 2 }}>
        {title}
        <Typography variant="body2" color="text.secondary" mt={1}>
          Loading…
        </Typography>
      </Paper>
    )
  }

  if (isError || !data?.has_attribution) {
    return (
      <Paper sx={{ p: 2.5, mb: 2 }}>
        {title}
        <Typography variant="body2" color="text.secondary" mt={1}>
          No storefront browsing journey recorded for this lead.
        </Typography>
      </Paper>
    )
  }

  const { source, session, vehicles_viewed, path, minutes_to_convert, event_count } =
    data

  return (
    <Paper sx={{ p: 2.5, mb: 2 }}>
      {title}

      <Box mt={1.5}>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
          Source
        </Typography>
        <Box mt={0.5}>
          <SourceChips source={source} />
        </Box>
        {source?.landing_page && (
          <Typography variant="caption" color="text.secondary" display="block" mt={0.75}>
            Landing: {source.landing_page}
          </Typography>
        )}
        {source?.referrer && (
          <Typography variant="caption" color="text.secondary" display="block">
            Referrer: {source.referrer}
          </Typography>
        )}
      </Box>

      <Stack direction="row" spacing={2} mt={1.5} flexWrap="wrap" useFlexGap>
        {minutes_to_convert != null && (
          <Chip
            size="small"
            color="success"
            variant="outlined"
            label={`Converted after ${minutes_to_convert} min`}
          />
        )}
        <Chip size="small" variant="outlined" label={`${event_count} events`} />
        {vehicles_viewed?.length > 0 && (
          <Chip
            size="small"
            variant="outlined"
            icon={<DirectionsCarFilledOutlinedIcon fontSize="inherit" />}
            label={`${vehicles_viewed.length} vehicle${
              vehicles_viewed.length === 1 ? '' : 's'
            } viewed`}
          />
        )}
      </Stack>

      {vehicles_viewed?.length > 0 && (
        <Box mt={1.5}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
            Vehicles viewed
          </Typography>
          <Stack spacing={0.25} mt={0.5}>
            {vehicles_viewed.map((v, i) => (
              <Typography key={`${v.listing_code}-${i}`} variant="body2">
                • {vehicleLabel(v)}
                {v.listing_code ? (
                  <Typography component="span" variant="caption" color="text.secondary">
                    {'  '}
                    {v.listing_code}
                  </Typography>
                ) : null}
              </Typography>
            ))}
          </Stack>
        </Box>
      )}

      {path?.length > 0 && (
        <Box mt={1.5}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
            Path to conversion
          </Typography>
          <Stack spacing={0.25} mt={0.5}>
            {path.map((p, i) => (
              <Stack
                key={i}
                direction="row"
                spacing={1}
                sx={{ alignItems: 'baseline' }}
              >
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ minWidth: 130 }}
                >
                  {p.occurred_at ? dayjs(p.occurred_at).format('MMM D, h:mm A') : '—'}
                </Typography>
                <Typography variant="body2" sx={{ flex: 1 }}>
                  {EVENT_LABELS[p.event_name] || p.event_name}
                  {p.path ? (
                    <Typography
                      component="span"
                      variant="caption"
                      color="text.secondary"
                    >
                      {'  '}
                      {p.path}
                    </Typography>
                  ) : null}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}

      {session?.user_agent && (
        <Typography variant="caption" color="text.secondary" display="block" mt={1.5}>
          Device: {session.user_agent}
        </Typography>
      )}
    </Paper>
  )
}
