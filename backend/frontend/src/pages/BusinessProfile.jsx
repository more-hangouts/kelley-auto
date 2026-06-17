import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Divider,
  FormControlLabel,
  FormHelperText,
  IconButton,
  InputAdornment,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import SettingsPageHeader from '../components/SettingsPageHeader'

import {
  deleteBusinessLogo,
  fetchBusinessLogoBlob,
  getBusinessProfile,
  updateBusinessProfile,
  uploadBusinessLogo,
} from '../services/api'
import TaxRateInput from '../components/TaxRateInput'

export default function BusinessProfile() {
  const queryClient = useQueryClient()
  const fileInputRef = useRef(null)

  const profileQuery = useQuery({
    queryKey: ['business-profile'],
    queryFn: getBusinessProfile,
  })

  const [form, setForm] = useState(null)
  const [errorMsg, setErrorMsg] = useState(null)
  const [logoUrl, setLogoUrl] = useState(null)

  // Fetch the logo via Axios so the bearer token rides along; turn the
  // blob into an object URL the <Avatar src> can render. Re-runs whenever
  // has_logo or updated_at changes (i.e. after upload/delete).
  useEffect(() => {
    const hasLogo = !!profileQuery.data?.has_logo
    if (!hasLogo) {
      setLogoUrl(null)
      return undefined
    }
    let cancelled = false
    let url = null
    fetchBusinessLogoBlob()
      .then((blob) => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setLogoUrl(url)
      })
      .catch(() => {
        if (!cancelled) setLogoUrl(null)
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [profileQuery.data?.has_logo, profileQuery.data?.updated_at])

  useEffect(() => {
    if (profileQuery.data) {
      setForm({
        legal_name: profileQuery.data.legal_name || '',
        display_name: profileQuery.data.display_name || '',
        address_line1: profileQuery.data.address_line1 || '',
        address_line2: profileQuery.data.address_line2 || '',
        city: profileQuery.data.city || '',
        state: profileQuery.data.state || '',
        postal_code: profileQuery.data.postal_code || '',
        country: profileQuery.data.country || 'US',
        phone: profileQuery.data.phone || '',
        email: profileQuery.data.email || '',
        website: profileQuery.data.website || '',
        default_tax_name: profileQuery.data.default_tax_name || '',
        default_invoice_terms: profileQuery.data.default_invoice_terms || '',
        default_invoice_footer: profileQuery.data.default_invoice_footer || '',
        default_payment_instructions: profileQuery.data.default_payment_instructions || '',
        default_tax_rate: profileQuery.data.default_tax_rate,
        discount_presets: (profileQuery.data.discount_presets || []).map((p) => ({
          id: p.id || '',
          label: p.label || '',
          percent: p.percent != null ? String(p.percent) : '0',
          active: p.active !== false,
        })),
        default_payment_plan_count:
          profileQuery.data.default_payment_plan_count == null
            ? ''
            : String(profileQuery.data.default_payment_plan_count),
        default_deposit_percent:
          profileQuery.data.default_deposit_percent == null
            ? ''
            : String(profileQuery.data.default_deposit_percent),
        attendance_gate_enabled:
          profileQuery.data.attendance_gate_enabled !== false,
        selfie_policy: profileQuery.data.selfie_policy || 'optional',
        selfie_retention_days:
          profileQuery.data.selfie_retention_days == null
            ? ''
            : String(profileQuery.data.selfie_retention_days),
        biweekly_anchor_date: profileQuery.data.biweekly_anchor_date || '',
        target_labor_pct:
          profileQuery.data.target_labor_pct == null
            ? ''
            : String(profileQuery.data.target_labor_pct),
        gps_accuracy_buffer_max_m:
          profileQuery.data.gps_accuracy_buffer_max_m == null
            ? '50'
            : String(profileQuery.data.gps_accuracy_buffer_max_m),
        trusted_network_enabled: !!profileQuery.data.trusted_network_enabled,
        // Render the JSONB array as a newline-separated textarea so the
        // owner can edit it as plain text. Save splits on newlines.
        trusted_clock_in_ips_text: Array.isArray(
          profileQuery.data.trusted_clock_in_ips,
        )
          ? profileQuery.data.trusted_clock_in_ips.join('\n')
          : '',
      })
    }
  }, [profileQuery.data])

  const saveMutation = useMutation({
    mutationFn: () => {
      const body = { ...form }
      // Trim, send empty as null where it makes sense. Skip the
      // non-string fields (presets list, plan count, deposit percent)
      // and convert them explicitly below.
      const skip = new Set([
        'discount_presets',
        'default_payment_plan_count',
        'default_deposit_percent',
        'default_tax_rate',
        // Sales-portal settings handled explicitly below (some are
        // bool/null, some need empty-string-to-null conversion).
        'attendance_gate_enabled',
        'selfie_policy',
        'selfie_retention_days',
        'biweekly_anchor_date',
        'target_labor_pct',
        // Clock-in reliability — handled below.
        'gps_accuracy_buffer_max_m',
        'trusted_network_enabled',
        'trusted_clock_in_ips_text',
      ])
      for (const k of Object.keys(body)) {
        if (skip.has(k)) continue
        if (typeof body[k] === 'string') {
          body[k] = body[k].trim() || null
        }
      }
      // Preserve required: legal_name must be a non-empty string.
      if (!body.legal_name) {
        throw Object.assign(new Error('legal_name_required'), {
          response: { data: { detail: { code: 'legal_name_required' } } },
        })
      }

      // Discount presets: drop blank rows, send id as null when empty so
      // the server slugifies; coerce percent to a number string.
      body.discount_presets = (form.discount_presets || [])
        .filter((p) => (p.label || '').trim())
        .map((p) => ({
          id: (p.id || '').trim() || null,
          label: p.label.trim(),
          percent: p.percent === '' ? '0' : String(p.percent),
          active: !!p.active,
        }))

      body.default_payment_plan_count =
        form.default_payment_plan_count === ''
          ? null
          : Number(form.default_payment_plan_count)
      body.default_deposit_percent =
        form.default_deposit_percent === ''
          ? null
          : String(form.default_deposit_percent)

      // Sales-portal attendance settings.
      body.attendance_gate_enabled = !!form.attendance_gate_enabled
      body.selfie_policy = form.selfie_policy || 'optional'
      body.selfie_retention_days =
        form.selfie_retention_days === '' ? null : Number(form.selfie_retention_days)
      body.biweekly_anchor_date =
        form.biweekly_anchor_date === '' ? null : form.biweekly_anchor_date
      body.target_labor_pct =
        form.target_labor_pct === '' || form.target_labor_pct == null
          ? null
          : String(form.target_labor_pct)

      // Clock-in reliability settings.
      body.gps_accuracy_buffer_max_m =
        form.gps_accuracy_buffer_max_m === '' ||
        form.gps_accuracy_buffer_max_m == null
          ? 50
          : Number(form.gps_accuracy_buffer_max_m)
      body.trusted_network_enabled = !!form.trusted_network_enabled
      body.trusted_clock_in_ips = (form.trusted_clock_in_ips_text || '')
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean)

      return updateBusinessProfile(body)
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['business-profile'], data)
      setErrorMsg(null)
    },
    onError: (err) => {
      const code = err?.response?.data?.detail?.code || err.message
      const msgMap = {
        legal_name_required: 'Legal name is required.',
        invalid_country: 'Country must be a 2-letter ISO code (e.g. US).',
        invalid_tax_rate: 'Tax rate must be between 0 and 100%.',
        invalid_discount_presets: 'Discount presets are not in the expected shape.',
        too_many_discount_presets: 'You can have at most 12 discount presets.',
        invalid_discount_preset_label: 'Each preset needs a label of 60 characters or fewer.',
        invalid_discount_preset_percent: 'Preset percent must be between 0 and 50.',
        invalid_discount_preset_id: 'Preset id must use lowercase letters, digits, hyphens, or underscores.',
        duplicate_discount_preset_id: 'Two presets share the same id; pick a unique one.',
        invalid_default_payment_plan_count: 'Default plan count must be 1, 2, or 3.',
        invalid_default_deposit_percent: 'Default deposit must be between 50 and 100.',
        invalid_selfie_policy: 'Selfie policy must be required, optional, or disabled.',
        invalid_selfie_retention_days:
          'Selfie retention must be between 1 and 3650 days, or blank for "keep forever".',
        invalid_biweekly_anchor_date:
          'Biweekly anchor must be a valid date or blank.',
        invalid_target_labor_pct:
          'Target labor percent must be greater than 0 and at most 100.',
        invalid_gps_accuracy_buffer_max_m:
          'Accuracy buffer must be between 0 and 200 meters.',
        invalid_trusted_clock_in_ips:
          'Trusted IPs must be valid IPv4/IPv6 addresses or CIDR ranges, one per line.',
      }
      setErrorMsg(msgMap[code] || `Save failed (${code}).`)
    },
  })

  const uploadLogoMutation = useMutation({
    mutationFn: (file) => uploadBusinessLogo(file),
    onSuccess: (data) => queryClient.setQueryData(['business-profile'], data),
    onError: (err) => {
      const code = err?.response?.data?.detail?.code || err.message
      const msgMap = {
        unsupported_logo_type: 'Logo must be a PNG, JPG, or SVG.',
        logo_too_large: 'Logo file is over the 2 MB limit.',
        empty_file: 'Selected file is empty.',
        insufficient_storage: 'Server is low on disk space.',
      }
      setErrorMsg(msgMap[code] || `Upload failed (${code}).`)
    },
  })

  const deleteLogoMutation = useMutation({
    mutationFn: () => deleteBusinessLogo(),
    onSuccess: (data) => queryClient.setQueryData(['business-profile'], data),
  })

  if (profileQuery.isLoading || !form) {
    return (
      <Box sx={{ p: 6, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Box>
    )
  }

  const setField = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  const updatePreset = (idx, patch) => {
    const next = [...(form.discount_presets || [])]
    next[idx] = { ...next[idx], ...patch }
    setForm({ ...form, discount_presets: next })
  }
  const removePreset = (idx) => {
    const next = [...(form.discount_presets || [])]
    next.splice(idx, 1)
    setForm({ ...form, discount_presets: next })
  }
  const addPreset = () => {
    const next = [...(form.discount_presets || [])]
    if (next.length >= 12) return
    next.push({ id: '', label: '', percent: '0', active: true })
    setForm({ ...form, discount_presets: next })
  }

  return (
    <Box sx={{ maxWidth: 900, mx: 'auto' }}>
      <SettingsPageHeader
        crumbs={[
          { label: 'Settings', to: '/settings' },
          { label: 'Business profile' },
        ]}
      />
      <Card>
        <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Typography variant="h4" gutterBottom>
            Business profile
          </Typography>
          <Typography color="text.secondary" sx={{ mb: 3 }}>
            These details appear on every invoice PDF, customer portal page,
            and receipt the shop sends out.
          </Typography>

          {errorMsg && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorMsg(null)}>
              {errorMsg}
            </Alert>
          )}

          <Stack spacing={3}>
            {/* Identity */}
            <Section title="Identity">
              <TextField
                label="Legal name"
                value={form.legal_name}
                onChange={setField('legal_name')}
                required
                fullWidth
              />
              <TextField
                label="Display name (optional)"
                value={form.display_name}
                onChange={setField('display_name')}
                helperText="Shows on PDF header. Defaults to legal name when blank."
                fullWidth
              />
            </Section>

            {/* Contact */}
            <Section title="Contact">
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField label="Phone" value={form.phone} onChange={setField('phone')} fullWidth />
                <TextField label="Email" type="email" value={form.email} onChange={setField('email')} fullWidth />
              </Stack>
              <TextField label="Website" value={form.website} onChange={setField('website')} fullWidth />
            </Section>

            {/* Address */}
            <Section title="Address">
              <TextField label="Address line 1" value={form.address_line1} onChange={setField('address_line1')} fullWidth />
              <TextField label="Address line 2" value={form.address_line2} onChange={setField('address_line2')} fullWidth />
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField label="City" value={form.city} onChange={setField('city')} fullWidth />
                <TextField label="State" value={form.state} onChange={setField('state')} sx={{ maxWidth: 120 }} />
                <TextField label="Postal code" value={form.postal_code} onChange={setField('postal_code')} sx={{ maxWidth: 160 }} />
                <TextField
                  label="Country"
                  value={form.country}
                  onChange={(e) => setForm({ ...form, country: e.target.value.toUpperCase() })}
                  sx={{ maxWidth: 100 }}
                  inputProps={{ maxLength: 2 }}
                />
              </Stack>
            </Section>

            {/* Tax */}
            <Section title="Default tax">
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TaxRateInput
                  label="Default tax rate"
                  value={form.default_tax_rate}
                  onChange={(rate) => setForm({ ...form, default_tax_rate: rate })}
                  size="medium"
                  sx={{ maxWidth: 200 }}
                />
                <TextField
                  label="Tax label"
                  value={form.default_tax_name}
                  onChange={setField('default_tax_name')}
                  helperText='e.g. "TX Sales".'
                  sx={{ maxWidth: 240 }}
                />
              </Stack>
            </Section>

            {/* Defaults that flow into the invoice editor */}
            <Section title="Invoice defaults">
              <Typography variant="caption" color="text.secondary">
                These pre-fill on every new invoice. Staff can override per invoice.
              </Typography>
              <TextField
                label="Default payment terms"
                multiline
                minRows={2}
                value={form.default_invoice_terms}
                onChange={setField('default_invoice_terms')}
                fullWidth
              />
              <TextField
                label="Default footer"
                multiline
                minRows={2}
                value={form.default_invoice_footer}
                onChange={setField('default_invoice_footer')}
                fullWidth
              />
              <TextField
                label="Default payment instructions"
                multiline
                minRows={2}
                value={form.default_payment_instructions}
                onChange={setField('default_payment_instructions')}
                fullWidth
                helperText="Shown on the customer portal so the family knows how to pay."
              />
            </Section>

            {/* Discount presets */}
            <Section title="Discounts">
              <Typography variant="caption" color="text.secondary">
                Presets show up in the discount dropdown on quote and invoice editors.
                Up to 12 presets, each capped at 50%.
              </Typography>
              <Stack spacing={1.5}>
                {(form.discount_presets || []).map((preset, idx) => (
                  <Stack
                    key={idx}
                    direction={{ xs: 'column', sm: 'row' }}
                    spacing={1.5}
                    alignItems={{ xs: 'stretch', sm: 'center' }}
                  >
                    <TextField
                      label="Label"
                      value={preset.label}
                      onChange={(e) => updatePreset(idx, { label: e.target.value })}
                      sx={{ flex: 1 }}
                      inputProps={{ maxLength: 60 }}
                    />
                    <TextField
                      label="Percent"
                      value={preset.percent}
                      onChange={(e) => updatePreset(idx, { percent: e.target.value })}
                      sx={{ width: { xs: '100%', sm: 140 } }}
                      InputProps={{
                        endAdornment: <InputAdornment position="end">%</InputAdornment>,
                      }}
                      inputProps={{ inputMode: 'decimal' }}
                    />
                    <FormControlLabel
                      control={
                        <Switch
                          checked={!!preset.active}
                          onChange={(e) => updatePreset(idx, { active: e.target.checked })}
                        />
                      }
                      label="Active"
                      sx={{ ml: 0 }}
                    />
                    <IconButton
                      aria-label="Remove preset"
                      onClick={() => removePreset(idx)}
                      size="small"
                    >
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </Stack>
                ))}
              </Stack>
              <Box>
                <Button
                  startIcon={<AddIcon />}
                  size="small"
                  onClick={addPreset}
                  disabled={(form.discount_presets || []).length >= 12}
                >
                  Add preset
                </Button>
              </Box>
            </Section>

            {/* Payment plan defaults */}
            <Section title="Payment plan defaults">
              <Typography variant="caption" color="text.secondary">
                Pre-fills the plan selector on new quotes and invoices.
                Falls back to 2 payments / 50% deposit when blank.
              </Typography>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  select
                  label="Plan count"
                  value={form.default_payment_plan_count}
                  onChange={setField('default_payment_plan_count')}
                  sx={{ width: { xs: '100%', sm: 200 } }}
                  helperText="Number of installments by default."
                >
                  <MenuItem value="">Use system default</MenuItem>
                  <MenuItem value="1">1 payment</MenuItem>
                  <MenuItem value="2">2 payments</MenuItem>
                  <MenuItem value="3">3 payments</MenuItem>
                </TextField>
                <TextField
                  label="Deposit percent"
                  value={form.default_deposit_percent}
                  onChange={setField('default_deposit_percent')}
                  sx={{ width: { xs: '100%', sm: 200 } }}
                  InputProps={{
                    endAdornment: <InputAdornment position="end">%</InputAdornment>,
                  }}
                  inputProps={{ inputMode: 'decimal' }}
                  helperText="Minimum 50, maximum 100."
                />
              </Stack>
            </Section>

            {/* Sales staff and attendance */}
            <Section title="Sales staff and attendance">
              <Typography variant="caption" color="text.secondary">
                Set business-wide rules for the time clock, security selfies,
                and payroll reporting. (Note: Individual shift schedules will
                override these general defaults for automatic clock-outs).
              </Typography>
              <Box>
                <FormControlLabel
                  control={
                    <Switch
                      checked={!!form.attendance_gate_enabled}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          attendance_gate_enabled: e.target.checked,
                        })
                      }
                    />
                  }
                  label="Require staff to be clocked in to make changes"
                />
                <FormHelperText>
                  When active, off-the-clock stylists cannot create walk-ins,
                  edit appointments, or build quotes. They can still log in to
                  view their schedules or request time off.
                </FormHelperText>
              </Box>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  select
                  label="Selfie policy"
                  value={form.selfie_policy}
                  onChange={setField('selfie_policy')}
                  sx={{ width: { xs: '100%', sm: 220 } }}
                  helperText="Whether stylists must capture a selfie at clock-in."
                >
                  <MenuItem value="required">Required</MenuItem>
                  <MenuItem value="optional">Optional</MenuItem>
                  <MenuItem value="disabled">Disabled</MenuItem>
                </TextField>
                <TextField
                  label="Selfie retention (days)"
                  value={form.selfie_retention_days}
                  onChange={setField('selfie_retention_days')}
                  sx={{ width: { xs: '100%', sm: 220 } }}
                  inputProps={{ inputMode: 'numeric' }}
                  helperText='Blank means "keep forever". Punch metadata is preserved either way.'
                />
              </Stack>
              <TextField
                label="Biweekly pay-period anchor"
                type="date"
                value={form.biweekly_anchor_date}
                onChange={setField('biweekly_anchor_date')}
                InputLabelProps={{ shrink: true }}
                sx={{ width: { xs: '100%', sm: 240 } }}
                helperText="First day of any pay period in the running cycle. Drives 'Pay period' range and bucket=biweek on attendance reports. Blank falls back to a rolling 14-day window."
              />
              <TextField
                label="Target labor percent"
                value={form.target_labor_pct}
                onChange={setField('target_labor_pct')}
                sx={{ width: { xs: '100%', sm: 240 } }}
                InputProps={{
                  endAdornment: <InputAdornment position="end">%</InputAdornment>,
                }}
                inputProps={{ inputMode: 'decimal' }}
                helperText="Target labor cost as a share of sales. The schedule grid uses this to show the weekly sales goal next to scheduled labor cost. Blank hides the goal chip."
              />
            </Section>

            {/* Clock-in reliability */}
            <Section title="Clock-in reliability">
              <Typography variant="caption" color="text.secondary">
                Backstops for flaky phone GPS. The accuracy buffer widens
                the geofence by the smaller of (a) the phone&apos;s reported
                accuracy and (b) the cap below, so a phone reporting
                &plusmn;40m gets up to 40m of slack but a phone reporting
                &plusmn;500m does not. The trusted boutique network is a
                second path that accepts a clock-in from a known shop IP
                even if GPS rejects.
              </Typography>
              <TextField
                label="GPS accuracy buffer (meters)"
                value={form.gps_accuracy_buffer_max_m}
                onChange={setField('gps_accuracy_buffer_max_m')}
                sx={{ width: { xs: '100%', sm: 260 } }}
                inputProps={{ inputMode: 'numeric', min: 0, max: 200 }}
                helperText="0 to 200. Default 50. Set to 0 to disable the buffer entirely."
              />
              <FormControlLabel
                control={
                  <Switch
                    checked={!!form.trusted_network_enabled}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        trusted_network_enabled: e.target.checked,
                      })
                    }
                  />
                }
                label="Allow clock-in from trusted boutique network"
              />
              <TextField
                label="Trusted IPs or CIDRs (one per line)"
                value={form.trusted_clock_in_ips_text}
                onChange={setField('trusted_clock_in_ips_text')}
                multiline
                minRows={3}
                maxRows={8}
                sx={{ width: '100%', fontFamily: 'monospace' }}
                inputProps={{ style: { fontFamily: 'monospace' } }}
                helperText='Examples: "203.0.113.5" for a single IP, "198.51.100.0/24" for a range. Detection runs even when the toggle is off so you can see whether the right IP is hitting before flipping it on.'
              />
            </Section>

            {/* Logo */}
            <Section title="Logo">
              <Stack direction="row" spacing={2} alignItems="center">
                {profileQuery.data?.has_logo && logoUrl ? (
                  <Avatar
                    src={logoUrl}
                    variant="rounded"
                    sx={{ width: 96, height: 96, bgcolor: 'background.paper' }}
                  />
                ) : (
                  <Avatar
                    variant="rounded"
                    sx={{ width: 96, height: 96, bgcolor: 'grey.200', color: 'grey.500' }}
                  >
                    No logo
                  </Avatar>
                )}
                <Stack spacing={1}>
                  <Button
                    startIcon={<UploadFileIcon />}
                    variant="outlined"
                    size="small"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploadLogoMutation.isPending}
                  >
                    {profileQuery.data?.has_logo ? 'Replace logo' : 'Upload logo'}
                  </Button>
                  {profileQuery.data?.has_logo && (
                    <Button
                      startIcon={<DeleteOutlineIcon />}
                      size="small"
                      color="error"
                      onClick={() => deleteLogoMutation.mutate()}
                      disabled={deleteLogoMutation.isPending}
                    >
                      Remove logo
                    </Button>
                  )}
                  <Typography variant="caption" color="text.secondary">
                    PNG, JPG, or SVG. 2 MB max.
                  </Typography>
                </Stack>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".png,.jpg,.jpeg,.svg"
                  hidden
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) uploadLogoMutation.mutate(file)
                    e.target.value = ''
                  }}
                />
              </Stack>
            </Section>
          </Stack>

          <Divider sx={{ my: 3 }} />
          <Stack direction="row" justifyContent="flex-end">
            <Button
              variant="contained"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? 'Saving…' : 'Save changes'}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  )
}

function Section({ title, children }) {
  return (
    <Box>
      <Typography variant="overline" color="text.secondary">
        {title}
      </Typography>
      <Stack spacing={2} sx={{ mt: 1 }}>
        {children}
      </Stack>
    </Box>
  )
}
