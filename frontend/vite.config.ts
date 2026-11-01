import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開發時把 /api 轉給 FastAPI（python -m carearm.api.app）
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
