import type { NextConfig } from 'next'
import { withPayload } from '@payloadcms/next/withPayload'

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      // Payload-hosted content media (blog/page images), served from the
      // app's own origin.
      {
        protocol: 'https',
        hostname: 'www.kelleyautoplex.com',
        pathname: '/api/media/file/**',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '3000',
        pathname: '/media/**',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '3000',
        pathname: '/api/media/file/**',
      },
      // TODO(Day 5): vehicle photos come from the FastAPI public inventory
      // (public_vehicle_dto.photos) as absolute URLs. Add the real
      // production photo CDN host(s) here once known, e.g.:
      //   { protocol: 'https', hostname: '<cdn-host>', pathname: '/**' }
      // Until then, off-host vehicle images won't optimize through
      // next/image (add the host, or set `unoptimized` on those <Image>s).
    ],
  },
}

export default withPayload(nextConfig)
