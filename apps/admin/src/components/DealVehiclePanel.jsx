import { useMemo, useState } from 'react'
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import DirectionsCarOutlinedIcon from '@mui/icons-material/DirectionsCarOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { listVehicles, patchEventVehicles } from '../services/api'

// The two links are different questions, and the copy has to say so — the
// whole reason for the second column is that staff were overwriting the first.
const FIELDS = [
  {
    key: 'vehicle_catalog_item_id',
    refKey: 'vehicle',
    label: 'Asking about',
    help: 'The car this lead came in on. Changing it rewrites where the lead came from — only fix it if it was wrong.',
  },
  {
    key: 'sold_vehicle_catalog_item_id',
    refKey: 'sold_vehicle',
    label: 'Bought',
    help: 'Set this only when they bought a different car than they asked about. It is what gets marked sold in inventory.',
  },
]

function vehicleLabel(v) {
  if (!v) return ''
  const name = [v.year, v.make, v.model, v.trim].filter(Boolean).join(' ')
  const tail = [v.stock_number, v.vehicle_status].filter(Boolean).join(' · ')
  return tail ? `${name || `#${v.id}`} — ${tail}` : name || `#${v.id}`
}

// The saved value comes back as a DealVehicleRef (already-joined label), while
// options come from the catalog list (raw columns). Normalize so Autocomplete
// compares and renders one shape.
function refToOption(ref) {
  if (!ref) return null
  return {
    id: ref.id,
    label: ref.label,
    stock_number: ref.stock_number,
    vehicle_status: ref.vehicle_status,
  }
}

function optionLabel(o) {
  if (!o) return ''
  // A DealVehicleRef arrives with `label` pre-joined; a catalog row does not.
  if (o.label) {
    const tail = [o.stock_number, o.vehicle_status].filter(Boolean).join(' · ')
    return tail ? `${o.label} — ${tail}` : o.label
  }
  return vehicleLabel(o)
}

export default function DealVehiclePanel({ event }) {
  const queryClient = useQueryClient()
  const [error, setError] = useState(null)
  const [drafts, setDrafts] = useState({})

  // Inactive rows included on purpose: a car goes inactive once it leaves the
  // lot, and recording which car an older deal closed on is exactly when you
  // need to pick one.
  const { data: vehicles = [], isLoading } = useQuery({
    queryKey: ['vehicles', 'picker'],
    queryFn: () => listVehicles({ includeInactive: true, limit: 500 }),
    staleTime: 5 * 60 * 1000,
  })

  const options = useMemo(
    () =>
      vehicles.map((v) => ({
        id: v.id,
        label: [v.year, v.make, v.model, v.trim].filter(Boolean).join(' ') || null,
        stock_number: v.stock_number,
        vehicle_status: v.vehicle_status,
      })),
    [vehicles],
  )

  const mutation = useMutation({
    mutationFn: ({ key, value }) => patchEventVehicles(event.id, { [key]: value }),
    onSuccess: (_data, { key }) => {
      setError(null)
      setDrafts((d) => {
        const next = { ...d }
        delete next[key]
        return next
      })
      queryClient.invalidateQueries({ queryKey: ['event', event.id] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      queryClient.invalidateQueries({ queryKey: ['events', 'follow-ups'] })
      // The sold link drives inventory status, so the vehicle lists are stale
      // the moment it changes on a closed deal.
      queryClient.invalidateQueries({ queryKey: ['vehicles'] })
    },
    onError: (err) =>
      setError(
        err?.response?.data?.detail === 'not_a_vehicle'
          ? 'That catalog item is not a vehicle.'
          : err?.response?.data?.detail || err.message || 'Could not save.',
      ),
  })

  return (
    <Box>
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Stack spacing={2.5}>
        {FIELDS.map((f) => {
          const saved = refToOption(event[f.refKey])
          const dirty = Object.prototype.hasOwnProperty.call(drafts, f.key)
          const value = dirty ? drafts[f.key] : saved
          const savedId = saved?.id ?? null
          const valueId = value?.id ?? null
          const pending = mutation.isPending && mutation.variables?.key === f.key

          return (
            <Box key={f.key}>
              <Autocomplete
                options={options}
                loading={isLoading}
                value={value}
                onChange={(_e, next) =>
                  setDrafts((d) => ({ ...d, [f.key]: next }))
                }
                getOptionLabel={optionLabel}
                isOptionEqualToValue={(o, v) => o.id === v?.id}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={f.label}
                    size="small"
                    placeholder="No vehicle linked"
                    InputProps={{
                      ...params.InputProps,
                      endAdornment: (
                        <>
                          {isLoading ? <CircularProgress size={16} /> : null}
                          {params.InputProps.endAdornment}
                        </>
                      ),
                    }}
                  />
                )}
              />
              <Stack
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                spacing={1}
                sx={{ mt: 0.5 }}
              >
                <Typography variant="caption" color="text.secondary">
                  {f.help}
                </Typography>
                {valueId !== savedId && (
                  <Button
                    size="small"
                    variant="contained"
                    disabled={pending}
                    onClick={() =>
                      mutation.mutate({ key: f.key, value: valueId })
                    }
                  >
                    {pending ? 'Saving…' : 'Save'}
                  </Button>
                )}
              </Stack>
            </Box>
          )
        })}
      </Stack>

      {/* Says out loud what NULL means, so an empty "Bought" field is never
          read as "we don't know what they bought". */}
      {event.vehicle && !event.sold_vehicle && (
        <Chip
          icon={<DirectionsCarOutlinedIcon />}
          size="small"
          variant="outlined"
          sx={{ mt: 2 }}
          label="Closing this deal marks the car above sold"
        />
      )}
    </Box>
  )
}
