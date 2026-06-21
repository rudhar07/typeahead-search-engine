import type { NextConfig } from "next";

// The FastAPI backend. Override with BACKEND_URL if you run it elsewhere.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // WHY rewrites: the browser calls "/api/..." on the SAME origin as the page,
  // and Next forwards (proxies) it to the FastAPI backend. This avoids CORS
  // entirely — the browser never makes a cross-origin request (the alternative
  // would be enabling CORS middleware on the backend).
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
