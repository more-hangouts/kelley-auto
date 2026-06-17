import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

// Staff-side signature capture for in-store quote approval. Mirrors the
// customer portal's pad (templates/portal/quote.html): the canvas
// captures pen/touch strokes; submit returns the raw base64 PNG bytes
// (no data: URL prefix) plus the typed name. Both are required.
export default function SignatureDialog({
  open,
  onClose,
  onSubmit,
  submitting = false,
  errorMessage = null,
  customerName = '',
  title = 'Sign in store',
  submitLabel = 'Sign and approve',
}) {
  const canvasRef = useRef(null)
  const ctxRef = useRef(null)
  const drawingRef = useRef(false)
  const dirtyRef = useRef(false)

  const [name, setName] = useState('')
  const [localError, setLocalError] = useState(null)

  useEffect(() => {
    if (!open) return undefined
    setName(customerName || '')
    setLocalError(null)
    dirtyRef.current = false

    // The Dialog's content paints after the open transition, so the
    // canvas measurements aren't reliable until the next frame. Run
    // the resize/scale on rAF to make sure getBoundingClientRect()
    // returns the painted size.
    const raf = requestAnimationFrame(() => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const ratio = window.devicePixelRatio || 1
      canvas.width = rect.width * ratio
      canvas.height = 200 * ratio
      canvas.style.height = '200px'
      const ctx = canvas.getContext('2d')
      ctx.scale(ratio, ratio)
      ctx.lineWidth = 2
      ctx.lineCap = 'round'
      ctx.strokeStyle = '#2A1B1F'
      ctxRef.current = ctx
    })
    return () => cancelAnimationFrame(raf)
  }, [open, customerName])

  const pos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect()
    const t = e.touches ? e.touches[0] : e
    return { x: t.clientX - rect.left, y: t.clientY - rect.top }
  }

  const handleStart = (e) => {
    if (!ctxRef.current) return
    drawingRef.current = true
    const p = pos(e)
    ctxRef.current.beginPath()
    ctxRef.current.moveTo(p.x, p.y)
    e.preventDefault()
  }
  const handleMove = (e) => {
    if (!drawingRef.current || !ctxRef.current) return
    const p = pos(e)
    ctxRef.current.lineTo(p.x, p.y)
    ctxRef.current.stroke()
    dirtyRef.current = true
    e.preventDefault()
  }
  const handleEnd = () => {
    drawingRef.current = false
  }

  const clearPad = () => {
    const canvas = canvasRef.current
    const ctx = ctxRef.current
    if (!canvas || !ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    dirtyRef.current = false
  }

  const handleSubmit = () => {
    setLocalError(null)
    const trimmed = name.trim()
    if (!trimmed) {
      setLocalError('Please type the customer’s full name.')
      return
    }
    if (!dirtyRef.current) {
      setLocalError('Please have the customer sign on the pad.')
      return
    }
    const dataUrl = canvasRef.current.toDataURL('image/png')
    const comma = dataUrl.indexOf(',')
    const base64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl
    onSubmit({ signatureName: trimmed, signatureBase64: base64 })
  }

  return (
    <Dialog open={open} onClose={submitting ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Typography variant="body2" color="text.secondary">
            By signing, the customer accepts this quote as their contract.
            The signature is stored with the quote and timestamped.
          </Typography>
          <TextField
            label="Customer full legal name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ maxLength: 120 }}
            disabled={submitting}
            autoFocus
            fullWidth
          />
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ mb: 0.5, display: 'block' }}>
              Signature
            </Typography>
            <Box
              sx={{
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                bgcolor: 'background.paper',
                touchAction: 'none',
              }}
            >
              <canvas
                ref={canvasRef}
                style={{ width: '100%', height: 200, display: 'block', cursor: 'crosshair' }}
                onMouseDown={handleStart}
                onMouseMove={handleMove}
                onMouseUp={handleEnd}
                onMouseLeave={handleEnd}
                onTouchStart={handleStart}
                onTouchMove={handleMove}
                onTouchEnd={handleEnd}
              />
            </Box>
            <Box sx={{ mt: 1, textAlign: 'right' }}>
              <Button size="small" onClick={clearPad} disabled={submitting}>
                Clear
              </Button>
            </Box>
          </Box>
          {(localError || errorMessage) && (
            <Alert severity="error">{localError || errorMessage}</Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          onClick={handleSubmit}
          variant="contained"
          color="success"
          disabled={submitting}
        >
          {submitting ? 'Signing…' : submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
