/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    CLAW_API_URL: process.env.CLAW_API_URL || 'http://localhost:8765',
    CLAW_API_KEY: process.env.CLAW_API_KEY || 'claw-dev-key-change-in-production',
  },
}

module.exports = nextConfig
