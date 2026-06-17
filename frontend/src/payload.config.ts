import { buildConfig } from 'payload'
import { postgresAdapter } from '@payloadcms/db-postgres'
import { lexicalEditor } from '@payloadcms/richtext-lexical'
import sharp from 'sharp'
import { fileURLToPath } from 'url'
import path from 'path'

const filename = fileURLToPath(import.meta.url)
const dirname = path.dirname(filename)

// Year options: 2026 down to 1980
const yearOptions = Array.from({ length: 47 }, (_, i) => {
  const y = (2026 - i).toString()
  return { label: y, value: y }
})

const makeOptions = [
  'Acura', 'Alfa Romeo', 'Aston Martin', 'Audi', 'Bentley', 'BMW',
  'Buick', 'Cadillac', 'Chevrolet', 'Chrysler', 'Dodge', 'Ferrari',
  'Fiat', 'Ford', 'Genesis', 'GMC', 'Honda', 'Hyundai', 'Infiniti',
  'Jaguar', 'Jeep', 'Kia', 'Lamborghini', 'Land Rover', 'Lexus',
  'Lincoln', 'Maserati', 'Mazda', 'McLaren', 'Mercedes-Benz', 'MINI',
  'Mitsubishi', 'Nissan', 'Pontiac', 'Porsche', 'Ram', 'Rolls-Royce',
  'Subaru', 'Tesla', 'Toyota', 'Volkswagen', 'Volvo', 'Other',
].map((m) => ({ label: m, value: m }))

const exteriorColorOptions = [
  'Black', 'White', 'Silver', 'Gray', 'Red', 'Blue', 'Navy Blue',
  'Dark Blue', 'Green', 'Brown', 'Beige', 'Orange', 'Yellow', 'Gold',
  'Purple', 'Maroon', 'Champagne', 'Pearl White', 'Gunmetal', 'Other',
].map((c) => ({ label: c, value: c }))

const interiorColorOptions = [
  'Black', 'Beige', 'Gray', 'Dark Gray', 'Brown', 'Red', 'White', 'Tan', 'Cream', 'Other',
].map((c) => ({ label: c, value: c }))

