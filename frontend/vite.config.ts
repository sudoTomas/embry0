import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 3001,
    proxy: {
      "/api": {
        target: process.env.API_URL || "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: process.env.API_URL || "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
      "/health": {
        target: process.env.API_URL || "http://localhost:8000",
        changeOrigin: true,
      },
      "/agent": {
        target: process.env.AGENT_URL || "http://localhost:7778",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/agent/, ""),
      },
    },
  },
});
