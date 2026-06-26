import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      // Vehicle photos come from the FastAPI public inventory
      // (public_vehicle_dto.photos) as absolute URLs. Add the real
      // production photo CDN host(s) here once known, e.g.:
      //   { protocol: 'https', hostname: '<cdn-host>', pathname: '/**' }
      // Until then, off-host vehicle images won't optimize through
      // next/image (add the host, or set `unoptimized` on those <Image>s).
    ],
  },
}

export default nextConfig
