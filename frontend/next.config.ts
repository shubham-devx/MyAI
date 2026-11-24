import type { NextConfig } from 'next'
const apiProxyTarget = process.env.API_PROXY_TARGET?.replace(/\/+$/, '')
if (process.env.VERCEL && !apiProxyTarget) {
  throw new Error('Set API_PROXY_TARGET to the FastAPI API origin in Vercel project environment variables.')
}

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    return apiProxyTarget ? [{ source: '/backend-api/:path*', destination: `${apiProxyTarget}/:path*` }] : []
  },
  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'Permissions-Policy', value: 'camera=(), geolocation=(), microphone=(self)' },
      ],
    }]
  },
}
export default nextConfig
