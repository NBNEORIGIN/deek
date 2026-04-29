/**
 * Brand name shown to users.
 *
 * Build-time injected via NEXT_PUBLIC_DEEK_BRAND_NAME. Defaults to
 * "Deek" so the Hetzner production deploy is unchanged. Pass
 *   --build-arg NEXT_PUBLIC_DEEK_BRAND_NAME=Rex
 * when building jo-pip-web so Jo's PWA shows "Rex" in every place
 * that previously hardcoded "Deek".
 *
 * Next.js inlines NEXT_PUBLIC_* values into the bundle at build time,
 * so this constant works in both server and client components.
 */
export const BRAND = process.env.NEXT_PUBLIC_DEEK_BRAND_NAME || 'Deek'
