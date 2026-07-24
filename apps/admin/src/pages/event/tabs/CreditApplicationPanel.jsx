import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import LockOutlinedIcon from '@mui/icons-material/LockOutlined'
import VisibilityOutlinedIcon from '@mui/icons-material/VisibilityOutlined'
import VisibilityOffOutlinedIcon from '@mui/icons-material/VisibilityOffOutlined'
import { useState } from 'react'
import dayjs from 'dayjs'

import { getEventApplication } from '../../../services/api'
import { useAuth } from '../../../contexts/AuthContext'

const PII_PERMISSION = 'lead_applications:read_sensitive'

function formatDOB(raw) {
  if (!raw) return null
  const d = dayjs(raw)
  if (!d.isValid()) return raw
  // Age is the reason staff look at a DOB on a BHPH deal, so save them
  // the mental arithmetic.
  return `${d.format('MMM D, YYYY')} (age ${dayjs().diff(d, 'year')})`
}

function formatAddress(addr) {
  if (!addr || typeof addr !== 'object') return null
  const line1 = [addr.street, addr.street2].filter(Boolean).join(' ')
  const cityLine = [addr.city, addr.state].filter(Boolean).join(', ')
  const tail = [cityLine, addr.postal_code || addr.zip]
    .filter(Boolean)
    .join(' ')
  const out = [line1, tail].filter(Boolean).join('\n')
  return out || null
}

/** One labelled value with a copy button. Values are selectable text —
 *  staff routinely retype these into lender portals. */
function Field({ label, value, mono = false, onCopy }) {
  if (!value) return null
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Stack direction="row" spacing={0.5} alignItems="flex-start">
        <Typography
          variant="body2"
          sx={{
            fontWeight: 500,
            whiteSpace: 'pre-line',
            fontFamily: mono ? 'ui-monospace, monospace' : undefined,
          }}
        >
          {value}
        </Typography>
        <Tooltip title={`Copy ${label.toLowerCase()}`}>
          <Button
            size="small"
            sx={{ minWidth: 0, p: 0.25, color: 'text.secondary' }}
            onClick={() => onCopy(value, label)}
          >
            <ContentCopyOutlinedIcon sx={{ fontSize: 15 }} />
          </Button>
        </Tooltip>
      </Stack>
    </Box>
  )
}

/**
 * BHPH credit-application PII (DOB / driver's license / SSN / address) for a
 * deal, fetched from the permission-gated GET /events/{id}/application.
 *
 * Deliberately reveal-on-demand rather than auto-loading: every successful
 * read writes an `application.pii_viewed` audit row naming the viewer, so
 * fetching on mount would log a "read" for anyone who merely opened the deal
 * and make the audit trail useless as evidence of who actually looked.
 *
 * Renders nothing at all for users without the permission — an empty locked
 * box would just advertise data they cannot have and generate access
 * requests. The endpoint enforces this independently; this is only UI.
 */
export default function CreditApplicationPanel({ eventId }) {
  const { user } = useAuth()
  const [state, setState] = useState({ status: 'idle', data: null, error: '' })
  const [toast, setToast] = useState('')

  const permitted = (user?.permissions || []).includes(PII_PERMISSION)
  if (!permitted) return null

  async function handleReveal() {
    setState({ status: 'loading', data: null, error: '' })
    try {
      const data = await getEventApplication(eventId)
      setState({ status: 'shown', data, error: '' })
    } catch (err) {
      const code = err?.response?.status
      let message = 'Could not load the credit application. Try again.'
      if (code === 404) {
        message = 'No credit application on file for this deal.'
      } else if (code === 403) {
        // Their permission changed mid-session.
        message =
          'Your access to credit applications has been removed. Ask an owner if you need it back.'
      } else if (code === 401) {
        message = 'Your session expired. Sign in again to view this.'
      }
      setState({ status: 'error', data: null, error: message })
    }
  }

  function handleCopy(value, label) {
    navigator.clipboard
      ?.writeText(value)
      .then(() => setToast(`${label} copied`))
      .catch(() => setToast('Could not copy'))
  }

  const app = state.data
  const dob = formatDOB(app?.date_of_birth)
  const address = formatAddress(app?.address)
  const license = app?.driver_license_number
  const licenseLabel = app?.driver_license_state
    ? `Driver's license (${app.driver_license_state})`
    : "Driver's license"

  return (
    <Paper sx={{ p: 2.5, mb: 2 }}>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        flexWrap="wrap"
        gap={1}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <LockOutlinedIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ fontWeight: 600 }}
          >
            Credit Application
          </Typography>
          {app?.has_driver_license === false && state.status === 'shown' && (
            <Chip
              label="No driver's license"
              size="small"
              color="warning"
              variant="outlined"
            />
          )}
        </Stack>

        {state.status === 'shown' ? (
          <Button
            size="small"
            startIcon={<VisibilityOffOutlinedIcon />}
            onClick={() => setState({ status: 'idle', data: null, error: '' })}
          >
            Hide
          </Button>
        ) : (
          <Button
            size="small"
            variant="outlined"
            startIcon={
              state.status === 'loading' ? (
                <CircularProgress size={14} />
              ) : (
                <VisibilityOutlinedIcon />
              )
            }
            disabled={state.status === 'loading'}
            onClick={handleReveal}
          >
            {state.status === 'error' ? 'Try again' : 'Reveal'}
          </Button>
        )}
      </Stack>

      <Box mt={1.5}>
        {state.status === 'idle' && (
          <Typography variant="body2" color="text.secondary">
            Date of birth, driver&apos;s license, and address are encrypted.
            Revealing them is recorded in the activity log under your name.
          </Typography>
        )}

        {state.status === 'error' && (
          <Alert severity={state.error.startsWith('No credit') ? 'info' : 'warning'}>
            {state.error}
          </Alert>
        )}

        {state.status === 'shown' && app && (
          <Stack spacing={1.5}>
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={{ xs: 1.5, sm: 4 }}
              flexWrap="wrap"
              useFlexGap
            >
              <Field label="Date of birth" value={dob} onCopy={handleCopy} />
              <Field
                label={licenseLabel}
                value={license}
                mono
                onCopy={handleCopy}
              />
              <Field label="SSN" value={app.ssn} mono onCopy={handleCopy} />
            </Stack>
            <Field label="Home address" value={address} onCopy={handleCopy} />
            {!dob && !license && !app.ssn && !address && (
              <Typography variant="body2" color="text.secondary">
                An application exists for this deal but carries no stored
                details.
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary">
              This view was logged
              {app.updated_at
                ? ` · application last updated ${dayjs(app.updated_at).format('MMM D, YYYY h:mm A')}`
                : ''}
            </Typography>
          </Stack>
        )}
      </Box>

      <Snackbar
        open={!!toast}
        autoHideDuration={2000}
        onClose={() => setToast('')}
        message={toast}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      />
    </Paper>
  )
}
