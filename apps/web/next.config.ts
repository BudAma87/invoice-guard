import type { NextConfig } from "next";

const API = process.env.API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  // Same-origin proxy to the FastAPI review API: no CORS, cookies/headers stay first-party.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/:path*` },
      { source: "/files/:path*", destination: `${API}/files/:path*` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default config;
