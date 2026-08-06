import { Box, Chip, Collapse, Link, Paper, Stack, Typography } from '@mui/material'
import DirectionsCarFilledOutlinedIcon from '@mui/icons-material/DirectionsCarFilledOutlined'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
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

// A path step's detail: the car itself when the step is on a vehicle,
// otherwise the page path. Never the raw KAP stock code.
function stepDetail(p) {
  return p.vehicle_label || p.path || ''
}

function Fact({ label, value }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      {typeof value === 'string' || typeof value === 'number' ? (
        <Typography variant="body2" sx={{ fontWeight: 500 }}>
          {value}
        </Typography>
      ) : (
        <Box mt={0.25}>{value}</Box>
      )}
    </Box>
  )
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
  const [showPath, setShowPath] = useState(false)
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
      Website activity
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

  const {
    source,
    session,
    vehicles_viewed,
    path,
    event_count,
    visits = [],
    top_interests = [],
    first_seen_at,
    converted_at,
  } = data

  // Days they actually came back, not raw event count. "138 events" reads
  // like surveillance; "came back on 7 days" is the same fact, useful.
  const returnDays = new Set(
    visits.map((v) => dayjs(v.started_at).format('YYYY-MM-DD')),
  ).size

  return (
    <Paper sx={{ p: 2.5, mb: 2 }}>
      {title}

      {/* The whole story in one line, before any detail. */}
      <Stack direction="row" spacing={3} mt={1.5} flexWrap="wrap" useFlexGap>
        {first_seen_at && (
          <Fact label="First seen" value={dayjs(first_seen_at).format('MMM D')} />
        )}
        {converted_at && (
          <Fact label="Became a lead" value={dayjs(converted_at).format('MMM D')} />
        )}
        {visits.length > 0 && (
          <Fact
            label="Came back"
            value={`${returnDays} day${returnDays === 1 ? '' : 's'}`}
          />
        )}
        {vehicles_viewed?.length > 0 && (
          <Fact label="Vehicles viewed" value={vehicles_viewed.length} />
        )}
        <Fact label="Source" value={<SourceChips source={source} />} />
      </Stack>

      {top_interests.length > 0 && (
        <Box mt={2}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
            Kept coming back to
          </Typography>
          <Stack direction="row" spacing={0.75} mt={0.5} flexWrap="wrap" useFlexGap>
            {top_interests.map((t) => (
              <Chip
                key={t.label}
                size="small"
                color="primary"
                variant="outlined"
                icon={<DirectionsCarFilledOutlinedIcon fontSize="inherit" />}
                label={
                  t.visits > 1 ? `${t.label} · ${t.visits} visits` : t.label
                }
              />
            ))}
          </Stack>
        </Box>
      )}

      {/* One line per visit, not one per beacon event. Within a visit the
          same car is listed once — the beacon fires page_view AND
          vehicle_view for a single listing open, which made the raw list
          look twice as busy as the shopper actually was. */}
      {visits.length > 0 && (
        <Box mt={2}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
            Visits
          </Typography>
          <Stack spacing={1} mt={0.75}>
            {[...visits].reverse().map((v, i) => (
              <Box key={i}>
                <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap">
                  <Typography variant="body2" sx={{ fontWeight: 600, minWidth: 128 }}>
                    {dayjs(v.started_at).format('MMM D, h:mm A')}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
                    {v.vehicles.length
                      ? `Viewed ${v.vehicles.join(', ')}`
                      : 'Browsed the site'}
                    {v.converted ? ', then submitted the lead.' : ''}
                  </Typography>
                  {v.converted && (
                    <Chip size="small" color="success" label="Became a lead" />
                  )}
                </Stack>
              </Box>
            ))}
          </Stack>
        </Box>
      )}

      {path?.length > 0 && (
        <Box mt={2}>
          {/* Every beacon hit. Useful when something looks wrong with
              tracking; noise for a salesperson, so it stays closed. */}
          <Link
            component="button"
            type="button"
            underline="none"
            onClick={() => setShowPath((v) => !v)}
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 0.25,
              fontSize: 12,
              fontWeight: 500,
              color: 'text.secondary',
            }}
          >
            {showPath ? (
              <KeyboardArrowDownIcon sx={{ fontSize: 16 }} />
            ) : (
              <KeyboardArrowRightIcon sx={{ fontSize: 16 }} />
            )}
            Show technical details ({event_count} tracked events)
          </Link>
          <Collapse in={showPath} unmountOnExit>
            {source?.landing_page && (
              <Typography variant="caption" color="text.secondary" display="block" mt={1}>
                Landing: {source.landing_page}
              </Typography>
            )}
            {source?.referrer && (
              <Typography variant="caption" color="text.secondary" display="block">
                Referrer: {source.referrer}
              </Typography>
            )}
            <Stack spacing={0.25} mt={0.75}>
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
                    {stepDetail(p) ? (
                      <Typography
                        component="span"
                        variant="caption"
                        color="text.secondary"
                      >
                        {'  '}
                        {stepDetail(p)}
                      </Typography>
                    ) : null}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </Collapse>
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
