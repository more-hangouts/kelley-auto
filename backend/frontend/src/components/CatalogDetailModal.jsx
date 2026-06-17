import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  Divider,
  IconButton,
  Stack,
  Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'

import { getCatalogPriceBreakdown } from '../services/api'

// Read-only catalog detail view, opened by clicking a product card photo
// or title on the Products page. Deliberately separate from the Edit
// dialog: this is the "look at the dress" surface (gallery, colors,
// description, and a plain-language price breakdown), while Edit stays
// the admin write surface. The Edit button here just hands off to the
// existing editor.

function formatPrice(cents) {
  if (cents === null || cents === undefined) return null
  return `$${(cents / 100).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

// Gather every image across the style's color variants, de-duplicated,
// so the gallery shows the full range, not just the primary color.
function galleryImages(group) {
  const seen = new Set()
  const urls = []
  for (const variant of group.variants) {
    for (const url of variant.image_urls || []) {
      if (!seen.has(url)) {
        seen.add(url)
        urls.push(url)
      }
    }
  }
  return urls
}

export default function CatalogDetailModal({ group, open, onClose, onEdit }) {
  const [activeImage, setActiveImage] = useState(0)

  // The representative row that carries the price (group price is the min
  // across variants; for Morilee every color shares a price). Prefer a
  // variant that actually has a price.
  const priced =
    group?.variants?.find(
      (v) => v.unit_price_cents !== null && v.unit_price_cents !== undefined,
    ) || group?.primary

  useEffect(() => {
    setActiveImage(0)
  }, [group?.key])

  const { data: breakdown, isLoading: priceLoading } = useQuery({
    queryKey: ['catalog-price-breakdown', priced?.id],
    queryFn: () => getCatalogPriceBreakdown(priced.id),
    enabled: !!open && Number.isFinite(priced?.id),
    staleTime: 60_000,
  })

  if (!group) return null

  const primary = group.primary
  const images = galleryImages(group)
  const heroImage = images[activeImage] || images[0]
  const colors = group.variants.map((v) => v.color).filter(Boolean)
  const title =
    primary.product_title ||
    primary.house_name ||
    primary.style_number ||
    primary.internal_sku
  const subtitle =
    [primary.designer, primary.style_number].filter(Boolean).join(' · ') ||
    primary.public_code

  const packagePrice = formatPrice(breakdown?.package_price_cents)
  const dressOnly = formatPrice(breakdown?.dress_only_price_cents)
  const removable = (breakdown?.items || []).filter((i) => i.removable)

  return (
    <Dialog open={!!open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogContent sx={{ p: 0 }}>
        <Stack
          direction={{ xs: 'column', md: 'row' }}
          sx={{ minHeight: { md: 520 } }}
        >
          {/* Gallery */}
          <Box
            sx={{
              flex: { md: '0 0 45%' },
              bgcolor: 'grey.100',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <Box
              sx={{
                aspectRatio: '4 / 5',
                width: '100%',
                backgroundImage: heroImage ? `url(${heroImage})` : 'none',
                backgroundPosition: 'center',
                backgroundSize: 'cover',
                backgroundRepeat: 'no-repeat',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'text.secondary',
              }}
            >
              {!heroImage && <Typography variant="caption">No image</Typography>}
            </Box>
            {images.length > 1 && (
              <Stack
                direction="row"
                spacing={0.75}
                sx={{ p: 1, overflowX: 'auto' }}
              >
                {images.slice(0, 8).map((url, idx) => (
                  <Box
                    key={url}
                    onClick={() => setActiveImage(idx)}
                    sx={{
                      flex: '0 0 auto',
                      width: 48,
                      height: 60,
                      borderRadius: 0.5,
                      cursor: 'pointer',
                      backgroundImage: `url(${url})`,
                      backgroundPosition: 'center',
                      backgroundSize: 'cover',
                      outline: idx === activeImage ? '2px solid' : 'none',
                      outlineColor: 'primary.main',
                    }}
                  />
                ))}
              </Stack>
            )}
          </Box>

          {/* Details */}
          <Box sx={{ flex: 1, p: 2.5, position: 'relative' }}>
            <IconButton
              size="small"
              onClick={onClose}
              sx={{ position: 'absolute', top: 8, right: 8 }}
              aria-label="Close"
            >
              <CloseIcon fontSize="small" />
            </IconButton>

            <Stack spacing={2} sx={{ pr: 4 }}>
              <Box>
                <Typography variant="h6" fontWeight={700}>
                  {title}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {subtitle}
                </Typography>
              </Box>

              {/* Price breakdown */}
              <Box
                sx={{
                  border: 1,
                  borderColor: 'divider',
                  borderRadius: 1,
                  p: 1.75,
                  bgcolor: 'background.paper',
                }}
              >
                {priceLoading ? (
                  <CircularProgress size={18} />
                ) : packagePrice ? (
                  <Stack spacing={1}>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="baseline"
                    >
                      <Typography variant="h5" fontWeight={700}>
                        {packagePrice}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        full package
                      </Typography>
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      Includes{' '}
                      {(breakdown.items || [])
                        .map((i) => i.label.toLowerCase())
                        .join(', ')}
                      .
                    </Typography>
                    {dressOnly && (
                      <>
                        <Divider />
                        <Stack
                          direction="row"
                          justifyContent="space-between"
                          alignItems="baseline"
                        >
                          <Typography variant="body2">Dress only</Typography>
                          <Typography variant="body1" fontWeight={700}>
                            {dressOnly}
                          </Typography>
                        </Stack>
                      </>
                    )}
                    {removable.length > 0 && (
                      <Box>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mb: 0.5 }}
                        >
                          Remove an item to save:
                        </Typography>
                        <Stack
                          direction="row"
                          spacing={0.75}
                          flexWrap="wrap"
                          useFlexGap
                        >
                          {removable.map((i) => (
                            <Chip
                              key={i.key}
                              size="small"
                              variant="outlined"
                              label={`${i.label} −${formatPrice(i.deduct_cents)}`}
                            />
                          ))}
                        </Stack>
                      </Box>
                    )}
                    <Typography variant="caption" color="text.secondary">
                      Discounts up to{' '}
                      {breakdown.discretionary_discount_max_percent}% are at your
                      discretion; more needs a manager.
                    </Typography>
                  </Stack>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    No price set for this style yet.
                  </Typography>
                )}
              </Box>

              {/* Colors */}
              {colors.length > 0 && (
                <Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    Colors ({colors.length})
                  </Typography>
                  <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                    {colors.map((color, idx) => (
                      <Chip key={`${color}-${idx}`} size="small" label={color} />
                    ))}
                  </Stack>
                </Box>
              )}

              {/* Description */}
              {primary.description_text && (
                <Box>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    Description
                  </Typography>
                  <Typography variant="body2">
                    {primary.description_text}
                  </Typography>
                </Box>
              )}

              <Stack direction="row" justifyContent="flex-end" spacing={1}>
                {onEdit && (
                  <Button variant="outlined" onClick={() => onEdit(primary)}>
                    Edit
                  </Button>
                )}
                <Button variant="contained" onClick={onClose}>
                  Close
                </Button>
              </Stack>
            </Stack>
          </Box>
        </Stack>
      </DialogContent>
    </Dialog>
  )
}
