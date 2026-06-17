'use client'

import React, { useState } from 'react'
import { useField } from '@payloadcms/ui'

const MAKE_OPTIONS = [
  'Acura','Alfa Romeo','Aston Martin','Audi','Bentley','BMW','Buick','Cadillac',
  'Chevrolet','Chrysler','Dodge','Ferrari','Fiat','Ford','Genesis','GMC','Honda',
  'Hyundai','Infiniti','Jaguar','Jeep','Kia','Lamborghini','Land Rover','Lexus',
  'Lincoln','Maserati','Mazda','McLaren','Mercedes-Benz','MINI','Mitsubishi',
  'Nissan','Pontiac','Porsche','Ram','Rolls-Royce','Subaru','Tesla','Toyota',
  'Volkswagen','Volvo','Other',
]

function matchMake(apiMake: string): string {
  if (!apiMake) return 'Other'
  const upper = apiMake.toUpperCase()
  const found = MAKE_OPTIONS.find((m) => m.toUpperCase() === upper)
  if (found) return found
  const partial = MAKE_OPTIONS.find((m) => upper.includes(m.toUpperCase()) || m.toUpperCase().includes(upper))
  return partial || 'Other'
}

function mapFuelType(api: string): string | null {
  if (!api) return null
  const u = api.toLowerCase()
  if (u.includes('electric')) return 'ELECTRIC'
  if (u.includes('hybrid')) return 'HYBRID'
  if (u.includes('diesel')) return 'DIESEL'
  if (u.includes('gas') || u.includes('gasoline') || u.includes('petrol')) return 'GAS'
  return null
}

function mapTransmission(api: string): string | null {
  if (!api) return null
  const u = api.toLowerCase()
  if (u.includes('manual')) return 'MANUAL'
  if (u.includes('auto')) return 'AUTOMATIC'
  return null
}

type FieldPath = { path: string }

export function VinDecoderComponent({ path }: FieldPath) {
  const { value: vinValue, setValue: setVin } = useField<string>({ path })
  const { setValue: setMake } = useField<string>({ path: 'make' })
  const { setValue: setModel } = useField<string>({ path: 'model' })
  const { setValue: setYear } = useField<string>({ path: 'year' })
  const { setValue: setFuelType } = useField<string>({ path: 'fuelType' })
  const { setValue: setTransmission } = useField<string>({ path: 'transmission' })

  const [inputVin, setInputVin] = useState((vinValue as string) || '')
  const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [message, setMessage] = useState('')

  async function handleDecode() {
    const vin = inputVin.trim().toUpperCase()
    if (vin.length !== 17) {
      setStatus('error')
      setMessage('VIN must be exactly 17 characters.')
      return
    }

    setStatus('loading')
    setMessage('')

    try {
      const res = await fetch(
        `https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/${vin}?format=json`,
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      const r = data?.Results?.[0]
      if (!r) throw new Error('No results returned')

      const errorCode = r.ErrorCode || ''
      if (errorCode !== '0' && !r.Make) {
        throw new Error(r.ErrorText || 'Invalid VIN')
      }

      setVin(vin)

      const make = matchMake(r.Make || '')
      if (make) setMake(make)

      if (r.Model) setModel(r.Model)

      const yearStr = (r.ModelYear || '').toString()
      const yearNum = parseInt(yearStr, 10)
      if (yearNum >= 1980 && yearNum <= 2026) setYear(yearStr)

      const fuel = mapFuelType(r.FuelTypePrimary || '')
      if (fuel) setFuelType(fuel)

      const trans = mapTransmission(r.TransmissionStyle || '')
      if (trans) setTransmission(trans)

      setStatus('success')
      setMessage(`Decoded: ${r.ModelYear || ''} ${r.Make || ''} ${r.Model || ''}`.trim())
    } catch (err: unknown) {
      setStatus('error')
      setMessage(err instanceof Error ? err.message : 'Failed to decode VIN')
    }
  }

  const inputStyle: React.CSSProperties = {
    flex: 1,
    padding: '8px 12px',
    border: '1px solid var(--theme-elevation-150)',
    borderRadius: '4px',
    backgroundColor: 'var(--theme-input-bg)',
    color: 'var(--theme-text)',
    fontSize: '14px',
    fontFamily: 'monospace',
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
  }

  const btnStyle: React.CSSProperties = {
    padding: '8px 16px',
    backgroundColor: 'var(--theme-success-500)',
    color: '#fff',
    border: 'none',
    borderRadius: '4px',
    cursor: status === 'loading' ? 'not-allowed' : 'pointer',
    fontSize: '13px',
    fontWeight: 600,
    whiteSpace: 'nowrap',
    opacity: status === 'loading' ? 0.7 : 1,
  }

  return (
    <div className="field-type text" style={{ marginBottom: '1rem' }}>
      <label className="field-label" style={{ display: 'block', marginBottom: '6px', fontSize: '13px', fontWeight: 600 }}>
        VIN
      </label>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <input
          type="text"
          value={inputVin}
          onChange={(e) => {
            setInputVin(e.target.value.toUpperCase())
            if (status !== 'idle') setStatus('idle')
          }}
          placeholder="Enter 17-character VIN"
          maxLength={17}
          style={inputStyle}
        />
        <button type="button" onClick={handleDecode} disabled={status === 'loading'} style={btnStyle}>
          {status === 'loading' ? 'Decoding…' : 'Decode VIN'}
        </button>
      </div>
      {status === 'success' && (
        <p style={{ marginTop: '6px', fontSize: '13px', color: 'var(--theme-success-500)' }}>
          ✓ {message}
        </p>
      )}
      {status === 'error' && (
        <p style={{ marginTop: '6px', fontSize: '13px', color: 'var(--theme-error-500)' }}>
          ✗ {message}
        </p>
      )}
    </div>
  )
}
