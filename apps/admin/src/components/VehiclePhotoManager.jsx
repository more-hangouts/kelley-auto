import { useState } from 'react'
import { Box, Button, Chip, Collapse, IconButton, Stack, TextField, Tooltip, Typography } from '@mui/material'
import AddPhotoAlternateOutlinedIcon from '@mui/icons-material/AddPhotoAlternateOutlined'
import AccessibilityNewOutlinedIcon from '@mui/icons-material/AccessibilityNewOutlined'
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

// Visual photo manager for the vehicle editor. A grid of real thumbnails
// you drag to reorder; the first photo is the public cover.
//
// Two modes share one UI:
//  - EDIT: `urls` are already uploaded (`image_urls`). Adding a file
//    uploads it immediately via `onUpload`.
//  - CREATE: no vehicle id exists yet, so selected files are STAGED
//    (`staged` = [{id, file, preview}]) and uploaded after the vehicle is
//    created. Order/cover chosen here is the order they upload in.

const TILE = 116
const ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

function PhotoTile({ id, src, isCover, onMakeCover, onRemove, broken, onBroken }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
    zIndex: isDragging ? 1 : 'auto',
  }

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
          src={src}
          alt=""
          draggable={false}
          onError={onBroken}
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
        sx={{ position: 'absolute', top: 2, right: 2, opacity: 0, transition: 'opacity 120ms' }}
      >
        {!isCover ? (
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
        ) : (
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
  mode = 'edit',
  urls = [],
  onChange,
  staged = [],
  onStagedChange,
  onUpload,
  uploading = false,
  maxMb = 10,
  onError,
  alts = {},
  onAltsChange,
}) {
  const [broken, setBroken] = useState(() => new Set())
  const [urlOpen, setUrlOpen] = useState(false)
  const [urlDraft, setUrlDraft] = useState('')
  const [altsOpen, setAltsOpen] = useState(false)

  const isCreate = mode === 'create'
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  )

  // Normalize both modes to a common tile model: {key, src}. In create
  // mode the key is the staged id and src is the local blob preview; in
  // edit mode the key is the URL and src is the resolved media URL.
  const tiles = isCreate
    ? staged.map((s) => ({ key: s.id, src: s.preview }))
    : urls.map((u) => ({ key: u, src: mediaSrc(u) }))

  const markBroken = (key) =>
    setBroken((prev) => (prev.has(key) ? prev : new Set(prev).add(key)))

  function reorder(fromKey, toKey) {
    if (isCreate) {
      const from = staged.findIndex((s) => s.id === fromKey)
      const to = staged.findIndex((s) => s.id === toKey)
      if (from !== -1 && to !== -1) onStagedChange(arrayMove(staged, from, to))
    } else {
      const from = urls.indexOf(fromKey)
      const to = urls.indexOf(toKey)
      if (from !== -1 && to !== -1) onChange(arrayMove(urls, from, to))
    }
  }

  function makeCover(key) {
    if (isCreate) {
      onStagedChange([staged.find((s) => s.id === key), ...staged.filter((s) => s.id !== key)])
    } else {
      onChange([key, ...urls.filter((u) => u !== key)])
    }
  }

  function remove(key) {
    if (isCreate) {
      const gone = staged.find((s) => s.id === key)
      if (gone?.preview) URL.revokeObjectURL(gone.preview)
      onStagedChange(staged.filter((s) => s.id !== key))
    } else {
      onChange(urls.filter((u) => u !== key))
    }
  }

  // Create mode: hold selected files locally with blob previews.
  function handleStageFiles(e) {
    const files = Array.from(e.target.files || [])
    e.target.value = ''
    const accepted = []
    for (const file of files) {
      if (!ALLOWED_TYPES.has(file.type)) {
        onError?.(`${file.name}: only JPG, PNG, or WebP.`)
        continue
      }
      if (file.size > maxMb * 1024 * 1024) {
        onError?.(`${file.name}: larger than ${maxMb} MB.`)
        continue
      }
      accepted.push({
        id: `staged-${file.name}-${file.size}-${file.lastModified}`,
        file,
        preview: URL.createObjectURL(file),
      })
    }
    if (accepted.length) {
      // de-dupe by id so re-selecting the same file doesn't double it
      const existing = new Set(staged.map((s) => s.id))
      const fresh = accepted.filter((a) => !existing.has(a.id))
      fresh.forEach((a) => {
        if (existing.has(a.id)) URL.revokeObjectURL(a.preview)
      })
      onStagedChange([...staged, ...fresh])
    }
  }

  // Only count non-blank descriptions — a whitespace-only value is
  // cleared on save, so counting it would overstate coverage.
  const describedCount = tiles.filter((t) => (alts[t.key] ?? '').trim()).length

  function addUrl() {
    const v = urlDraft.trim()
    if (!v || !/^https?:\/\//i.test(v)) return
    if (!urls.includes(v)) onChange([...urls, v])
    setUrlDraft('')
    setUrlOpen(false)
  }

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="subtitle2">Photos</Typography>
        <Stack direction="row" spacing={1}>
          {!isCreate && (
            <Button
              size="small"
              variant="text"
              startIcon={<LinkOutlinedIcon />}
              onClick={() => setUrlOpen((o) => !o)}
            >
              Add by URL
            </Button>
          )}
          <Button
            component="label"
            size="small"
            variant="outlined"
            startIcon={<PhotoCameraOutlinedIcon />}
            disabled={uploading}
          >
            {uploading ? 'Uploading…' : isCreate ? 'Add photos' : 'Upload'}
            <input
              hidden
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              onChange={isCreate ? handleStageFiles : onUpload}
            />
          </Button>
        </Stack>
      </Stack>

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Drag to reorder. The first photo (★ Cover) is what shows on the website.
        JPG, PNG, or WebP, max {maxMb} MB — uploads are auto-resized and stripped of metadata.
        {isCreate && ' Photos upload when you save the vehicle.'}
      </Typography>

      {!isCreate && (
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
      )}

      {tiles.length === 0 ? (
        <Stack
          alignItems="center"
          justifyContent="center"
          spacing={1}
          sx={{ py: 4, borderRadius: 1.5, border: '1px dashed', borderColor: 'divider', color: 'text.secondary' }}
        >
          <AddPhotoAlternateOutlinedIcon />
          <Typography variant="body2">
            {isCreate ? 'Add photos — they’ll upload when you save.' : 'No photos yet — upload to add some.'}
          </Typography>
        </Stack>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={({ active, over }) => {
            if (over && active.id !== over.id) reorder(active.id, over.id)
          }}
        >
          <SortableContext items={tiles.map((t) => t.key)} strategy={rectSortingStrategy}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
              {tiles.map((t, idx) => (
                <PhotoTile
                  key={t.key}
                  id={t.key}
                  src={t.src}
                  isCover={idx === 0}
                  broken={broken.has(t.key)}
                  onBroken={() => markBroken(t.key)}
                  onMakeCover={() => makeCover(t.key)}
                  onRemove={() => remove(t.key)}
                />
              ))}
            </Box>
          </SortableContext>
        </DndContext>
      )}

      {!isCreate && tiles.length > 0 && onAltsChange && (
        <Box sx={{ mt: 2 }}>
          <Button
            size="small"
            variant="text"
            startIcon={<AccessibilityNewOutlinedIcon />}
            onClick={() => setAltsOpen((o) => !o)}
          >
            {altsOpen ? 'Hide descriptions' : `Descriptions (${describedCount}/${tiles.length})`}
          </Button>

          <Collapse in={altsOpen}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mt: 1, mb: 1.5 }}
            >
              What each photo shows, read aloud to shoppers using a screen
              reader and indexed by search engines. Say the angle and what’s
              visible — “Driver-side profile, black paint, alloy wheels” —
              not “car photo 3”. Descriptions stay attached to their photo
              when you drag to reorder.
            </Typography>

            <Stack spacing={1.5}>
              {tiles.map((t, idx) => (
                <Stack key={t.key} direction="row" spacing={1.5} alignItems="flex-start">
                  <Box
                    component="img"
                    src={t.src}
                    alt=""
                    sx={{
                      width: 64,
                      height: 48,
                      objectFit: 'cover',
                      borderRadius: 1,
                      flexShrink: 0,
                      bgcolor: 'action.hover',
                    }}
                  />
                  <TextField
                    size="small"
                    fullWidth
                    multiline
                    maxRows={3}
                    label={idx === 0 ? 'Cover photo' : `Photo ${idx + 1}`}
                    placeholder="Describe what this photo shows"
                    value={alts[t.key] ?? ''}
                    inputProps={{ maxLength: 300 }}
                    onChange={(e) =>
                      onAltsChange({ ...alts, [t.key]: e.target.value })
                    }
                  />
                </Stack>
              ))}
            </Stack>
          </Collapse>
        </Box>
      )}
    </Box>
  )
}