export default buildConfig({
  sharp,
  admin: {
    user: 'users',
  },
  collections: [
    {
      slug: 'users',
      auth: {
        cookies: {
          secure: process.env.NODE_ENV === 'production',
          sameSite: 'Lax' as const,
        },
      },
      fields: [],
    },
    {
      slug: 'media',
      upload: {
        mimeTypes: ['image/jpeg', 'image/png', 'image/webp', 'image/gif'],
        imageSizes: [
          {
            name: 'thumbnail',
            width: 400,
            height: 300,
            fit: 'cover',
            formatOptions: {
              format: 'webp',
              options: { quality: 75 },
            },
          },
          {
            name: 'card',
            width: 768,
            height: 576,
            fit: 'cover',
            formatOptions: {
              format: 'webp',
              options: { quality: 80 },
            },
          },
          {
            name: 'full',
            width: 1920,
            withoutEnlargement: true,
            formatOptions: {
              format: 'webp',
              options: { quality: 80 },
            },
          },
        ],
        adminThumbnail: 'thumbnail',
        formatOptions: {
          format: 'webp',
          options: { quality: 80 },
        },
        resizeOptions: {
          width: 2560,
          withoutEnlargement: true,
        },
      },
      access: {
        read: () => true,
        create: ({ req: { user } }) => Boolean(user),
        update: ({ req: { user } }) => Boolean(user),
        delete: ({ req: { user } }) => Boolean(user),
      },
      fields: [
        {
          name: 'alt',
          type: 'text',
        },
      ],
    },
    {
      slug: 'vehicles',
      admin: {
        useAsTitle: 'title',
      },
      access: {
        read: () => true,
        create: ({ req: { user } }) => Boolean(user),
        update: ({ req: { user } }) => Boolean(user),
        delete: ({ req: { user } }) => Boolean(user),
      },
      fields: [
        {
          name: 'title',
          type: 'text',
          required: true,
        },
        {
          name: 'vin',
          type: 'text',
          admin: {
            components: {
              Field: '@/collections/fields/VinDecoder#VinDecoderComponent',
            },
          },
        },
        {
          name: 'make',
          type: 'select',
          required: true,
          options: makeOptions,
        },
        {
          name: 'model',
          type: 'text',
          required: true,
          admin: {
            components: {
              Field: '@/collections/fields/ModelSelect#ModelSelectComponent',
            },
          },
        },
        {
          name: 'year',
          type: 'select',
          required: true,
          options: yearOptions,
        },
        {
          name: 'cashPrice',
          type: 'number',
        },
        {
          name: 'mileage',
          type: 'number',
        },
        {
          name: 'condition',
          type: 'select',
          options: [
            { label: 'New', value: 'NEW' },
            { label: 'Used', value: 'USED' },
          ],
        },
        {
          name: 'exteriorColor',
          type: 'select',
          options: exteriorColorOptions,
        },
        {
          name: 'exteriorColorCustom',
          type: 'text',
          label: 'Custom Exterior Color',
          admin: {
            condition: (_, siblingData) => siblingData?.exteriorColor === 'Other',
          },
        },
        {
          name: 'interiorColor',
          type: 'select',
          options: interiorColorOptions,
        },
        {
          name: 'interiorColorCustom',
          type: 'text',
          label: 'Custom Interior Color',
          admin: {
            condition: (_, siblingData) => siblingData?.interiorColor === 'Other',
          },
        },
        {
          name: 'transmission',
          type: 'select',
          options: [
            { label: 'Automatic', value: 'AUTOMATIC' },
            { label: 'Manual', value: 'MANUAL' },
          ],
        },
        {
          name: 'fuelType',
          type: 'select',
          options: [
            { label: 'Gas', value: 'GAS' },
            { label: 'Diesel', value: 'DIESEL' },
            { label: 'Electric', value: 'ELECTRIC' },
            { label: 'Hybrid', value: 'HYBRID' },
          ],
        },
        {
          name: 'description',
          type: 'richText',
          editor: lexicalEditor({}),
        },
        {
          name: 'status',
          type: 'select',
          defaultValue: 'AVAILABLE',
          options: [
            { label: 'Available', value: 'AVAILABLE' },
            { label: 'Pending', value: 'PENDING' },
            { label: 'Sold', value: 'SOLD' },
          ],
        },
        {
          name: 'photos',
          type: 'relationship',
          relationTo: 'media',
          hasMany: true,
        },
      ],
    },
    {
      slug: 'inquiries',
      admin: {
        useAsTitle: 'firstName',
      },
      access: {
        read: ({ req: { user } }) => Boolean(user),
        create: () => true,
        update: ({ req: { user } }) => Boolean(user),
        delete: ({ req: { user } }) => Boolean(user),
      },
      fields: [
        {
          name: 'firstName',
          type: 'text',
          required: true,
        },
        {
          name: 'lastName',
          type: 'text',
          required: true,
        },
        {
          name: 'email',
          type: 'email',
          required: true,
        },
        {
          name: 'phone',
          type: 'text',
        },
        {
          name: 'message',
          type: 'textarea',
        },
        {
          name: 'vehicle',
          type: 'relationship',
          relationTo: 'vehicles',
        },
        {
          name: 'preferredTime',
          type: 'text',
          label: 'Preferred Appointment Time',
        },
        {
          name: 'status',
          type: 'select',
          defaultValue: 'NEW',
          options: [
            { label: 'New', value: 'NEW' },
            { label: 'Reviewed', value: 'REVIEWED' },
            { label: 'Contacted', value: 'CONTACTED' },
          ],
        },
      ],
    },
    // -------------------------------------------------------------------------
    // Blog posts
    // -------------------------------------------------------------------------
    {
      slug: 'posts',
      admin: {
        useAsTitle: 'title',
      },
      versions: {
        drafts: true,
      },
      access: {
        read: () => true,
        create: ({ req: { user } }) => Boolean(user),
        update: ({ req: { user } }) => Boolean(user),
        delete: ({ req: { user } }) => Boolean(user),
      },
      fields: [
        {
          name: 'title',
          type: 'text',
          required: true,
        },
        {
          name: 'slug',
          type: 'text',
          required: true,
          unique: true,
          admin: {
            description: 'URL slug — auto-generated from title if left blank (e.g. "top-family-cars")',
          },
          hooks: {
            beforeValidate: [
              ({ value, data }: { value?: string | undefined; data?: Record<string, unknown> }) => {
                if (!value && data?.title) {
                  return (data.title as string)
                    .toLowerCase()
                    .replace(/[^a-z0-9]+/g, '-')
                    .replace(/(^-|-$)/g, '')
                }
                return value
              },
            ],
          },
        },
        {
          name: 'status',
          type: 'select',
          defaultValue: 'DRAFT',
          options: [
            { label: 'Draft', value: 'DRAFT' },
            { label: 'Published', value: 'PUBLISHED' },
          ],
        },
        {
          name: 'publishedAt',
          type: 'date',
          admin: {
            date: { pickerAppearance: 'dayOnly' },
            description: 'Date shown on the post. Defaults to today when published.',
          },
        },
        {
          name: 'coverImage',
          type: 'relationship',
          relationTo: 'media',
          label: 'Cover Image',
        },
        {
          name: 'excerpt',
          type: 'textarea',
          label: 'Excerpt / Summary',
          admin: {
            description: 'Short summary shown in cards and previews.',
          },
        },
        {
          name: 'readTime',
          type: 'text',
          label: 'Read Time',
          admin: {
            readOnly: true,
            description: 'Auto-calculated from body content — do not edit manually.',
          },
        },
        {
          name: 'author',
          type: 'text',
        },
        {
          name: 'body',
          type: 'richText',
          editor: lexicalEditor({}),
        },
      ],
      hooks: {
        beforeChange: [
          ({ data }: { data: Record<string, unknown> }) => {
            const body = data.body
            if (body && typeof body === 'object') {
              // Walk Lexical JSON to extract plain text for word count
              type LNode = { type?: string; text?: string; children?: LNode[] }
              function walk(node: LNode): string {
                if (node.type === 'text' && typeof node.text === 'string') return node.text
                if (Array.isArray(node.children)) return node.children.map(walk).join(' ')
                return ''
              }
              const root = (body as { root?: LNode }).root
              const text = root ? walk(root).replace(/\s+/g, ' ').trim() : ''
              const words = text ? text.split(/\s+/).filter(Boolean).length : 0
              const minutes = Math.max(1, Math.round(words / 200))
              data.readTime = `${minutes} min read`
            }
            return data
          },
        ],
      },
    },
    // -------------------------------------------------------------------------
    // Testimonials
    // -------------------------------------------------------------------------
    {
      slug: 'testimonials',
      admin: {
        useAsTitle: 'name',
      },
      access: {
        read: () => true,
        create: ({ req: { user } }) => Boolean(user),
        update: ({ req: { user } }) => Boolean(user),
        delete: ({ req: { user } }) => Boolean(user),
      },
      fields: [
        {
          name: 'name',
          type: 'text',
          required: true,
        },
        {
          name: 'quote',
          type: 'textarea',
          required: true,
        },
        {
          name: 'rating',
          type: 'number',
          min: 0,
          max: 5,
          admin: {
            description: 'e.g. 4.8',
          },
        },
        {
          name: 'photo',
          type: 'relationship',
          relationTo: 'media',
        },
        {
          name: 'vehiclePurchased',
          type: 'text',
          label: 'Vehicle Purchased (optional)',
          admin: {
            description: 'e.g. "2019 Toyota Camry" — shown under the reviewer name',
          },
        },
      ],
    },
  ],
  // ---------------------------------------------------------------------------
  // Globals — singleton documents for site-wide settings
  // ---------------------------------------------------------------------------
  globals: [
    {
      slug: 'siteSettings',
      label: 'Site Settings',
      versions: {
        drafts: true,
      },
      hooks: {
        afterChange: [
          async () => {
            const { revalidateTag } = await import('next/cache')
            revalidateTag('site-settings')
          },
        ],
      },
      fields: [
        {
          name: 'businessName',
          type: 'text',
          defaultValue: 'Reliable Used Cars',
        },
        {
          name: 'phone',
          type: 'text',
          label: 'Phone Number',
          admin: { description: 'Displayed in the top banner, navbar, and footer. e.g. (555) 000-0000' },
        },
        {
          name: 'email',
          type: 'email',
          label: 'Contact Email',
        },
        {
          name: 'address',
          type: 'text',
          label: 'Full Address',
          admin: { description: 'Shown in footer and contact page. e.g. 123 Main St, City, ST 00000' },
        },
        {
          name: 'city',
          type: 'text',
          label: 'City / Region (short)',
          admin: { description: 'Shown in top banner. e.g. Miami, FL' },
        },
        {
          name: 'bannerLabel',
          type: 'text',
          label: 'Top Banner Label (bold)',
          admin: { description: 'The bold colored word. Default: "Cash Only"' },
        },
        {
          name: 'bannerText',
          type: 'text',
          label: 'Top Banner Message',
          admin: { description: 'Text after the label. Default: "Quality pre-owned vehicles at honest prices"' },
        },
        {
          name: 'primaryColor',
          type: 'text',
          label: 'Primary Color (hex)',
          admin: { description: 'Brand accent color used across buttons and highlights. e.g. #F76C45' },
        },
        {
          name: 'primaryColorDark',
          type: 'text',
          label: 'Primary Color Dark (hex)',
          admin: { description: 'Hover variant of the primary color. e.g. #e55a33' },
        },
      ],
    },
    {
      slug: 'heroContent',
      label: 'Hero Section',
      versions: {
        drafts: true,
      },
      hooks: {
        afterChange: [
          async () => {
            const { revalidateTag } = await import('next/cache')
            revalidateTag('hero-content')
          },
        ],
      },
      fields: [
        {
          name: 'watermark',
          type: 'text',
          label: 'Background Watermark Text',
          admin: { description: 'The large text behind the car. Default: "RELIABLE"' },
        },
        {
          name: 'headline',
          type: 'text',
          label: 'Headline (bottom right, large font)',
          admin: { description: 'Default: "Find the perfect car that fits your journey"' },
        },
        {
          name: 'subheadline',
          type: 'textarea',
          label: 'Subheadline (bottom left paragraph)',
        },
        {
          name: 'ctaLabel',
          type: 'text',
          label: 'CTA Button Label',
          admin: { description: 'Default: "Shop Now"' },
        },
        {
          name: 'ctaHref',
          type: 'text',
          label: 'CTA Button Link',
          admin: { description: 'Default: "/shop"' },
        },
        {
          name: 'bgImage',
          type: 'relationship',
          relationTo: 'media',
          label: 'Background Image',
        },
        {
          name: 'showCarImage',
          type: 'checkbox',
          label: 'Show Hero Car Image',
          defaultValue: true,
          admin: { description: 'Toggle the car image overlay on or off.' },
        },
        {
          name: 'carImage',
          type: 'relationship',
          label: 'Hero Car Image',
          relationTo: 'media',
          admin: {
            condition: (_, siblingData) => siblingData?.showCarImage !== false,
            description: 'Choose the car image shown in the hero overlay.',
          },
        },
      ],
    },
    {
      slug: 'contactPage',
      label: 'Contact Page',
      versions: {
        drafts: true,
      },
      hooks: {
        afterChange: [
          async () => {
            const { revalidateTag } = await import('next/cache')
            revalidateTag('contact-page')
          },
        ],
      },
      fields: [
        {
          name: 'tagline',
          type: 'text',
          label: 'Tagline (above heading)',
          admin: { description: 'Small colored text above the title. Default: "Get in Touch"' },
        },
        {
          name: 'heading',
          type: 'text',
          label: 'Page Heading',
          admin: { description: 'Default: "Contact Us"' },
        },
        {
          name: 'description',
          type: 'richText',
          label: 'Page Description',
          editor: lexicalEditor({}),
          admin: { description: 'Paragraph below the heading. Supports bold, italic, links, etc.' },
        },
        {
          name: 'appointmentNote',
          type: 'richText',
          label: 'Appointment Notice',
          editor: lexicalEditor({}),
          admin: { description: 'The note inside the box below the hours table. Supports formatting.' },
        },
        {
          name: 'formHeading',
          type: 'text',
          label: 'Form Section Heading',
          admin: { description: 'Default: "Request an Appointment"' },
        },
        {
          name: 'mondayFriday',
          type: 'text',
          label: 'Monday–Friday Hours',
          admin: { description: 'Default: "10:00 AM – 6:00 PM"' },
        },
        {
          name: 'saturday',
          type: 'text',
          label: 'Saturday Hours',
          admin: { description: 'Default: "10:00 AM – 4:00 PM"' },
        },
        {
          name: 'sunday',
          type: 'text',
          label: 'Sunday Hours',
          admin: { description: 'Default: "Closed"' },
        },
      ],
    },
  ],
  db: postgresAdapter({
    pool: {
      connectionString: process.env.DATABASE_URL,
    },
    push: true,
  }),
  editor: lexicalEditor({}),
  secret: process.env.PAYLOAD_SECRET || '',
  csrf: [
    'https://drivereliablecars.com',
    'http://drivereliablecars.com',
    'http://localhost:3000',
  ],
  typescript: {
    outputFile: path.resolve(dirname, 'payload-types.ts'),
  },
  serverURL: process.env.NEXT_PUBLIC_SERVER_URL || '',
})
