/**
 * Build modes:
 *  - Default (self-host / Docker / Fargate): `output: standalone`
 *  - Vercel (VERCEL=1): Vercel's own adapter (no explicit output)
 *  - GitHub Pages static demo (NEXT_PUBLIC_STATIC_DEMO=true): `output: export`
 *
 * The static-demo mode emits a fully static `out/` folder for GitHub Pages.
 * Middleware, API routes, and header rules do NOT run in that mode — see
 * DEPLOY_GITHUB_PAGES.md for what works and what doesn't.
 */
const isStaticDemo = process.env.NEXT_PUBLIC_STATIC_DEMO === "true";
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  output: isStaticDemo ? "export" : process.env.VERCEL ? undefined : "standalone",

  // Project-repo Pages sites live under /<repo>, so assets and internal links
  // must be prefixed. Left empty for root deployments (Vercel/custom domain).
  basePath: isStaticDemo && basePath ? basePath : undefined,
  assetPrefix: isStaticDemo && basePath ? basePath : undefined,

  // Static hosts serve /about as /about/index.html; trailing slash makes that work.
  trailingSlash: isStaticDemo ? true : undefined,

  images: {
    // next/image has no optimization server on a static host.
    unoptimized: isStaticDemo,
    remotePatterns: [
      { protocol: "https", hostname: "cdn.soakingarri.com" },
      { protocol: "https", hostname: "*.soakingarri.com" },
    ],
  },

  // Header rules require a server; skip them (and their warnings) in static mode.
  ...(isStaticDemo
    ? {}
    : {
        async headers() {
          return [
            {
              source: "/:path*",
              headers: [
                { key: "X-Frame-Options", value: "SAMEORIGIN" },
                { key: "X-Content-Type-Options", value: "nosniff" },
                { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
              ],
            },
          ];
        },
      }),
};

module.exports = nextConfig;
