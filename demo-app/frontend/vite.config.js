import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The page talks to the demo backend through /api. Backend port comes from BACKEND_PORT (default 3100),
// so a port change made by DevOS on the backend only needs this one variable to follow it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174, // 5173 belongs to the DevOS UI
    proxy: { "/api": `http://localhost:${process.env.BACKEND_PORT ?? 3100}` },
  },
});
