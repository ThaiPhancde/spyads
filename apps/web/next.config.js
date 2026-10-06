/** @type {import('next').NextConfig} */
const API = process.env.API_URL || "http://127.0.0.1:8000";
const exportMode = process.env.EXPORT === "1";

// EXPORT=1 → build tĩnh (out/) để FastAPI phục vụ cùng origin — chỉ cần MỘT service ở bất kỳ nhà cung cấp nào.
// Dev (npm run dev) → proxy /api sang backend :8000.
module.exports = exportMode
  ? { output: "export", trailingSlash: true, images: { unoptimized: true } }
  : {
      trailingSlash: true,
      skipTrailingSlashRedirect: true,
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
      },
    };
