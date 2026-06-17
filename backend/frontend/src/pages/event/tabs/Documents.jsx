import { useRef, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import CloudUploadOutlinedIcon from '@mui/icons-material/CloudUploadOutlined'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import DownloadIcon from '@mui/icons-material/Download'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import ConfirmDialog from '../../../components/ConfirmDialog'
import { useAuth } from '../../../contexts/AuthContext'
import {
  deleteDocument,
  downloadDocument,
  listEventDocuments,
  patchDocument,
  uploadEventDocument,
  viewDocument,
} from '../../../services/api'

function canDelete(user, doc) {
  if (!user) return false
  if (user.role === 'admin') return true
  return doc.uploaded_by_user_id === user.id
}

const ALLOWED_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png', 'heic', 'docx']
const MAX_BYTES = 25 * 1024 * 1024

function getExtension(name) {
  const idx = name.lastIndexOf('.')
  if (idx < 0) return ''
  return name.slice(idx + 1).toLowerCase()
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function validateFile(file) {
  const ext = getExtension(file.name)
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `Unsupported file type. Allowed: ${ALLOWED_EXTENSIONS.join(', ')}.`
  }
  if (file.size > MAX_BYTES) {
    return `File is ${formatBytes(file.size)}. Maximum is 25 MB.`
  }
  return null
}

function uploadErrorMessage(err) {
  const detail = err?.response?.data?.detail
  if (detail === 'file_too_large') return 'Server rejected the file as too large.'
  if (detail === 'unsupported_type') return 'Server rejected the file type.'
  if (detail === 'insufficient_storage') return 'Server is out of disk space. Tell ops before retrying.'
  if (detail === 'event_not_found') return 'Event no longer exists. Refresh and try again.'
  return 'Upload failed. Check your connection and try again.'
}

function deleteErrorMessage(err) {
  const detail = err?.response?.data?.detail
  if (detail === 'delete_forbidden') return 'Only the uploader or an admin can delete this file.'
  if (detail === 'document_not_found') return 'File was already removed.'
  return 'Could not delete. Try again.'
}

export default function Documents() {
  const { event } = useOutletContext()
  const { user } = useAuth()
  const eventId = event.id
  const queryClient = useQueryClient()
  const fileInputRef = useRef(null)
  const queryKey = ['event', eventId, 'documents', { kind: 'document' }]
  const countsKey = ['event', eventId, 'document-counts']

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey })
    queryClient.invalidateQueries({ queryKey: countsKey })
    queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
  }

  const [uploadError, setUploadError] = useState(null)
  const [pendingUpload, setPendingUpload] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editingLabel, setEditingLabel] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null)

  const documentsQuery = useQuery({
    queryKey,
    queryFn: () => listEventDocuments(eventId, 'document'),
  })

  const uploadMutation = useMutation({
    mutationFn: ({ file }) =>
      uploadEventDocument({
        eventId,
        file,
        kind: 'document',
        onProgress: (pct) =>
          setPendingUpload((s) => (s ? { ...s, progress: pct } : s)),
      }),
    onSuccess: invalidateAll,
    onSettled: () => {
      setPendingUpload(null)
    },
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, label }) => patchDocument(id, { label }),
    onSuccess: () => {
      invalidateAll()
      setEditingId(null)
      setEditingLabel('')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id) => deleteDocument(id),
    onSuccess: invalidateAll,
  })

  function handleFiles(files) {
    setUploadError(null)
    if (!files || files.length === 0) return
    const file = files[0]
    const error = validateFile(file)
    if (error) {
      setUploadError(error)
      return
    }
    setPendingUpload({ name: file.name, progress: 0 })
    uploadMutation.mutate(
      { file },
      {
        onError: (err) => {
          setUploadError(uploadErrorMessage(err))
          setPendingUpload(null)
        },
      },
    )
  }

  function startEdit(doc) {
    setEditingId(doc.id)
    setEditingLabel(doc.label || '')
  }

  function commitEdit() {
    if (editingId == null) return
    renameMutation.mutate({ id: editingId, label: editingLabel })
  }

  function cancelEdit() {
    setEditingId(null)
    setEditingLabel('')
  }

  function requestDelete(doc) {
    setPendingDelete(doc)
  }

  function confirmDelete() {
    if (!pendingDelete) return
    const doc = pendingDelete
    deleteMutation.mutate(doc.id, {
      onError: (err) => {
        setUploadError(deleteErrorMessage(err))
      },
      onSettled: () => {
        setPendingDelete(null)
      },
    })
  }

  async function handleView(doc) {
    setUploadError(null)
    try {
      await viewDocument(doc.id)
    } catch (err) {
      if (err?.code === 'popup_blocked') {
        setUploadError('Popup was blocked. Allow popups for this site to preview files in a new tab.')
      } else {
        setUploadError('Could not open the file. Try downloading it instead.')
      }
    }
  }

  function onDragOver(e) {
    e.preventDefault()
    setDragActive(true)
  }
  function onDragLeave(e) {
    e.preventDefault()
    setDragActive(false)
  }
  function onDrop(e) {
    e.preventDefault()
    setDragActive(false)
    handleFiles(e.dataTransfer.files)
  }

  const docs = documentsQuery.data || []

  return (
    <Box>
      <Paper
        sx={{
          p: 3,
          mb: 2,
          border: '2px dashed',
          borderColor: dragActive ? 'primary.main' : 'divider',
          bgcolor: dragActive ? 'rgba(93, 58, 107, 0.04)' : 'background.paper',
          textAlign: 'center',
          transition: 'border-color 120ms, background-color 120ms',
        }}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <CloudUploadOutlinedIcon
          sx={{ fontSize: 36, color: 'text.disabled', mb: 1 }}
        />
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Drop files here or
        </Typography>
        <Button
          variant="outlined"
          size="small"
          onClick={() => fileInputRef.current?.click()}
          disabled={!!pendingUpload}
        >
          Choose a file
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.jpg,.jpeg,.png,.heic,.docx"
          hidden
          onChange={(e) => {
            handleFiles(e.target.files)
            e.target.value = ''
          }}
        />
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: 'block', mt: 1 }}
        >
          PDF, JPG, PNG, HEIC, DOCX. 25 MB max.
        </Typography>
      </Paper>

      {pendingUpload && (
        <Paper sx={{ p: 1.5, mb: 2 }}>
          <Typography variant="caption" color="text.secondary">
            Uploading {pendingUpload.name}
          </Typography>
          <LinearProgress
            variant="determinate"
            value={pendingUpload.progress}
            sx={{ mt: 0.5 }}
          />
        </Paper>
      )}

      {uploadError && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          onClose={() => setUploadError(null)}
        >
          {uploadError}
        </Alert>
      )}

      {documentsQuery.isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
          <CircularProgress />
        </Box>
      ) : docs.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <DescriptionOutlinedIcon
            sx={{ fontSize: 36, color: 'text.disabled', mb: 1 }}
          />
          <Typography variant="body2" color="text.secondary">
            No documents yet. Drop a file above to attach a contract or order
            confirmation to this lead.
          </Typography>
        </Paper>
      ) : (
        <Paper sx={{ overflow: 'hidden' }}>
          {docs.map((doc, i) => (
            <Box
              key={doc.id}
              sx={{
                p: 2,
                borderBottom: i < docs.length - 1 ? '1px solid' : 'none',
                borderColor: 'divider',
                display: 'flex',
                alignItems: 'center',
                gap: 2,
              }}
            >
              <DescriptionOutlinedIcon color="action" />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  variant="body2"
                  noWrap
                  onClick={() => handleView(doc)}
                  sx={{
                    fontWeight: 500,
                    cursor: 'pointer',
                    color: 'primary.main',
                    '&:hover': { textDecoration: 'underline' },
                  }}
                >
                  {doc.filename}
                </Typography>
                {editingId === doc.id ? (
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    sx={{ mt: 0.5 }}
                  >
                    <TextField
                      size="small"
                      value={editingLabel}
                      onChange={(e) => setEditingLabel(e.target.value)}
                      placeholder="Add a label"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitEdit()
                        if (e.key === 'Escape') cancelEdit()
                      }}
                      disabled={renameMutation.isPending}
                      sx={{ flex: 1 }}
                    />
                    <Button
                      size="small"
                      onClick={commitEdit}
                      disabled={renameMutation.isPending}
                    >
                      Save
                    </Button>
                    <Button
                      size="small"
                      onClick={cancelEdit}
                      disabled={renameMutation.isPending}
                    >
                      Cancel
                    </Button>
                  </Stack>
                ) : (
                  <Typography
                    variant="caption"
                    color={doc.label ? 'text.secondary' : 'text.disabled'}
                    sx={{ cursor: 'pointer', display: 'inline-block' }}
                    onClick={() => startEdit(doc)}
                  >
                    {doc.label || 'Add label'}
                  </Typography>
                )}
              </Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ minWidth: 80, textAlign: 'right' }}
              >
                {formatBytes(doc.byte_size)}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ minWidth: 110, textAlign: 'right' }}
              >
                {dayjs(doc.created_at).format('MMM D, YYYY')}
              </Typography>
              <IconButton
                size="small"
                onClick={() => downloadDocument(doc.id, doc.filename)}
                aria-label="download"
              >
                <DownloadIcon fontSize="small" />
              </IconButton>
              {(() => {
                const allowed = canDelete(user, doc)
                const btn = (
                  <IconButton
                    size="small"
                    onClick={() => allowed && requestDelete(doc)}
                    aria-label="delete"
                    disabled={!allowed || deleteMutation.isPending}
                  >
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                )
                return allowed ? btn : (
                  <Tooltip title="Only the uploader or an admin can delete this file.">
                    <span>{btn}</span>
                  </Tooltip>
                )
              })()}
            </Box>
          ))}
        </Paper>
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title="Delete file?"
        message={
          pendingDelete
            ? `"${pendingDelete.filename}" will be removed from this lead. This cannot be undone from the UI.`
            : ''
        }
        confirmLabel="Delete"
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
        pending={deleteMutation.isPending}
      />
    </Box>
  )
}
