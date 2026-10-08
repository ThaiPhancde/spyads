/** @type {import('next').NextConfig} */
const API = process.env.API_URL || "http://127.0.0.1:8000";
const exportMode = process.env.EXPORT === "1";

// EXPORT=1 → build tĩnh (out/) để FastAPI phục vụ cùng origin — chỉ cần MỘT service ở bất kỳ nhà cung cấp nào.
// Dev (npm run dev) → proxy /api sang backend :8000.
module.exports = exportMode
  // pageExtensions without "ts": drops app/api/events/stream/route.ts (a server route cannot be exported; FastAPI
  // serves /api/events/stream on the same origin in this mode). All pages/layouts are .tsx so nothing else changes.
  ? { output: "export", trailingSlash: true, images: { unoptimized: true }, pageExtensions: ["tsx", "jsx"] }
  : {
      trailingSlash: true,
      skipTrailingSlashRedirect: true,
      experimental: { proxyTimeout: 120_000 }, // default 30 s → a slow API call surfaced as "500"
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
      },
    };
