import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api/* → FastAPI on :8000 (strip the /api prefix)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
