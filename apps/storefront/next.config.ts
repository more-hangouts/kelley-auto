import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // Staging and preview builds use a real, relative NEXT_DIST_DIR. Production
  // intentionally leaves this unset and serves the real `.next` directory:
  // Next 15.5 returns MODULE_NOT_FOUND for request-time chunks when distDir is
  // a symlink, even when that symlink targets a valid build.
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
