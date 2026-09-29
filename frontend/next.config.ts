import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Keep SSE responses unbuffered when they pass through the local /api proxy.
  compress: false,
};

export default nextConfig;
