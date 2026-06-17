'use client'

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useField, useFormFields } from '@payloadcms/ui'

const MODEL_MAP: Record<string, string[]> = {
  Acura: ['ILX', 'MDX', 'NSX', 'RDX', 'RLX', 'TLX'],
  'Alfa Romeo': ['Giulia', 'Giulietta', 'Stelvio'],
  'Aston Martin': [],
  Audi: ['A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'Q3', 'Q5', 'Q7', 'Q8', 'TT', 'R8', 'e-tron'],
  Bentley: [],
  BMW: [
    '1 Series', '2 Series', '3 Series', '4 Series', '5 Series', '6 Series',
    '7 Series', '8 Series', 'X1', 'X2', 'X3', 'X4', 'X5', 'X6', 'X7', 'Z4', 'M3', 'M5',
  ],
  Buick: ['Enclave', 'Encore', 'Envision', 'LaCrosse', 'Regal', 'Verano'],
  Cadillac: ['ATS', 'CT4', 'CT5', 'CT6', 'Escalade', 'SRX', 'XT4', 'XT5', 'XT6'],
  Chevrolet: [
    'Blazer', 'Camaro', 'Colorado', 'Corvette', 'Cruze', 'Equinox', 'Express',
    'Impala', 'Malibu', 'Silverado', 'Sonic', 'Spark', 'Suburban', 'Tahoe',
    'Trailblazer', 'Traverse', 'Trax',
  ],
  Chrysler: ['300', 'Pacifica', 'Voyager'],
  Dodge: ['Challenger', 'Charger', 'Durango', 'Grand Caravan', 'Hornet', 'Journey'],
  Ferrari: [],
  Fiat: [],
  Ford: [
    'Bronco', 'EcoSport', 'Edge', 'Escape', 'Expedition', 'Explorer',
    'F-150', 'F-250', 'F-350', 'Fusion', 'Maverick', 'Mustang', 'Ranger', 'Transit',
  ],
  Genesis: ['G70', 'G80', 'G90', 'GV70', 'GV80'],
  GMC: ['Acadia', 'Canyon', 'Envoy', 'Sierra', 'Terrain', 'Yukon'],
  Honda: ['Accord', 'Civic', 'CR-V', 'HR-V', 'Insight', 'Odyssey', 'Passport', 'Pilot', 'Ridgeline'],
  Hyundai: [
    'Accent', 'Elantra', 'Ioniq', 'Kona', 'Palisade', 'Santa Cruz',
    'Santa Fe', 'Sonata', 'Tucson', 'Venue',
  ],
  Infiniti: ['Q50', 'Q60', 'QX50', 'QX55', 'QX60', 'QX80'],
  Jaguar: ['E-Pace', 'F-Pace', 'F-Type', 'I-Pace', 'XE', 'XF', 'XJ'],
  Jeep: ['Cherokee', 'Compass', 'Gladiator', 'Grand Cherokee', 'Renegade', 'Wrangler'],
  Kia: ['Carnival', 'EV6', 'Forte', 'K5', 'Niro', 'Seltos', 'Sorento', 'Soul', 'Sportage', 'Stinger', 'Telluride'],
  'Land Rover': [
    'Defender', 'Discovery', 'Discovery Sport', 'Range Rover',
    'Range Rover Evoque', 'Range Rover Sport', 'Range Rover Velar',
  ],
  Lamborghini: [],
  Lexus: ['ES', 'GS', 'GX', 'IS', 'LC', 'LS', 'LX', 'NX', 'RX', 'UX'],
  Lincoln: ['Aviator', 'Corsair', 'MKZ', 'Nautilus', 'Navigator'],
  Maserati: [],
  Mazda: ['CX-3', 'CX-30', 'CX-5', 'CX-50', 'CX-9', 'Mazda3', 'Mazda6', 'MX-5 Miata'],
  McLaren: [],
  'Mercedes-Benz': [
    'A-Class', 'C-Class', 'CLA', 'CLS', 'E-Class', 'G-Class',
    'GLA', 'GLB', 'GLC', 'GLE', 'GLS', 'S-Class', 'SL', 'AMG GT', 'EQS',
  ],
  MINI: ['Clubman', 'Convertible', 'Countryman', 'Hardtop'],
  Mitsubishi: ['Eclipse Cross', 'Mirage', 'Outlander', 'Outlander Sport'],
  Nissan: [
    'Altima', 'Armada', 'Frontier', 'Kicks', 'Leaf', 'Maxima',
    'Murano', 'Pathfinder', 'Rogue', 'Sentra', 'Titan', 'Versa',
  ],
  Pontiac: ['G6', 'Grand Prix', 'Solstice', 'Vibe'],
  Porsche: ['718', '911', 'Cayenne', 'Macan', 'Panamera', 'Taycan'],
  Ram: ['1500', '2500', '3500', 'ProMaster'],
  'Rolls-Royce': [],
  Subaru: ['Ascent', 'BRZ', 'Crosstrek', 'Forester', 'Impreza', 'Legacy', 'Outback', 'WRX'],
  Tesla: ['Cybertruck', 'Model 3', 'Model S', 'Model X', 'Model Y', 'Roadster'],
  Toyota: [
    '4Runner', 'Avalon', 'Camry', 'Corolla', 'GR86', 'Highlander',
    'Land Cruiser', 'Prius', 'RAV4', 'Sequoia', 'Sienna', 'Tacoma', 'Tundra', 'Venza',
  ],
  Volkswagen: ['Atlas', 'Golf', 'ID.4', 'Jetta', 'Passat', 'Taos', 'Tiguan'],
  Volvo: ['C40', 'S60', 'S90', 'V60', 'V90', 'XC40', 'XC60', 'XC90'],
  Other: [],
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '0.5rem 0.75rem',
  border: '1px solid var(--theme-elevation-150, #ccc)',
  borderRadius: '4px',
  background: 'var(--theme-input-bg, #fff)',
  color: 'var(--theme-text, #000)',
  fontSize: '14px',
  boxSizing: 'border-box',
}

interface ModelSelectProps {
  path: string
  field?: {
    label?: string | false | null
    name?: string
    required?: boolean
  }
  readOnly?: boolean
}

// Stable selector defined outside the component so its reference never changes.
// An inline arrow function recreated on every render is the primary cause of
// infinite loops with useFormFields.
// Selector defined with explicit any to avoid TypeScript index-signature errors.
// Stable reference (not recreated per render) is what prevents the infinite loop.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const selectMake = (state: any): string | undefined => state[0]?.['make']?.value as string | undefined

export const ModelSelectComponent: React.FC<ModelSelectProps> = ({ path, field, readOnly }) => {
  const resolvedPath = path || field?.name || 'model'
  const { value, setValue } = useField<string>({ path: resolvedPath })

  // Memoized selector — stable reference prevents re-render cascade.
  // useCallback with [] is equivalent to the module-level selector above,
  // but useCallback makes the intent explicit inside the component.
  const makeValue = useFormFields(useCallback(selectMake, []))

  const models = makeValue ? (MODEL_MAP[makeValue] ?? []) : []
  const hasModels = models.length > 0

  // Derive whether the current saved value is a free-text (custom) entry.
  // Computed once after mount and updated only when make changes.
  const isCustom = !!value && hasModels && !models.includes(value)
  const [showCustomInput, setShowCustomInput] = useState(isCustom)

  // Track previous make so we only call setShowCustomInput when it actually changes,
  // avoiding any spurious setState calls on re-renders where make is the same.
  const prevMakeRef = useRef(makeValue)
  useEffect(() => {
    if (prevMakeRef.current !== makeValue) {
      prevMakeRef.current = makeValue
      setShowCustomInput(false)
    }
  }, [makeValue])

  const label = typeof field?.label === 'string' ? field.label : 'Model'
  const required = field?.required ?? false

  // No predefined models for this make → plain text input
  if (!makeValue || makeValue === 'Other' || !hasModels) {
    return (
      <div className="field-type text" style={{ marginBottom: '1.5rem' }}>
        <label
          htmlFor={resolvedPath}
          style={{ display: 'block', marginBottom: '0.4rem', fontWeight: 600, fontSize: '13px' }}
        >
          {label}
          {required && <span style={{ color: 'var(--theme-error-500, red)', marginLeft: '2px' }}>*</span>}
        </label>
        <input
          id={resolvedPath}
          type="text"
          value={value ?? ''}
          onChange={(e) => { if (!readOnly) setValue(e.target.value) }}
          readOnly={readOnly}
          placeholder="Enter model..."
          style={inputStyle}
        />
      </div>
    )
  }

  const options = [...models, 'Other']
  const selectValue = showCustomInput ? 'Other' : (models.includes(value ?? '') ? (value ?? '') : '')

  return (
    <div className="field-type text" style={{ marginBottom: '1.5rem' }}>
      <label
        htmlFor={resolvedPath}
        style={{ display: 'block', marginBottom: '0.4rem', fontWeight: 600, fontSize: '13px' }}
      >
        {label}
        {required && <span style={{ color: 'var(--theme-error-500, red)', marginLeft: '2px' }}>*</span>}
      </label>
      <select
        id={resolvedPath}
        value={selectValue}
        onChange={(e) => {
          if (e.target.value === 'Other') {
            setShowCustomInput(true)
            setValue('')
          } else {
            setShowCustomInput(false)
            setValue(e.target.value)
          }
        }}
        disabled={readOnly}
        style={{ ...inputStyle, cursor: readOnly ? 'not-allowed' : 'pointer' }}
      >
        <option value="">Select a model...</option>
        {options.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      {showCustomInput && (
        <input
          type="text"
          value={value ?? ''}
          onChange={(e) => { if (!readOnly) setValue(e.target.value) }}
          readOnly={readOnly}
          placeholder="Enter model name..."
          style={{ ...inputStyle, marginTop: '0.5rem' }}
        />
      )}
    </div>
  )
}

export default ModelSelectComponent
