import { useState } from 'react'
import { Box, Button, Chip, Collapse, IconButton, Stack, TextField, Tooltip, Typography } from '@mui/material'
import AddPhotoAlternateOutlinedIcon from '@mui/icons-material/AddPhotoAlternateOutlined'
import BrokenImageOutlinedIcon from '@mui/icons-material/BrokenImageOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import LinkOutlinedIcon from '@mui/icons-material/LinkOutlined'
import PhotoCameraOutlinedIcon from '@mui/icons-material/PhotoCameraOutlined'
import StarIcon from '@mui/icons-material/Star'
import StarOutlineIcon from '@mui/icons-material/StarOutline'
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

import { mediaSrc } from '../utils/mediaUrl'

// Visual photo manager for the vehicle editor. Replaces the old raw
// "Photo URLs" text list: a grid of real thumbnails you drag to reorder,
// with the first photo used as the public cover. Uploaded photos and
// pasted external URLs live in the same ordered list (`image_urls`); the
// first entry is what the storefront shows as the card image.

const TILE = 116

function PhotoTile({ url, isCover, onMakeCover, onRemove, broken, onBroken }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: url })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
    zIndex: isDragging ? 1 : 'auto',
  }

  // Stop pointer-down on the buttons from starting a drag.
  const stop = (e) => e.stopPropagation()

  return (
    <Box
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      sx={{
        position: 'relative',
        width: TILE,
        height: TILE,
        borderRadius: 1.5,
        overflow: 'hidden',
        border: '1px solid',
        borderColor: isCover ? 'primary.main' : 'divider',
        boxShadow: isCover ? (t) => `0 0 0 1px ${t.palette.primary.main}` : 'none',
        bgcolor: 'action.hover',
        cursor: 'grab',
        touchAction: 'none',
        '&:hover .photo-actions': { opacity: 1 },
      }}
    >
      {broken ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          sx={{ width: '100%', height: '100%', color: 'text.disabled', p: 1, textAlign: 'center' }}
        >
          <BrokenImageOutlinedIcon fontSize="small" />
          <Typography variant="caption" sx={{ fontSize: 9, mt: 0.5 }}>
            Can’t load
          </Typography>
        </Stack>
      ) : (
        <Box
          component="img"
          src={mediaSrc(url)}
          alt=""
          draggable={false}
          onError={() => onBroken(url)}
          sx={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      )}

      {isCover && (
        <Chip
          size="small"
          label="Cover"
          color="primary"
          sx={{ position: 'absolute', top: 4, left: 4, height: 18, fontSize: 10 }}
        />
      )}

      <Stack
        direction="row"
        spacing={0.5}
        className="photo-actions"
        sx={{
          position: 'absolute',
          top: 2,
          right: 2,
          opacity: 0,
          transition: 'opacity 120ms',
        }}
      >
        {!isCover && (
          <Tooltip title="Make cover photo">
            <IconButton
              size="small"
              onPointerDown={stop}
              onClick={onMakeCover}
              sx={{ bgcolor: 'rgba(0,0,0,0.55)', color: '#fff', '&:hover': { bgcolor: 'rgba(0,0,0,0.75)' }, p: 0.4 }}
            >
              <StarOutlineIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        )}
        {isCover && (
          <Box sx={{ bgcolor: 'rgba(0,0,0,0.55)', borderRadius: '50%', p: 0.4, display: 'flex' }}>
            <StarIcon sx={{ fontSize: 16, color: (t) => t.palette.primary.main }} />
          </Box>
        )}
        <Tooltip title="Remove photo">
          <IconButton
            size="small"
            onPointerDown={stop}
            onClick={onRemove}
            sx={{ bgcolor: 'rgba(0,0,0,0.55)', color: '#fff', '&:hover': { bgcolor: 'error.main' }, p: 0.4 }}
          >
            <DeleteOutlineIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </Tooltip>
      </Stack>
    </Box>
  )
}

export default function VehiclePhotoManager({
  urls,
  onChange,
  onUpload,
  uploading = false,
  canUpload = true,
  maxMb = 10,
}) {
  const [broken, setBroken] = useState(() => new Set())
  const [urlOpen, setUrlOpen] = useState(false)
  const [urlDraft, setUrlDraft] = useState('')

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  )

  const markBroken = (u) =>
    setBroken((prev) => {
      if (prev.has(u)) return prev
      const next = new Set(prev)
      next.add(u)
      return next
    })

  function handleDragEnd(event) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const from = urls.indexOf(active.id)
    const to = urls.indexOf(over.id)
    if (from === -1 || to === -1) return
    onChange(arrayMove(urls, from, to))
  }

  const makeCover = (u) => onChange([u, ...urls.filter((x) => x !== u)])
  const remove = (u) => onChange(urls.filter((x) => x !== u))

  function addUrl() {
    const v = urlDraft.trim()
    if (!v) return
    if (!/^https?:\/\//i.test(v)) return
    if (!urls.includes(v)) onChange([...urls, v])
    setUrlDraft('')
    setUrlOpen(false)
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="subtitle2">Photos</Typography>
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            variant="text"
            startIcon={<LinkOutlinedIcon />}
            onClick={() => setUrlOpen((o) => !o)}
          >
            Add by URL
          </Button>
          <Tooltip title={canUpload ? '' : 'Save the vehicle first, then upload photos'} disableInteractive>
            <span>
              <Button
                component="label"
                size="small"
                variant="outlined"
                startIcon={<PhotoCameraOutlinedIcon />}
                disabled={!canUpload || uploading}
              >
                {uploading ? 'Uploading…' : 'Upload'}
                <input
                  hidden
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  multiple
                  onChange={onUpload}
                />
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </Stack>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Drag to reorder. The first photo (★ Cover) is what shows on the website.
        JPG, PNG, or WebP, max {maxMb} MB — uploads are auto-resized and stripped of metadata.
      </Typography>

      <Collapse in={urlOpen}>
        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="https://…/photo.jpg"
            value={urlDraft}
            onChange={(e) => setUrlDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addUrl()
              }
            }}
          />
          <Button variant="outlined" onClick={addUrl} disabled={!urlDraft.trim()}>
            Add
          </Button>
        </Stack>
      </Collapse>

      {urls.length === 0 ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          spacing={1}
          sx={{
            py: 4,
            borderRadius: 1.5,
            border: '1px dashed',
            borderColor: 'divider',
            color: 'text.secondary',
          }}
        >
          <AddPhotoAlternateOutlinedIcon />
          <Typography variant="body2">
            {canUpload ? 'No photos yet — upload to add some.' : 'Save the vehicle, then reopen it to add photos.'}
          </Typography>
        </Stack>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={urls} strategy={rectSortingStrategy}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
              {urls.map((u, idx) => (
                <PhotoTile
                  key={u}
                  url={u}
                  isCover={idx === 0}
                  broken={broken.has(u)}
                  onBroken={markBroken}
                  onMakeCover={() => makeCover(u)}
                  onRemove={() => remove(u)}
                />
              ))}
            </Box>
          </SortableContext>
        </DndContext>
      )}
    </Box>
  )
}
