import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // Staged releases build into a versioned directory and the running service
  // is pointed at it via NEXT_DIST_DIR (set to `.next-current`, a symlink to
  // the active release). Defaults to `.next` for local dev and any process
  // that does not set it. Build and `next start` must see the SAME value.
  distDir: process.env.NEXT_DIST_DIR || '.next',
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
