import { useEffect, useMemo, useState } from 'react'
import {
  Autocomplete,
  Box,
  Checkbox,
  Collapse,
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

import LeadOriginFields from './LeadOriginFields'
import { useSearch } from '../hooks/useSearch'
import { salesSearchLeads } from '../services/api'
import {
  BUDGET_OPTIONS,
  FINANCING_OPTIONS,
  VEHICLE_TYPE_OPTIONS,
} from '../utils/walkInLeadIntake'

const MIN_SEARCH_LENGTH = 2

function splitName(label) {
  const parts = (label || '').trim().split(/\s+/).filter(Boolean)
  return {
    first_name: parts[0] || '',
    last_name: parts.length > 1 ? parts.slice(1).join(' ') : '',
  }
}

function useIntakeSearch(query, scope) {
  const adminSearch = useSearch(scope === 'admin' ? query : '')
  const trimmed = query.trim()
  const salesSearch = useQuery({
    queryKey: ['sales', 'walk-in-intake-search', trimmed],
    queryFn: ({ signal }) =>
      salesSearchLeads({ q: trimmed, limit: 5, signal }),
    enabled: scope === 'sales' && trimmed.length >= MIN_SEARCH_LENGTH,
    staleTime: 30_000,
    retry: false,
  })

  if (scope === 'sales') {
    return {
      isFetching: salesSearch.isFetching,
      data: salesSearch.data,
    }
  }
  return adminSearch
}

export default function WalkInLeadIntakeForm({
  value,
  onChange,
  searchScope = 'admin',
  creditOptions = [],
  creditLoading = false,
  creditError = false,
}) {
  const [search, setSearch] = useState('')
  const { isFetching, data } = useIntakeSearch(search, searchScope)

  useEffect(() => {
    if (!value.buyer_is_different) {
      onChange((prev) => ({
        ...prev,
        buyer_first_name: '',
        buyer_last_name: '',
      }))
    }
  }, [onChange, value.buyer_is_different])

  const options = useMemo(() => {
    const results = data?.results || []
    return results.filter((r) => r.type === 'contact')
  }, [data])

  function patch(updates) {
    onChange((prev) => ({ ...prev, ...updates }))
  }

  function pickContact(picked) {
    if (!picked || typeof picked === 'string') {
      patch({ pickedContactId: null, pickedDisplayName: '' })
      return
    }
    const guessed = splitName(picked.label)
    patch({
      pickedContactId: picked.id,
      pickedDisplayName: picked.label || '',
      first_name: value.first_name || guessed.first_name,
      last_name: value.last_name || guessed.last_name,
    })
  }

  return (
    <Stack spacing={2.25}>
      <Autocomplete
        freeSolo
        options={options}
        loading={isFetching}
        getOptionLabel={(opt) =>
          typeof opt === 'string' ? opt : opt.label || ''
        }
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
            label="Search by name or phone"
            placeholder="Start with the customer in front of you"
            size="small"
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

      <TextField
        select
        fullWidth
        size="small"
        label="Who brought them in?"
        value={value.sales_credit_user_id || ''}
        onChange={(e) => patch({ sales_credit_user_id: e.target.value })}
        disabled={creditLoading}
        helperText={
          creditError
            ? 'Could not load staff. You can save without commission credit.'
            : "For commission credit. Doesn't change who owns the lead."
        }
      >
        <MenuItem value="">
          <em>Nobody — they came in on their own</em>
        </MenuItem>
        {creditOptions.map((row) => (
          <MenuItem key={row.id} value={String(row.id)}>
            {row.full_name || row.username}
          </MenuItem>
        ))}
      </TextField>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          label="Customer first name"
          value={value.first_name}
          onChange={(e) => patch({ first_name: e.target.value })}
          size="small"
        />
        <TextField
          fullWidth
          label="Customer last name"
          value={value.last_name}
          onChange={(e) => patch({ last_name: e.target.value })}
          size="small"
        />
      </Stack>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          label="Phone"
          value={value.phone}
          onChange={(e) => patch({ phone: e.target.value })}
          size="small"
          required
        />
        <TextField
          fullWidth
          label="Email"
          type="email"
          value={value.email}
          onChange={(e) => patch({ email: e.target.value })}
          size="small"
        />
      </Stack>

      <FormControlLabel
        control={
          <Checkbox
            checked={value.buyer_is_different}
            onChange={(e) => patch({ buyer_is_different: e.target.checked })}
          />
        }
        label="Buyer is a different person"
      />

      <Collapse in={value.buyer_is_different} unmountOnExit>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
          <TextField
            fullWidth
            required={value.buyer_is_different}
            label="Buyer first name"
            value={value.buyer_first_name}
            onChange={(e) => patch({ buyer_first_name: e.target.value })}
            size="small"
          />
          <TextField
            fullWidth
            label="Buyer last name"
            value={value.buyer_last_name}
            onChange={(e) => patch({ buyer_last_name: e.target.value })}
            size="small"
          />
        </Stack>
      </Collapse>

      <LeadOriginFields value={value} onPatch={patch} />

      <TextField
        fullWidth
        label="What are they currently driving?"
        value={value.current_vehicle}
        onChange={(e) => patch({ current_vehicle: e.target.value })}
        size="small"
      />

      <Box>
        <Typography variant="caption" color="text.secondary">
          What type of vehicle are they looking for?
        </Typography>
        <ToggleButtonGroup
          exclusive
          fullWidth
          size="small"
          color="primary"
          value={value.desired_vehicle_type}
          onChange={(_e, next) => {
            if (next !== null) patch({ desired_vehicle_type: next })
          }}
          sx={{
            mt: 0.75,
            display: 'grid',
            gridTemplateColumns: {
              xs: 'repeat(2, minmax(0, 1fr))',
              sm: 'repeat(4, minmax(0, 1fr))',
            },
            '& .MuiToggleButtonGroup-grouped': {
              borderRadius: 1,
              border: 1,
              borderColor: 'divider',
              mx: 0.25,
              minHeight: 42,
              whiteSpace: 'normal',
            },
          }}
        >
          {VEHICLE_TYPE_OPTIONS.map((opt) => (
            <ToggleButton key={opt} value={opt}>
              {opt}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
        <TextField
          fullWidth
          select
          label="Investment / budget"
          value={value.budget_range}
          onChange={(e) => patch({ budget_range: e.target.value })}
          size="small"
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

        <TextField
          fullWidth
          select
          label="Financing preference"
          value={value.financing_preference}
          onChange={(e) => patch({ financing_preference: e.target.value })}
          size="small"
        >
          <MenuItem value="">
            <em>Not sure yet</em>
          </MenuItem>
          {FINANCING_OPTIONS.map((opt) => (
            <MenuItem key={opt} value={opt}>
              {opt}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      <TextField
        fullWidth
        label="Quick notes"
        value={value.notes}
        onChange={(e) => patch({ notes: e.target.value })}
        size="small"
        multiline
        minRows={2}
      />
    </Stack>
  )
}
