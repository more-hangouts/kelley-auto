import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      // Vehicle photos are served by the FastAPI backend at
      // /api/public/media/vehicles/... as absolute URLs (built from
      // PUBLIC_API_BASE_URL). Allow the API origins so next/image can
      // optimize them. Dev: 127.0.0.1/localhost:8000. Prod: the API host.
      {
        protocol: 'http',
        hostname: '127.0.0.1',
        port: '8000',
        pathname: '/api/public/media/**',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '8000',
        pathname: '/api/public/media/**',
      },
      {
        protocol: 'https',
        hostname: 'api.kelleyautoplex.com',
        pathname: '/api/public/media/**',
      },
    ],
  },
}

export default nextConfig
