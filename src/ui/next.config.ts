import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Hide the Next.js dev mode indicator/devtools button so it doesn't
  // overlap the chat input box at the bottom of the page.
  devIndicators: false,
};

export default nextConfig;
